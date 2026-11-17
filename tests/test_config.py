import pytest

from app.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_NAME", "DEBUG", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "SimilarMusic"
    assert settings.debug is False
    assert settings.database_url == "sqlite:///./similarmusic.db"


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Test App")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./other.db")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Test App"
    assert settings.debug is True
    assert settings.database_url == "sqlite:///./other.db"
