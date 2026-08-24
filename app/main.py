import logging
import os
from contextlib import asynccontextmanager

from agents import set_default_openai_key
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse

from app.api import api_router
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

origins = [
    settings.STREAMLIT_URL.rstrip("/"),
    "http://localhost:8501",
    "http://127.0.0.1:8501",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(api_router)


@app.get("/")
def read_root():
    """Redirect root requests to Streamlit web UI."""
    return RedirectResponse(url=settings.STREAMLIT_URL)
