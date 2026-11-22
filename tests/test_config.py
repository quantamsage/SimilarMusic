import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_NAME",
        "DEBUG",
        "DATABASE_URL",
        "HTTP_TIMEOUT_SECONDS",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_MARKET",
        "YOUTUBE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "SimilarMusic"
    assert settings.debug is False
    assert settings.database_url == "sqlite:///./similarmusic.db"
    assert settings.http_timeout_seconds == 10.0
    assert settings.spotify_client_id is None
    assert settings.spotify_client_secret is None
    assert settings.spotify_market == "US"
    assert settings.youtube_api_key is None


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test App")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./other.db")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Test App"
    assert settings.debug is True
    assert settings.database_url == "sqlite:///./other.db"


@pytest.mark.parametrize(
    ("name", "value"),
    [("SPOTIFY_MARKET", "usa"), ("HTTP_TIMEOUT_SECONDS", "0"), ("HTTP_TIMEOUT_SECONDS", "120"), ("AI_TIMEOUT_SECONDS", "131"), ("AI_EFFORT", "extreme")],
)
def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
