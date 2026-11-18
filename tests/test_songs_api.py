from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_metadata_providers
from app.errors import (
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    RateLimitedError,
    SongNotFoundError,
)
from app.main import app
from app.services.link_parser import Platform
from tests.fakes import SPOTIFY_ID, YOUTUBE_ID, FakeProvider, spotify_song

LOOKUP = "/api/songs/lookup"


@pytest.fixture
def providers() -> Iterator[dict[Platform, FakeProvider]]:
    fakes = {
        Platform.SPOTIFY: FakeProvider(Platform.SPOTIFY, spotify_song()),
        Platform.YOUTUBE: FakeProvider(Platform.YOUTUBE, error=SongNotFoundError("YouTube couldn't find that video.")),
    }
    app.dependency_overrides[get_metadata_providers] = lambda: list(fakes.values())
    yield fakes
    app.dependency_overrides.pop(get_metadata_providers, None)


def test_lookup_returns_song(client: TestClient, providers: dict[Platform, FakeProvider]) -> None:
    response = client.post(LOOKUP, json={"url": f"https://open.spotify.com/track/{SPOTIFY_ID}"})

    assert response.status_code == 200
    assert response.json() == {
        "platform": "spotify",
        "external_id": SPOTIFY_ID,
        "title": "Midnight City",
        "artist": "M83",
        "album": "Hurry Up, We're Dreaming",
        "artwork_url": "https://i.scdn.co/image/large",
        "url": f"https://open.spotify.com/track/{SPOTIFY_ID}",
        "attributes": {"artists": ["M83"], "release_date": "2011-10-18"},
    }


@pytest.mark.parametrize(
    ("url", "status", "code"),
    [
        ("not a link", 400, "invalid_url"),
        ("https://open.spotify.com/track/bad", 400, "invalid_url"),
        ("https://soundcloud.com/a/b", 400, "unsupported_platform"),
        (f"https://open.spotify.com/album/{SPOTIFY_ID}", 400, "unsupported_platform"),
        (f"https://youtu.be/{YOUTUBE_ID}", 404, "song_not_found"),
    ],
)
def test_lookup_errors(
    client: TestClient, providers: dict[Platform, FakeProvider], url: str, status: int, code: str
) -> None:
    response = client.post(LOOKUP, json={"url": url})

    assert response.status_code == status
    body = response.json()
    assert body["error"] == code
    assert body["message"]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ProviderNotConfiguredError("no key"), 503, "provider_not_configured"),
        (ProviderUnavailableError("down"), 502, "provider_unavailable"),
        (RateLimitedError("slow down"), 503, "rate_limited"),
    ],
)
def test_provider_failures_map_to_status(
    client: TestClient,
    providers: dict[Platform, FakeProvider],
    error: Exception,
    status: int,
    code: str,
) -> None:
    providers[Platform.SPOTIFY].error = error

    response = client.post(LOOKUP, json={"url": f"spotify:track:{SPOTIFY_ID}"})

    assert response.status_code == status
    assert response.json() == {"error": code, "message": str(error)}


def test_rate_limit_sets_retry_after(client: TestClient, providers: dict[Platform, FakeProvider]) -> None:
    providers[Platform.SPOTIFY].error = RateLimitedError("slow down", retry_after=30)

    response = client.post(LOOKUP, json={"url": f"spotify:track:{SPOTIFY_ID}"})

    assert response.headers["Retry-After"] == "30"


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "x" * 2049}, {"url": 5}])
def test_lookup_rejects_malformed_body(
    client: TestClient, providers: dict[Platform, FakeProvider], payload: dict
) -> None:
    assert client.post(LOOKUP, json=payload).status_code == 422


def test_lookup_without_credentials_reports_not_configured(client: TestClient) -> None:
    # Real providers are used here; tests run without API credentials configured.
    response = client.post(LOOKUP, json={"url": f"https://youtu.be/{YOUTUBE_ID}"})

    assert response.status_code == 503
    assert response.json()["error"] == "provider_not_configured"
