from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_lastfm, get_metadata_providers
from app.errors import NoCandidatesError, RateLimitedError
from app.integrations.lastfm import LastFmArtist
from app.main import app
from app.services.link_parser import Platform
from app.services.music_discovery import DiscoverySettings, MusicDiscoveryService, prior
from app.schemas.discovery import Candidate
from tests.fake_lastfm import FakeLastFm, m83_world, tags, track
from tests.fakes import SPOTIFY_ID, FakeProvider, spotify_song

pytestmark = pytest.mark.anyio


def keys(result) -> list[tuple[str, str]]:
    return [(c.artist, c.title) for c in result.candidates]


async def test_combines_sources_and_filters() -> None:
    fm = m83_world()
    result = await MusicDiscoveryService(fm).discover(spotify_song())

    assert result.seed.title == "Midnight City" and result.seed.artist == "M83"
    assert result.seed.found_on_lastfm
    found = keys(result)
    assert ("MGMT", "Kids") in found
    assert ("Washed Out", "Feel It All Around") in found, "similar artists add candidates"
    assert ("Beach House", "Space Song") in found, "seed tags add cross-artist candidates"
    assert all(artist != "M83" for artist, _ in found), "the seed's own artist is excluded"
    assert all("Midnight City" not in title for _, title in found), "versions and covers are excluded"
    assert all("Karaoke" not in artist for artist, _ in found)
    assert sum(1 for artist, _ in found if artist == "MGMT") == 2, "at most two tracks per artist"
    assert len(found) == len(set(found)), "no duplicates"


async def test_merges_duplicates_and_records_sources() -> None:
    result = await MusicDiscoveryService(m83_world()).discover(spotify_song())
    by_key = {(c.artist, c.title): c for c in result.candidates}

    kids = by_key[("MGMT", "Kids")]
    assert kids.sources == ["similar_track", "similar_artist"]
    assert (kids.track_match, kids.artist_match) == (0.82, 0.9)

    intro = by_key[("The xx", "Intro")]
    assert intro.sources == ["similar_track", "tag:electronic"]
    assert intro.artist_match is None

    washed = by_key[("Washed Out", "Feel It All Around")]
    assert (washed.track_match, washed.artist_match) == (None, 0.75)


async def test_candidates_are_ordered_and_trimmed() -> None:
    settings = DiscoverySettings(max_candidates=3)
    result = await MusicDiscoveryService(m83_world(), settings).discover(spotify_song())
    assert keys(result) == [("MGMT", "Kids"), ("MGMT", "Electric Feel"), ("The xx", "Intro")]


async def test_seed_and_candidate_tags_are_cleaned() -> None:
    result = await MusicDiscoveryService(m83_world()).discover(spotify_song())
    assert [(t.name, t.weight) for t in result.seed.tags] == [("electronic", 100), ("dream pop", 70), ("synthpop", 25)]

    by_key = {(c.artist, c.title): c for c in result.candidates}
    assert [t.name for t in by_key[("MGMT", "Kids")].tags] == ["indie", "electronic", "synthpop"]
    # No track tags: the artist's tags are used instead.
    assert [t.name for t in by_key[("Washed Out", "Feel It All Around")].tags] == ["chillwave", "dream pop"]
    assert by_key[("Washed Out", "Amor Fati")].tags == by_key[("Washed Out", "Feel It All Around")].tags
    # No data at all: empty, not invented.
    assert by_key[("ODESZA", "Sun Models")].tags == []


async def test_uses_primary_artist_and_clean_title() -> None:
    fm = m83_world()
    song = spotify_song().model_copy(update={"artist": "M83, Someone Else", "title": "Midnight City - 2011 Remaster"})
    result = await MusicDiscoveryService(fm).discover(song)
    assert fm.calls[0] == ("similar_tracks", "M83", "Midnight City")
    assert result.candidates


async def test_falls_back_to_search_when_track_is_unknown() -> None:
    fm = m83_world()
    song = spotify_song().model_copy(update={"title": "Midnite City (Official Video)", "artist": "m83", "attributes": {}})
    fm.search[("m83", "midnite city official video")] = track("Midnight City", "M83")
    result = await MusicDiscoveryService(fm).discover(song)
    assert ("search_track", "m83", "Midnite City (Official Video)") in fm.calls
    assert (result.seed.artist, result.seed.title) == ("M83", "Midnight City")
    assert ("MGMT", "Kids") in keys(result)


async def test_unknown_song_with_tags_still_finds_tag_candidates() -> None:
    fm = FakeLastFm()
    fm.artist_tag_map["new band"] = tags(("indie folk", 100))
    fm.tag_tracks["indie folk"] = [track("Holocene", "Bon Iver")]
    song = spotify_song().model_copy(update={"title": "Brand New Song", "artist": "New Band", "attributes": {}})
    result = await MusicDiscoveryService(fm).discover(song)
    assert result.seed.found_on_lastfm, "known through its artist's tags"
    assert keys(result) == [("Bon Iver", "Holocene")]
    assert result.candidates[0].sources == ["tag:indie folk"]


