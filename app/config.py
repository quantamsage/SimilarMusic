"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SimilarMusic"
    debug: bool = False
    database_url: str = "sqlite:///./similarmusic.db"

    # Timeout applied to every external API request.
    http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    # Spotify Web API (Client Credentials flow).
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    spotify_market: str = Field(default="US", pattern=r"^[A-Z]{2}$")

    # YouTube Data API v3.
    youtube_api_key: str | None = None

    # Last.fm API (similar tracks, similar artists and tags).
    lastfm_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
