"""Application configuration loaded from environment / .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Central settings. Every value can be overridden in `.env`."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Decide Well"
    debug: bool = False

    # Security
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    jwt_algorithm: str = "HS256"

    # Database
    database_url: str = f"sqlite:///{BASE_DIR / 'decidewell.db'}"

    # Gemini (LLM insights)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    gemini_timeout_seconds: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
