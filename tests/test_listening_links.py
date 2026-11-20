"""Artwork and listening links for recommendations: Spotify search matching,
the link service, and the fields in the recommendations API."""

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.dependencies import get_ai_provider, get_lastfm, get_metadata_providers, get_track_searcher
from app.errors import ProviderNotConfiguredError, ProviderUnavailableError
from app.integrations.spotify import SEARCH_LIMIT, SpotifyClient
from app.main import app
from app.models import Song
from app.schemas.song import SongMetadata
from app.services.link_parser import Platform
from app.services.listening_links import RECHECK_AFTER, ListeningLinkService
from tests.fake_ai import FakeAI
from tests.fake_lastfm import m83_world
from tests.fakes import SPOTIFY_ID, FakeProvider, spotify_song
from tests.test_recommendations import URL, queue_success

TOKEN = {"access_token": "token-1", "token_type": "Bearer", "expires_in": 3600}


def spotify_track(track_id: str, name: str, artists: list[str], album: str = "Album") -> dict:
    return {
        "id": track_id,
        "name": name,
        "artists": [{"id": f"a{i}", "name": a} for i, a in enumerate(artists)],
        "album": {"name": album, "images": [{"url": f"https://i.scdn.co/image/{track_id}", "width": 640, "height": 640}]},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
    }


def search_client(items: list[dict], calls: list[httpx.Request] | None = None, status: int = 200) -> SpotifyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.host == "accounts.spotify.com":
            return httpx.Response(200, json=TOKEN)
        return httpx.Response(status, json={"tracks": {"items": items}})

    return SpotifyClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "id", "secret", market="GB")


# ----- SpotifyClient.search_track -----


@pytest.mark.anyio
async def test_search_returns_only_a_matching_track() -> None:
    calls: list[httpx.Request] = []
    client = search_client(
        [
            spotify_track("cover", "Kids", ["Karaoke Kings"]),
            spotify_track("other", "Kids Again", ["MGMT"]),
            spotify_track("real", "Kids - 2008 Remaster", ["MGMT"], album="Oracular Spectacular"),
        ],
        calls,
    )
    song = await client.search_track("Kids", "MGMT")
    assert song is not None
    assert (song.external_id, song.album, song.artwork_url) == ("real", "Oracular Spectacular", "https://i.scdn.co/image/real")
    assert song.url == "https://open.spotify.com/track/real"

    search = calls[-1]
    assert search.url.path == "/v1/search"
    assert search.url.params["q"] == 'track:"Kids" artist:"MGMT"'
    assert search.url.params["type"] == "track"
    assert int(search.url.params["limit"]) == SEARCH_LIMIT <= 10
    assert search.url.params["market"] == "GB"


@pytest.mark.anyio
async def test_search_matches_featured_artists_and_decorations() -> None:
    calls: list[httpx.Request] = []
    client = search_client([spotify_track("t1", "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"])], calls)
    assert await client.search_track("Get Lucky (feat. Pharrell Williams) - Radio Edit", "Daft Punk feat. Pharrell Williams") is not None
    assert calls[-1].url.params["q"] == 'track:"Get Lucky" artist:"Daft Punk"'


@pytest.mark.anyio
async def test_quotes_cannot_break_out_of_query_fields() -> None:
    calls: list[httpx.Request] = []
    client = search_client([spotify_track("t2", 'Say "Hello"', ["Band"])], calls)
    assert await client.search_track('Say "Hello"', 'The "Band"') is None, "the artist doesn't match"
    query = calls[-1].url.params["q"]
    assert query.count('"') == 4, query


@pytest.mark.anyio
async def test_search_returns_none_without_a_match() -> None:
    assert await search_client([spotify_track("x", "Different Song", ["MGMT"])]).search_track("Kids", "MGMT") is None
    assert await search_client([]).search_track("Kids", "MGMT") is None
    assert await search_client([], status=400).search_track("Kids", "MGMT") is None


@pytest.mark.anyio
async def test_search_errors() -> None:
    with pytest.raises(ProviderUnavailableError):
        await search_client([], status=500).search_track("Kids", "MGMT")
    with pytest.raises(ProviderNotConfiguredError):
        unconfigured = SpotifyClient(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))), None, None)
        await unconfigured.search_track("Kids", "MGMT")


# ----- ListeningLinkService -----


class FakeSearcher:
    platform = Platform.SPOTIFY

    def __init__(self, matches: dict[tuple[str, str], SongMetadata | None] | None = None, error: Exception | None = None) -> None:
        self.matches = matches or {}
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def search_track(self, title: str, artist: str) -> SongMetadata | None:
        self.calls.append((title, artist))
        if self.error is not None:
            raise self.error
        return self.matches.get((title, artist))


