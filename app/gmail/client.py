import base64
from datetime import datetime, timezone
from email.mime.text import MIMEText

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.auth.google_oauth import SCOPES, decrypt_refresh_token
from app.config import settings
from app.db.crud import get_or_create_sync_state, update_sync_state
from app.db.models import User
from app.gmail.parser import parse_message

# No prior history: bound the first sync to a reasonable lookback instead of
# pulling a user's entire mailbox.
INITIAL_LOOKBACK_QUERY = "newer_than:1d"


def get_credentials(user: User) -> Credentials:
    refresh_token = decrypt_refresh_token(user.encrypted_refresh_token)
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    credentials.refresh(GoogleAuthRequest())
    return credentials


def build_gmail_service(user: User):
    return build("gmail", "v1", credentials=get_credentials(user), cache_discovery=False)


def _list_message_ids_since_history(service, history_id: str) -> list[str] | None:
    """Returns None if the historyId has expired (Gmail keeps ~1 week of history)."""
    message_ids: list[str] = []
    page_token = None
    try:
        while True:
            response = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                )
                .execute()
            )
            for record in response.get("history", []):
                for added in record.get("messagesAdded", []):
                    message_ids.append(added["message"]["id"])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except HttpError as exc:
        if exc.resp.status == 404:
            return None
        raise
    return message_ids


def _list_message_ids_by_query(service, query: str) -> list[str]:
    message_ids: list[str] = []
    page_token = None
    while True:
        response = (
            service.users().messages().list(userId="me", q=query, pageToken=page_token).execute()
        )
        message_ids.extend(msg["id"] for msg in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return message_ids


def fetch_new_messages(db: Session, user: User, max_batch: int = 25) -> list[dict]:
    service = build_gmail_service(user)
    sync_state = get_or_create_sync_state(db, user.id)

    # Fetch recent message IDs from Gmail (newer_than:7d to guarantee recent emails are found)
    recent_ids = _list_message_ids_by_query(service, "newer_than:7d")

    # Determine which messages are already in DB
    from app.db.models import EmailTriage
    existing_ids = set(
        row[0] for row in db.query(EmailTriage.message_id).filter(EmailTriage.user_id == user.id).all()
    )

    # Filter out already triaged messages
    untriaged_ids = [mid for mid in recent_ids if mid not in existing_ids][:max_batch]

    messages = []
    for message_id in untriaged_ids:
        try:
            raw = service.users().messages().get(userId="me", id=message_id, format="full").execute()
            messages.append(parse_message(raw))
        except Exception:
            continue

    profile = service.users().getProfile(userId="me").execute()
    update_sync_state(
        db,
        sync_state,
        last_history_id=profile.get("historyId"),
        last_synced_at=datetime.now(timezone.utc),
    )

    return messages


def get_message(user: User, message_id: str) -> dict:
    """Fetches and parses a single message by id (e.g. to build a reply —
    action items only store the source message id, not its content)."""
    service = build_gmail_service(user)
    raw = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return parse_message(raw)


def send_email(user: User, *, to_addr: str, subject: str, body: str) -> str:
    """Actually sends via the Gmail API. Only ever call this from a
    human-triggered action (e.g. the UI's "Send" button handler) — never give
    an LLM agent a tool that reaches this function, per the project's
    non-negotiable safety rule that sending is always a separate human step.
    """
    service = build_gmail_service(user)
    message = MIMEText(body)
    message["to"] = to_addr
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"]
