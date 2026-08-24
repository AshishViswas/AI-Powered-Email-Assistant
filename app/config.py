import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

# Streamlit Cloud secrets integration
try:
    import streamlit as st
    if hasattr(st, "secrets"):
        for key, val in st.secrets.items():
            if isinstance(val, (str, int, float, bool)) and not os.environ.get(str(key)):
                os.environ[str(key)] = str(val)
except Exception:
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    OPENAI_API_KEY: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:7860/auth/callback"
    FASTAPI_BACKEND_URL: str = "http://localhost:7860"
    FERNET_SECRET_KEY: str = ""
    SESSION_SECRET: str = "default_secret_key"
    DATABASE_URL: str = "sqlite:///./gmail.db"
    STREAMLIT_URL: str = "http://localhost:8501"
    SYNC_INTERVAL_SECONDS: int = 600  # background Gmail poll interval
    UI_REFRESH_SECONDS: int = 20  # how often the open browser tab re-reads the DB
    ENV: str = "development"

    @property
    def fastapi_backend_url(self) -> str:
        if self.FASTAPI_BACKEND_URL:
            return self.FASTAPI_BACKEND_URL.rstrip("/")
        if self.GOOGLE_REDIRECT_URI and "/auth/callback" in self.GOOGLE_REDIRECT_URI:
            return self.GOOGLE_REDIRECT_URI.rsplit("/auth/callback", 1)[0]
        return "http://localhost:7860"


settings = Settings()

if settings.OPENAI_API_KEY and not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY