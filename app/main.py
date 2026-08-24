import logging
import os
from contextlib import asynccontextmanager

from agents import set_default_openai_key
from fastapi import FastAPI
from starlette.responses import RedirectResponse

from app.auth.google_oauth import router as auth_router
from app.config import settings
from app.db.session import init_db
from app.scheduler import start_scheduler

if os.getenv("ENV", "development") != "production":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.OPENAI_API_KEY:
        set_default_openai_key(settings.OPENAI_API_KEY)
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(title="Gmail Agent Backend", lifespan=lifespan)
app.include_router(auth_router)


@app.get("/")
def read_root():
    """Redirect root requests to Streamlit web UI."""
    return RedirectResponse(url=settings.STREAMLIT_URL)
