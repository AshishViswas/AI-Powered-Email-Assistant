from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days

_serializer = URLSafeTimedSerializer(settings.SESSION_SECRET, salt="session-cookie")


def create_session_token(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def get_user_id_from_token(token: str) -> int | None:
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
        return data.get("user_id")
    except BadSignature:
        return None


def create_session_cookie(response: Response, user_id: int) -> None:
    token = create_session_token(user_id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.ENV == "production",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


def get_user_id_from_request(request: Request) -> int | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return get_user_id_from_token(token) if token else None