async def test_nothing_found_raises() -> None:
    with pytest.raises(NoCandidatesError, match="Couldn't find songs similar"):
        await MusicDiscoveryService(FakeLastFm()).discover(spotify_song())


async def test_single_tag_failure_does_not_lose_discovery() -> None:
    fm = m83_world()
    fm.failing_tags.add(("mgmt", "kids"))
    result = await MusicDiscoveryService(fm).discover(spotify_song())
    kids = next(c for c in result.candidates if c.title == "Kids")
    assert kids.tags == []


async def limited(*args, **kwargs):
    raise RateLimitedError("slow down")


async def test_failing_optional_sources_are_skipped() -> None:
    """Similar artists, their top tracks, tag charts and candidate tags only
    add data: one failing lookup doesn't lose the other candidates."""
    fm = m83_world()
    fm.similar_artists = limited  # type: ignore[method-assign]
    fm.tag_top_tracks = limited  # type: ignore[method-assign]
    fm.artist_tags = limited  # type: ignore[method-assign]
    result = await MusicDiscoveryService(fm).discover(spotify_song())
    assert {c.title for c in result.candidates} >= {"Kids", "Intro"}


async def test_rate_limits_propagate_when_nothing_is_found() -> None:
    fm = m83_world()
    fm.similar = {}
    fm.similar_artists = limited  # type: ignore[method-assign]
    fm.tag_top_tracks = limited  # type: ignore[method-assign]
    with pytest.raises(RateLimitedError):
        await MusicDiscoveryService(fm).discover(spotify_song())


async def test_the_main_lookup_still_fails_loudly() -> None:
    fm = m83_world()
    fm.similar_tracks = limited  # type: ignore[method-assign]
    with pytest.raises(RateLimitedError):
        await MusicDiscoveryService(fm).discover(spotify_song())


def test_prior_ordering() -> None:
    direct = Candidate(title="a", artist="x", track_match=0.6, sources=["similar_track"])
    via_artist = Candidate(title="b", artist="y", artist_match=0.9, sources=["similar_artist"])
    via_tag = Candidate(title="c", artist="z", sources=["tag:indie"])
    multi = Candidate(title="d", artist="w", artist_match=0.5, sources=["similar_artist", "tag:indie"])
    assert prior(via_artist) == pytest.approx(0.72)
    assert prior(direct) == pytest.approx(0.6)
    assert prior(via_tag) == pytest.approx(0.35)
    assert prior(multi) == pytest.approx(0.45)


# ----- API -----


@pytest.fixture
def api(client: TestClient) -> Iterator[tuple[TestClient, FakeLastFm]]:
    fm = m83_world()
    providers = [FakeProvider(Platform.SPOTIFY, spotify_song())]
    app.dependency_overrides[get_metadata_providers] = lambda: providers
    app.dependency_overrides[get_lastfm] = lambda: fm
    yield client, fm
    app.dependency_overrides.clear()


def test_discovery_endpoint(api: tuple[TestClient, FakeLastFm]) -> None:
    client, _ = api
    response = client.post("/api/discovery", json={"url": f"https://open.spotify.com/track/{SPOTIFY_ID}"})
    assert response.status_code == 200
    body = response.json()
    assert body["song"]["title"] == "Midnight City"
    assert body["discovery"]["seed"]["tags"][0] == {"name": "electronic", "weight": 100}
    first = body["discovery"]["candidates"][0]
    assert first["artist"] == "MGMT" and first["title"] == "Kids"
    assert first["url"] == "https://www.last.fm/music/MGMT/_/Kids"


def test_discovery_endpoint_errors(api: tuple[TestClient, FakeLastFm]) -> None:
    client, fm = api
    fm.similar.clear()
    fm.similar_artist_map.clear()
    fm.track_tag_map.clear()
    response = client.post("/api/discovery", json={"url": f"https://open.spotify.com/track/{SPOTIFY_ID}"})
    assert response.status_code == 404
    assert response.json()["error"] == "no_recommendations"

    assert client.post("/api/discovery", json={"url": "nope"}).status_code == 400


def test_discovery_without_lastfm_key(client: TestClient) -> None:
    providers = [FakeProvider(Platform.SPOTIFY, spotify_song())]
    app.dependency_overrides[get_metadata_providers] = lambda: providers
    try:
        response = client.post("/api/discovery", json={"url": f"https://open.spotify.com/track/{SPOTIFY_ID}"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["error"] == "provider_not_configured"
    assert "Last.fm" in response.json()["message"]


def test_similar_artist_without_match_is_handled() -> None:
    artist = LastFmArtist(name="X")
    assert artist.match is None


async def test_uses_the_provider_artist_list_when_available() -> None:
    fm = FakeLastFm()
    fm.similar[("earth wind and fire", "september")] = [track("Le Freak", "Chic", 0.9)]
    song = spotify_song().model_copy(
        update={"title": "September", "artist": "Earth, Wind & Fire", "attributes": {"artists": ["Earth, Wind & Fire"]}}
    )
    result = await MusicDiscoveryService(fm).discover(song)
    assert fm.calls[0] == ("similar_tracks", "Earth, Wind & Fire", "September")
    assert keys(result) == [("Chic", "Le Freak")]
