from collections.abc import Callable

import httpx
import pytest

from app.errors import ProviderNotConfiguredError, ProviderUnavailableError, RateLimitedError
from app.integrations.lastfm import LastFmClient, RateLimiter, TTLCache

pytestmark = pytest.mark.anyio

SIMILAR = {
    "similartracks": {
        "track": [
            {
                "name": "Wait",
                "match": 1.0,
                "mbid": "abc",
                "url": "https://www.last.fm/music/M83/_/Wait",
                "artist": {"name": "M83", "url": "https://www.last.fm/music/M83"},
            },
            {"name": "Kids", "match": "0.62", "url": "https://www.last.fm/music/MGMT/_/Kids", "artist": {"name": "MGMT"}},
            {"name": "", "match": 0.5, "artist": {"name": "Nameless"}},
            {"name": "Odd", "match": "not-a-number", "url": "https://evil.example/x", "artist": {"name": "Band"}},
        ]
    }
}


def make_client(handler: Callable[[httpx.Request], httpx.Response], api_key: str | None = "key") -> LastFmClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LastFmClient(http, api_key, requests_per_second=1000)


def by_method(responses: dict[str, object], calls: list[httpx.Request] | None = None) -> Callable:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        body = responses[request.url.params["method"]]
        return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)

    return handler


async def test_similar_tracks_are_normalized() -> None:
    calls: list[httpx.Request] = []
    client = make_client(by_method({"track.getSimilar": SIMILAR}, calls))

    tracks = await client.similar_tracks("M83", "Midnight City", limit=10)

    assert [(t.title, t.artist, t.match) for t in tracks] == [("Wait", "M83", 1.0), ("Kids", "MGMT", 0.62), ("Odd", "Band", None)]
    assert tracks[0].mbid == "abc"
    assert tracks[0].url == "https://www.last.fm/music/M83/_/Wait"
    assert tracks[2].url is None, "non-Last.fm URLs are dropped"
    params = calls[0].url.params
    assert (params["artist"], params["track"], params["limit"], params["autocorrect"]) == ("M83", "Midnight City", "10", "1")
    assert (params["api_key"], params["format"]) == ("key", "json")


async def test_single_results_and_string_artists() -> None:
    responses = {
        "artist.getSimilar": {"similarartists": {"artist": {"name": "MGMT", "match": "0.9", "url": "https://www.last.fm/music/MGMT"}}},
        "track.search": {"results": {"trackmatches": {"track": [{"name": "Midnight City", "artist": "M83", "url": "https://www.last.fm/music/M83/_/Midnight+City"}]}}},
        "artist.getTopTracks": {"toptracks": {"track": [{"name": "Kids", "artist": {"name": "MGMT"}}]}},
        "tag.getTopTracks": {"tracks": {"track": [{"name": "Intro", "artist": {"name": "The xx"}}]}},
    }
    client = make_client(by_method(responses))

    assert [(a.name, a.match) for a in await client.similar_artists("M83")] == [("MGMT", 0.9)]
    found = await client.search_track("m83", "midnight city (remastered)")
    assert found is not None and (found.title, found.artist) == ("Midnight City", "M83")
    assert [(t.title, t.artist) for t in await client.artist_top_tracks("MGMT")] == [("Kids", "MGMT")]
    assert [(t.title, t.artist) for t in await client.tag_top_tracks("indie")] == [("Intro", "The xx")]


async def test_tags_are_lowercased_and_bounded() -> None:
    tags = {"toptags": {"tag": [{"name": "Electronic", "count": 100}, {"name": "Dream Pop", "count": "61"}, {"name": "zero", "count": 0}, {"name": "x", "count": "lots"}]}}
    client = make_client(by_method({"track.getTopTags": tags, "artist.getTopTags": {"toptags": {}}}))
    assert [(t.name, t.count) for t in await client.track_tags("M83", "Midnight City")] == [("electronic", 100), ("dream pop", 61)]
    assert await client.artist_tags("Unknown") == []


async def test_not_found_is_an_empty_result() -> None:
    client = make_client(lambda request: httpx.Response(200, json={"error": 6, "message": "Track not found"}))
    assert await client.similar_tracks("Nobody", "Nothing") == []
    assert await client.search_track("Nobody", "Nothing") is None


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(200, json={"error": 10, "message": "Invalid API key"}), ProviderNotConfiguredError),
        (httpx.Response(403, json={"error": 26, "message": "Suspended API key"}), ProviderNotConfiguredError),
        (httpx.Response(200, json={"error": 29, "message": "Rate limit exceeded"}), RateLimitedError),
        (httpx.Response(429), RateLimitedError),
        (httpx.Response(200, json={"error": 16, "message": "Temporary error"}), ProviderUnavailableError),
        (httpx.Response(200, json={"error": 2, "message": "Invalid service"}), ProviderUnavailableError),
        (httpx.Response(503), ProviderUnavailableError),
        (httpx.Response(200, content=b"<html>"), ProviderUnavailableError),
        (httpx.Response(200, json=["not", "an", "object"]), ProviderUnavailableError),
        (httpx.Response(400, json={"unexpected": True}), ProviderUnavailableError),
    ],
)
async def test_errors(response: httpx.Response, error: type[Exception]) -> None:
    with pytest.raises(error):
        await make_client(lambda request: response).similar_tracks("A", "B")


async def test_missing_key_never_calls_the_api() -> None:
    calls: list[httpx.Request] = []
    client = make_client(by_method({}, calls), api_key=None)
    assert not client.configured
    with pytest.raises(ProviderNotConfiguredError):
        await client.similar_tracks("A", "B")
    assert calls == []


async def test_responses_are_cached_case_insensitively() -> None:
    calls: list[httpx.Request] = []
    client = make_client(by_method({"track.getSimilar": SIMILAR}, calls))
    await client.similar_tracks("M83", "Midnight City")
    await client.similar_tracks("m83", "MIDNIGHT CITY")
    await client.similar_tracks("M83", "Wait")
    assert len(calls) == 2


async def test_rate_limiter_spaces_calls() -> None:
    now = [0.0]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(4, clock=lambda: now[0], sleep=fake_sleep)
    for _ in range(3):
        await limiter.wait()
    assert sleeps == [0.25, 0.25]


def test_ttl_cache_expires_and_evicts() -> None:
    now = [0.0]
    cache = TTLCache(ttl_seconds=10, max_entries=2, clock=lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    assert cache.get("a") == 1
    cache.set("c", 3)  # evicts the least recently used ("b")
    assert cache.get("b") is None and cache.get("a") == 1 and cache.get("c") == 3
    now[0] = 11
    assert cache.get("a") is None
