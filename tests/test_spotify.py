from collections.abc import Callable

import httpx
import pytest

from app.errors import (
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    RateLimitedError,
    SongNotFoundError,
)
from app.integrations.spotify import SpotifyClient
from app.services.link_parser import Platform

pytestmark = pytest.mark.anyio

TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"

TRACK_PAYLOAD = {
    "id": TRACK_ID,
    "name": "Midnight City",
    "artists": [{"id": "artist1", "name": "M83"}, {"id": "artist2", "name": "Guest"}],
    "album": {
        "name": "Hurry Up, We're Dreaming",
        "release_date": "2011-10-18",
        "images": [
            {"url": "https://i.scdn.co/image/small", "width": 64, "height": 64},
            {"url": "https://i.scdn.co/image/large", "width": 640, "height": 640},
        ],
    },
    "duration_ms": 243960,
    "explicit": False,
    "external_urls": {"spotify": f"https://open.spotify.com/track/{TRACK_ID}"},
    "external_ids": {"isrc": "FR6V81141061"},
}

TOKEN_PAYLOAD = {"access_token": "token-1", "token_type": "Bearer", "expires_in": 3600}

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler, client_id: str | None = "id", secret: str | None = "secret") -> SpotifyClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SpotifyClient(http, client_id, secret, market="GB")


def routes(track_response: httpx.Response, calls: list[httpx.Request] | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "accounts.spotify.com":
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return track_response

    return handler


async def test_get_song_maps_track_metadata() -> None:
    calls: list[httpx.Request] = []
    client = make_client(routes(httpx.Response(200, json=TRACK_PAYLOAD), calls))

    song = await client.get_song(TRACK_ID)

    assert song.platform is Platform.SPOTIFY
    assert song.external_id == TRACK_ID
    assert song.title == "Midnight City"
    assert song.artist == "M83, Guest"
    assert song.album == "Hurry Up, We're Dreaming"
    assert song.artwork_url == "https://i.scdn.co/image/large"
    assert song.url == f"https://open.spotify.com/track/{TRACK_ID}"
    assert song.attributes == {
        "artists": ["M83", "Guest"],
        "artist_ids": ["artist1", "artist2"],
        "duration_ms": 243960,
        "explicit": False,
        "release_date": "2011-10-18",
        "isrc": "FR6V81141061",
    }

    token_request, track_request = calls
    assert token_request.method == "POST"
    assert token_request.headers["Authorization"].startswith("Basic ")
    assert b"grant_type=client_credentials" in token_request.content
    assert track_request.url.path == f"/v1/tracks/{TRACK_ID}"
    assert track_request.url.params["market"] == "GB"
    assert track_request.headers["Authorization"] == "Bearer token-1"


async def test_missing_optional_fields_are_left_out() -> None:
    payload = {
        "id": TRACK_ID,
        "name": "Song",
        "artists": [{"name": "Artist"}],
        "album": {"name": "", "images": []},
    }
    client = make_client(routes(httpx.Response(200, json=payload)))

    song = await client.get_song(TRACK_ID)

    assert song.album is None
    assert song.artwork_url is None
    assert song.url == f"https://open.spotify.com/track/{TRACK_ID}"
    assert song.attributes == {"artists": ["Artist"], "artist_ids": []}


async def test_token_is_reused_between_requests() -> None:
    calls: list[httpx.Request] = []
    client = make_client(routes(httpx.Response(200, json=TRACK_PAYLOAD), calls))

    await client.get_song(TRACK_ID)
    await client.get_song(TRACK_ID)

    assert [request.url.host for request in calls].count("accounts.spotify.com") == 1


async def test_expired_token_is_refreshed_once() -> None:
    track_responses = iter([httpx.Response(401), httpx.Response(200, json=TRACK_PAYLOAD)])
    token_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_count
        if request.url.host == "accounts.spotify.com":
            token_count += 1
            return httpx.Response(200, json=TOKEN_PAYLOAD)
        return next(track_responses)

    song = await make_client(handler).get_song(TRACK_ID)

    assert song.title == "Midnight City"
    assert token_count == 2


async def test_repeated_401_raises_unavailable() -> None:
    client = make_client(routes(httpx.Response(401)))
    with pytest.raises(ProviderUnavailableError):
        await client.get_song(TRACK_ID)


async def test_missing_credentials_raise_not_configured() -> None:
    client = make_client(routes(httpx.Response(200, json=TRACK_PAYLOAD)), client_id=None)
    with pytest.raises(ProviderNotConfiguredError):
        await client.get_song(TRACK_ID)


async def test_rejected_credentials_raise_not_configured() -> None:
    client = make_client(lambda request: httpx.Response(400, json={"error": "invalid_client"}))
    with pytest.raises(ProviderNotConfiguredError):
        await client.get_song(TRACK_ID)


@pytest.mark.parametrize("status", [400, 404])
async def test_unknown_track_raises_not_found(status: int) -> None:
    client = make_client(routes(httpx.Response(status)))
    with pytest.raises(SongNotFoundError):
        await client.get_song(TRACK_ID)


async def test_rate_limit_includes_retry_after() -> None:
    client = make_client(routes(httpx.Response(429, headers={"Retry-After": "7"})))
    with pytest.raises(RateLimitedError) as excinfo:
        await client.get_song(TRACK_ID)
    assert excinfo.value.retry_after == 7


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(403),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={**TRACK_PAYLOAD, "name": ""}),
    ],
)
async def test_bad_responses_raise_unavailable(response: httpx.Response) -> None:
    client = make_client(routes(response))
    with pytest.raises(ProviderUnavailableError):
        await client.get_song(TRACK_ID)


async def test_empty_artist_list_raises_not_found() -> None:
    client = make_client(routes(httpx.Response(200, json={**TRACK_PAYLOAD, "artists": []})))
    with pytest.raises(SongNotFoundError):
        await client.get_song(TRACK_ID)


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("slow"), httpx.ConnectError("down")])
async def test_network_errors_raise_unavailable(exc: Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with pytest.raises(ProviderUnavailableError):
        await make_client(handler).get_song(TRACK_ID)
