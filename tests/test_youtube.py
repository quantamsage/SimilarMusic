from collections.abc import Callable

import httpx
import pytest

from app.errors import (
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    RateLimitedError,
    SongNotFoundError,
)
from app.integrations.youtube import YouTubeClient, split_artist_and_title
from app.services.link_parser import Platform

VIDEO_ID = "dQw4w9WgXcQ"


def video_payload(**snippet_overrides: object) -> dict:
    snippet = {
        "title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
        "channelTitle": "Rick Astley",
        "publishedAt": "2009-10-25T06:57:33Z",
        "categoryId": "10",
        "tags": ["rick astley", "pop"],
        "defaultAudioLanguage": "en",
        "thumbnails": {
            "default": {"url": "https://i.ytimg.com/vi/x/default.jpg", "width": 120},
            "high": {"url": "https://i.ytimg.com/vi/x/hqdefault.jpg", "width": 480},
            "medium": {"url": "https://i.ytimg.com/vi/x/mqdefault.jpg", "width": 320},
        },
    }
    snippet.update(snippet_overrides)
    return {"items": [{"id": VIDEO_ID, "snippet": snippet, "contentDetails": {"duration": "PT3M33S"}}]}


def make_client(handler: Callable[[httpx.Request], httpx.Response], api_key: str | None = "key") -> YouTubeClient:
    return YouTubeClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), api_key)


def api_error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "errors": [{"reason": reason}]}})


# The response the live API gave for an invalid key (captured 2026-11-23):
# the precise reason is only in `details`.
INVALID_KEY_RESPONSE = httpx.Response(
    400,
    json={
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "errors": [{"message": "API key not valid. Please pass a valid API key.", "domain": "global", "reason": "badRequest"}],
            "status": "INVALID_ARGUMENT",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "API_KEY_INVALID",
                    "domain": "googleapis.com",
                    "metadata": {"service": "youtube.googleapis.com"},
                },
                {"@type": "type.googleapis.com/google.rpc.LocalizedMessage", "locale": "en-US", "message": "API key not valid."},
            ],
        }
    },
)


@pytest.mark.anyio
async def test_get_song_maps_video_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=video_payload())

    song = await make_client(handler).get_song(VIDEO_ID)

    assert song.platform is Platform.YOUTUBE
    assert song.external_id == VIDEO_ID
    assert song.artist == "Rick Astley"
    assert song.title == "Never Gonna Give You Up"
    assert song.album is None
    assert song.artwork_url == "https://i.ytimg.com/vi/x/hqdefault.jpg"
    assert song.url == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert song.attributes == {
        "raw_title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
        "channel_title": "Rick Astley",
        "is_music_category": True,
        "tags": ["rick astley", "pop"],
        "language": "en",
        "published_at": "2009-10-25T06:57:33Z",
        "duration_ms": 213000,
    }

    params = requests[0].url.params
    assert requests[0].url.host == "www.googleapis.com"
    assert params["id"] == VIDEO_ID
    assert params["part"] == "snippet,contentDetails"
    assert params["key"] == "key"


@pytest.mark.anyio
async def test_optional_fields_are_left_out() -> None:
    payload = {"items": [{"id": VIDEO_ID, "snippet": {"title": "Song", "channelTitle": "Band"}}]}
    song = await make_client(lambda request: httpx.Response(200, json=payload)).get_song(VIDEO_ID)

    assert song.artwork_url is None
    assert song.attributes == {"raw_title": "Song", "channel_title": "Band", "is_music_category": False}


@pytest.mark.parametrize(
    ("video_title", "channel", "expected"),
    [
        ("Daft Punk - Get Lucky (Official Audio) ft. Pharrell", "Daft Punk", ("Daft Punk", "Get Lucky ft. Pharrell")),
        ("Get Lucky", "Daft Punk - Topic", ("Daft Punk", "Get Lucky")),
        ("Hello", "AdeleVEVO", ("Adele", "Hello")),
        ("Hello [Lyrics]", "Adele Official", ("Adele", "Hello")),
        ("Artist – Song Title", "Some Uploader", ("Artist", "Song Title")),
        ("Song - Part 2", "Band - Topic", ("Band", "Song - Part 2")),
        ("(Official Video)", "Band", ("Band", "(Official Video)")),
        ("Track Name", "VEVO", ("VEVO", "Track Name")),
    ],
)
def test_split_artist_and_title(video_title: str, channel: str, expected: tuple[str, str]) -> None:
    assert split_artist_and_title(video_title, channel) == expected


@pytest.mark.anyio
async def test_missing_api_key_raises_not_configured() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        await make_client(lambda request: httpx.Response(200), api_key=None).get_song(VIDEO_ID)


@pytest.mark.anyio
async def test_empty_result_raises_not_found() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"items": []}))
    with pytest.raises(SongNotFoundError):
        await client.get_song(VIDEO_ID)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (api_error(403, "quotaExceeded"), RateLimitedError),
        (httpx.Response(429), RateLimitedError),
        (api_error(400, "keyInvalid"), ProviderNotConfiguredError),
        (INVALID_KEY_RESPONSE, ProviderNotConfiguredError),
        (httpx.Response(400, json={"error": {"details": "odd", "errors": None}}), SongNotFoundError),
        (httpx.Response(400, json={"error": "odd"}), SongNotFoundError),
        (api_error(403, "accessNotConfigured"), ProviderNotConfiguredError),
        (httpx.Response(403), ProviderNotConfiguredError),
        (httpx.Response(404), SongNotFoundError),
        (api_error(400, "invalidParameter"), SongNotFoundError),
        (httpx.Response(500), ProviderUnavailableError),
        (httpx.Response(409), ProviderUnavailableError),
        (httpx.Response(200, json={"items": [{"id": VIDEO_ID}]}), ProviderUnavailableError),
        (httpx.Response(200, content=b"<html>"), ProviderUnavailableError),
    ],
)
async def test_error_responses(response: httpx.Response, error: type[Exception]) -> None:
    with pytest.raises(error):
        await make_client(lambda request: response).get_song(VIDEO_ID)


@pytest.mark.anyio
async def test_network_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderUnavailableError):
        await make_client(handler).get_song(VIDEO_ID)
