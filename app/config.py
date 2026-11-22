"""Application settings loaded from environment variables and an optional .env file."""

from functools import lru_cache
from pathlib import Path

from typing import Literal

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

    # AI ranking and explanations (Claude API).
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"
    ai_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    # One retry is allowed, so two attempts plus discovery must stay under
    # the browser's 5-minute wait (static/js/app.js).
    ai_timeout_seconds: float = Field(default=120.0, gt=0, le=130)

    # Recommendations.
    recommendation_count: int = Field(default=10, ge=1, le=25)
    recommendation_cache_hours: float = Field(default=24.0, ge=0)
    # Recommendation requests one client (IP address) may make per hour; 0 = unlimited.
    recommendations_per_client_per_hour: int = Field(default=30, ge=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
