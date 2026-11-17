"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SimilarMusic"
    debug: bool = False
    database_url: str = "sqlite:///./similarmusic.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
