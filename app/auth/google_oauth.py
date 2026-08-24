import base64
import hashlib
import secrets

from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.auth.session import clear_session_cookie, create_session_cookie
from app.config import settings
from app.db.crud import upsert_user
from app.db.session import get_session

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
]

OAUTH_STATE_COOKIE = "oauth_state"
OAUTH_CODE_VERIFIER_COOKIE = "oauth_code_verifier"

_fernet = Fernet(settings.FERNET_SECRET_KEY.encode() if isinstance(settings.FERNET_SECRET_KEY, str) else settings.FERNET_SECRET_KEY)

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=settings.GOOGLE_REDIRECT_URI)


def encrypt_refresh_token(refresh_token: str) -> str:
    return _fernet.encrypt(refresh_token.encode()).decode()


def decrypt_refresh_token(encrypted_refresh_token: str) -> str:
    return _fernet.decrypt(encrypted_refresh_token.encode()).decode()


def _generate_pkce_pair() -> tuple[str, str]:
    # Google now requires PKCE on the authorization code flow for newer OAuth clients.
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


@router.get("/login")
def login() -> RedirectResponse:
    flow = _build_flow()
    code_verifier, code_challenge = _generate_pkce_pair()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    response = RedirectResponse(authorization_url)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )
    response.set_cookie(
        key=OAUTH_CODE_VERIFIER_COOKIE,
        value=code_verifier,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )
    return response


@router.get("/callback")
def callback(request: Request) -> RedirectResponse:
    state = request.query_params.get("state")
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not state or not cookie_state or state != cookie_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    code_verifier = request.cookies.get(OAUTH_CODE_VERIFIER_COOKIE)
    if not code_verifier:
        raise HTTPException(
            status_code=400, detail="Missing PKCE code verifier cookie; please restart sign-in"
        )

    flow = _build_flow()
    flow.fetch_token(authorization_response=str(request.url), code_verifier=code_verifier)
    credentials = flow.credentials

    if not credentials.refresh_token:
        raise HTTPException(
            status_code=400,
            detail="Google did not return a refresh token. Revoke app access at "
            "https://myaccount.google.com/permissions and try signing in again.",
        )

    claims = id_token.verify_oauth2_token(
        credentials.id_token, GoogleAuthRequest(), settings.GOOGLE_CLIENT_ID
    )

    db = get_session()
    try:
        user = upsert_user(
            db,
            google_sub=claims["sub"],
            email=claims["email"],
            name=claims.get("name"),
            encrypted_refresh_token=encrypt_refresh_token(credentials.refresh_token),
        )
    finally:
        db.close()

    from app.auth.session import create_session_token
    session_token = create_session_token(user.id)
    redirect_target = f"{settings.STREAMLIT_URL.rstrip('/')}/?session={session_token}"
    response = RedirectResponse(url=redirect_target)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.delete_cookie(OAUTH_CODE_VERIFIER_COOKIE)
    create_session_cookie(response, user.id)
    return response


@router.get("/logout")
def logout() -> RedirectResponse:
    redirect_target = f"{settings.STREAMLIT_URL.rstrip('/')}/?logout=true"
    response = RedirectResponse(url=redirect_target)
    clear_session_cookie(response)
    return response
