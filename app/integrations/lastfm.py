"""Last.fm API integration: similar tracks and artists, tags and tag charts.

Last.fm is the discovery source because it exposes listening-based similarity
(`track.getSimilar`, `artist.getSimilar`) and community tags, which describe
genre, mood and style. It needs a free API key and asks clients to stay well
below several requests per second, so calls are rate limited and responses
are cached in memory.

Last.fm's JSON is irregular (single items are objects instead of lists,
numbers are sometimes strings, errors may arrive with HTTP 200), so every
response is normalized and validated here.
"""

import asyncio
import time
from collections import OrderedDict
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.errors import ProviderNotConfiguredError, ProviderUnavailableError, RateLimitedError
from app.integrations.http import send

API_URL = "https://ws.audioscrobbler.com/2.0/"
PROVIDER = "Last.fm"

# Last.fm error codes (https://www.last.fm/api/errorcodes).
NOT_FOUND_CODES = {6, 7}
KEY_CODES = {10, 26}
RATE_LIMIT_CODE = 29
TEMPORARY_CODES = {8, 11, 16}


class LastFmTrack(BaseModel):
    title: str = Field(min_length=1)
    artist: str = Field(min_length=1)
    url: str | None = None
    mbid: str | None = None
    #: Similarity to the seed (0-1), for `track.getSimilar` results.
    match: float | None = Field(default=None, ge=0, le=1)


class LastFmArtist(BaseModel):
    name: str = Field(min_length=1)
    url: str | None = None
    match: float | None = Field(default=None, ge=0, le=1)


class LastFmTag(BaseModel):
    name: str = Field(min_length=1)
    #: Relative weight, 0-100 (100 = the most-applied tag).
    count: int = Field(ge=0, le=100)


class RateLimiter:
    """Allows at most `rate` calls per second, spacing calls evenly."""

    def __init__(self, rate: float, clock: Any = time.monotonic, sleep: Any = asyncio.sleep) -> None:
        self._interval = 1.0 / rate
        self._next = 0.0
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            delay = self._next - now
            if delay > 0:
                await self._sleep(delay)
                now += delay
            self._next = now + self._interval


class TTLCache:
    """A small in-memory cache with expiry and a size bound (LRU eviction)."""

    def __init__(self, ttl_seconds: float, max_entries: int = 2000, clock: Any = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires < self._clock():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: Any, value: Any) -> None:
        self._data[key] = (self._clock() + self._ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)


class LastFmClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str | None,
        requests_per_second: float = 4.0,
        cache_ttl_seconds: float = 24 * 3600,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._limiter = RateLimiter(requests_per_second)
        self._cache = TTLCache(cache_ttl_seconds)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    # ----- API methods -----

    async def similar_tracks(self, artist: str, title: str, limit: int = 50) -> list[LastFmTrack]:
        data = await self._call("track.getSimilar", artist=artist, track=title, limit=limit, autocorrect=1)
        items = _as_list(_path(data, "similartracks", "track"))
        return _parse_all(items, lambda t: LastFmTrack(
            title=_text(t.get("name")),
            artist=_text(_artist_name(t.get("artist"))),
            url=_url(t.get("url")),
            mbid=_text(t.get("mbid")) or None,
            match=_fraction(t.get("match")),
        ))

    async def search_track(self, artist: str, title: str) -> LastFmTrack | None:
        """The best Last.fm match for a possibly misspelled or decorated title."""
        data = await self._call("track.search", artist=artist, track=title, limit=5)
        items = _as_list(_path(data, "results", "trackmatches", "track"))
        tracks = _parse_all(items, lambda t: LastFmTrack(
            title=_text(t.get("name")),
            artist=_text(_artist_name(t.get("artist"))),
            url=_url(t.get("url")),
            mbid=_text(t.get("mbid")) or None,
        ))
        return tracks[0] if tracks else None

    async def track_tags(self, artist: str, title: str) -> list[LastFmTag]:
        data = await self._call("track.getTopTags", artist=artist, track=title, autocorrect=1)
        return _parse_tags(data)

    async def artist_tags(self, artist: str) -> list[LastFmTag]:
        data = await self._call("artist.getTopTags", artist=artist, autocorrect=1)
        return _parse_tags(data)

    async def similar_artists(self, artist: str, limit: int = 10) -> list[LastFmArtist]:
        data = await self._call("artist.getSimilar", artist=artist, limit=limit, autocorrect=1)
        items = _as_list(_path(data, "similarartists", "artist"))
        return _parse_all(items, lambda a: LastFmArtist(
            name=_text(a.get("name")), url=_url(a.get("url")), match=_fraction(a.get("match"))
        ))

    async def artist_top_tracks(self, artist: str, limit: int = 5) -> list[LastFmTrack]:
        data = await self._call("artist.getTopTracks", artist=artist, limit=limit, autocorrect=1)
        items = _as_list(_path(data, "toptracks", "track"))
        return _parse_all(items, lambda t: LastFmTrack(
            title=_text(t.get("name")),
            artist=_text(_artist_name(t.get("artist"))),
            url=_url(t.get("url")),
            mbid=_text(t.get("mbid")) or None,
        ))

    async def tag_top_tracks(self, tag: str, limit: int = 20) -> list[LastFmTrack]:
        data = await self._call("tag.getTopTracks", tag=tag, limit=limit)
        items = _as_list(_path(data, "tracks", "track"))
        return _parse_all(items, lambda t: LastFmTrack(
            title=_text(t.get("name")),
            artist=_text(_artist_name(t.get("artist"))),
            url=_url(t.get("url")),
            mbid=_text(t.get("mbid")) or None,
        ))

    # ----- Transport -----

    async def _call(self, method: str, **params: Any) -> dict:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                "Music discovery isn't available: the server has no Last.fm API key configured."
            )
        key = (method, tuple(sorted((k, str(v).casefold()) for k, v in params.items())))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        await self._limiter.wait()
        request = self._http.build_request(
            "GET", API_URL, params={"method": method, "api_key": self._api_key, "format": "json", **params}
        )
        response = await send(self._http, request, PROVIDER)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("Last.fm returned an unreadable response.") from exc
        if not isinstance(data, dict):
            raise ProviderUnavailableError("Last.fm returned an unexpected response.")

        code = data.get("error")
        if code is not None:
            data = _handle_error(code, data.get("message"))
        elif response.status_code != 200:
            raise ProviderUnavailableError(f"Last.fm returned an unexpected error ({response.status_code}).")
        self._cache.set(key, data)
        return data


def _handle_error(code: Any, message: Any) -> dict:
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = -1
    if code in NOT_FOUND_CODES:
        # Unknown track/artist: an empty result, not a failure.
        return {}
    if code in KEY_CODES:
        raise ProviderNotConfiguredError("Last.fm rejected the server's API key.")
    if code == RATE_LIMIT_CODE:
        raise RateLimitedError("Last.fm is receiving too many requests. Please wait a moment and try again.")
    if code in TEMPORARY_CODES:
        raise ProviderUnavailableError("Last.fm is temporarily unavailable. Please try again shortly.")
    raise ProviderUnavailableError(f"Last.fm returned an error ({code}: {str(message or '')[:100]}).")


# ----- Normalization helpers -----


def _path(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _as_list(value: Any) -> list[dict]:
    """Last.fm returns an object instead of a one-item list for single results."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _artist_name(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("#text"))
    return _text(value)


def _url(value: Any) -> str | None:
    text = _text(value)
    return text if text.startswith("https://www.last.fm/") or text.startswith("http://www.last.fm/") else None


def _fraction(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return min(max(number, 0.0), 1.0)


def _parse_all(items: list[dict], build: Any) -> list:
    """Builds each item, skipping malformed ones (e.g. a track without a name)."""
    parsed = []
    for item in items:
        try:
            parsed.append(build(item))
        except ValueError:
            continue
    return parsed


def _parse_tags(data: dict) -> list[LastFmTag]:
    items = _as_list(_path(data, "toptags", "tag"))

    def build(t: dict) -> LastFmTag:
        try:
            count = int(t.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        return LastFmTag(name=_text(t.get("name")).lower(), count=min(max(count, 0), 100))

    return [tag for tag in _parse_all(items, build) if tag.count > 0]
