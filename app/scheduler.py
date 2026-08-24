import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app.agents.summarizer_agent import summarize_emails
from app.config import settings
from app.db.crud import create_action_items, create_email_summary, create_email_triage, get_all_users
from app.db.models import ActionItem, EmailSummary, EmailTriage, User
from app.db.session import get_session
from app.gmail.client import fetch_new_messages

logger = logging.getLogger("app.scheduler")

SCHEDULER_JOB_ID = "sync_all_users"


def run_sync_for_user(db: Session, user: User) -> tuple[EmailSummary, list[ActionItem], list[EmailTriage]] | None:
    """Fetch + summarize + persist for one user.

    Shared by the manual "Sync now" button and the scheduled job so the two
    paths can never drift apart.
    """
    emails = fetch_new_messages(db, user)
    if not emails:
        return None

    summary_output = summarize_emails(emails)
    sender_by_message_id = {email["message_id"]: email["sender"] for email in emails}
    subject_by_message_id = {email["message_id"]: email["subject"] for email in emails}
    date_by_message_id = {email["message_id"]: email.get("date") for email in emails}

    summary_row = create_email_summary(
        db,
        user_id=user.id,
        summary_text=summary_output.summary,
        source_message_ids=[email["message_id"] for email in emails],
    )
    action_item_rows = create_action_items(
        db,
        user_id=user.id,
        summary_id=summary_row.id,
        items=[
            {**item.model_dump(), "source_sender": sender_by_message_id.get(item.source_message_id)}
            for item in summary_output.action_items
        ],
    )
    triage_rows = create_email_triage(
        db,
        user_id=user.id,
        summary_id=summary_row.id,
        items=[
            {
                **item.model_dump(),
                "sender": sender_by_message_id.get(item.message_id),
                "subject": subject_by_message_id.get(item.message_id),
            }
            for item in summary_output.triage
        ],
        email_dates=date_by_message_id,
    )
    return summary_row, action_item_rows, triage_rows


def run_sync_for_all_users() -> None:
    db = get_session()
    try:
        users = get_all_users(db)
    finally:
        db.close()

    for user in users:
        db = get_session()
        try:
            result = run_sync_for_user(db, user)
            if result is None:
                logger.info("sync: no new messages user_id=%s email=%s", user.id, user.email)
            else:
                summary_row, action_item_rows, triage_rows = result
                logger.info(
                    "sync: succeeded user_id=%s email=%s summary_id=%s action_items=%d triaged=%d",
                    user.id,
                    user.email,
                    summary_row.id,
                    len(action_item_rows),
                    len(triage_rows),
                )
        except Exception:
            logger.exception("sync: failed user_id=%s email=%s", user.id, user.email)
        finally:
            db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_sync_for_all_users,
        "interval",
        seconds=settings.SYNC_INTERVAL_SECONDS,
        id=SCHEDULER_JOB_ID,
    )
    scheduler.start()
    logger.info("scheduler started: syncing every %s seconds", settings.SYNC_INTERVAL_SECONDS)
    return scheduler