def lastfm_song(db: Session, title: str, artist: str) -> Song:
    song = Song(title=title, artist=artist, platform="lastfm", external_id=f"lf:{title}", url=f"https://www.last.fm/music/{artist}/_/{title}", attributes={"tags": []})
    db.add(song)
    db.commit()
    return song


def kids_on_spotify() -> SongMetadata:
    return SongMetadata(platform=Platform.SPOTIFY, external_id="kids1", title="Kids", artist="MGMT", album="Oracular Spectacular",
                        artwork_url="https://i.scdn.co/image/kids", url="https://open.spotify.com/track/kids1")


@pytest.mark.anyio
async def test_links_are_recorded_and_not_searched_again(db_session: Session) -> None:
    kids = lastfm_song(db_session, "Kids", "MGMT")
    obscure = lastfm_song(db_session, "Demo Tape", "Nobody")
    searcher = FakeSearcher({("Kids", "MGMT"): kids_on_spotify()})
    service = ListeningLinkService(searcher)

    await service.enrich([kids, obscure])
    db_session.commit()
    db_session.refresh(kids)
    assert kids.artwork_url == "https://i.scdn.co/image/kids"
    assert kids.album == "Oracular Spectacular"
    assert kids.attributes["listen"]["url"] == "https://open.spotify.com/track/kids1"
    assert kids.attributes["tags"] == [], "other attributes are kept"
    assert obscure.artwork_url is None and obscure.attributes["listen"].get("url") is None

    await service.enrich([kids, obscure])
    assert len(searcher.calls) == 2, "found and recently-missing songs are not searched again"

    # A miss is retried after a while.
    old = (datetime.now(timezone.utc) - RECHECK_AFTER - timedelta(hours=1)).isoformat()
    obscure.attributes = {**obscure.attributes, "listen": {"checked_at": old}}
    await service.enrich([kids, obscure])
    assert searcher.calls[-1] == ("Demo Tape", "Nobody")


@pytest.mark.anyio
async def test_failures_leave_songs_unchecked(db_session: Session) -> None:
    kids = lastfm_song(db_session, "Kids", "MGMT")
    for error in (ProviderUnavailableError("down"), ProviderNotConfiguredError("no keys")):
        await ListeningLinkService(FakeSearcher(error=error)).enrich([kids])
        assert "listen" not in kids.attributes
        assert kids.artwork_url is None


# ----- API -----


@pytest.fixture
def env(client: TestClient) -> Iterator[tuple[TestClient, FakeAI, FakeSearcher]]:
    ai = FakeAI()
    searcher = FakeSearcher({("Kids", "MGMT"): kids_on_spotify()})
    app.dependency_overrides[get_metadata_providers] = lambda: [FakeProvider(Platform.SPOTIFY, spotify_song())]
    app.dependency_overrides[get_lastfm] = m83_world
    app.dependency_overrides[get_ai_provider] = lambda: ai
    app.dependency_overrides[get_track_searcher] = lambda: searcher
    yield client, ai, searcher
    app.dependency_overrides.clear()


def test_recommendations_include_artwork_and_listening_links(env) -> None:
    client, ai, searcher = env
    queue_success(ai)
    body = client.post("/api/recommendations", json={"url": URL}).json()
    by_title = {r["title"]: r for r in body["recommendations"]}

    kids = by_title["Kids"]
    assert kids["artwork_url"] == "https://i.scdn.co/image/kids"
    assert kids["album"] == "Oracular Spectacular"
    assert (kids["listen_platform"], kids["listen_url"]) == ("spotify", "https://open.spotify.com/track/kids1")
    assert kids["url"].startswith("https://www.last.fm/"), "the source link is still available"

    other = next(r for r in body["recommendations"] if r["title"] != "Kids")
    assert other["artwork_url"] is None
    assert other["listen_platform"] == "lastfm" and other["listen_url"] == other["url"]
    assert len(searcher.calls) == len(body["recommendations"])

    # Stored results include the links; cached results don't search again.
    stored = client.get(f"/api/recommendations/{body['request_id']}").json()
    assert stored["recommendations"] == body["recommendations"]
    again = client.post("/api/recommendations", json={"url": URL}).json()
    assert again["cached"] is True and len(searcher.calls) == len(body["recommendations"])
    assert json.dumps(again["recommendations"]) == json.dumps(body["recommendations"])


def test_recommendations_work_without_spotify(env) -> None:
    client, ai, _ = env
    app.dependency_overrides[get_track_searcher] = lambda: None
    queue_success(ai)
    body = client.post("/api/recommendations", json={"url": URL}).json()
    assert body["recommendations"]
    assert all(r["artwork_url"] is None and r["listen_platform"] == "lastfm" for r in body["recommendations"])
    assert SPOTIFY_ID in body["song"]["url"]
