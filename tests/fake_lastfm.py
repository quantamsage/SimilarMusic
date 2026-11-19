"""A deterministic stand-in for LastFmClient."""

from app.errors import ProviderUnavailableError
from app.integrations.lastfm import LastFmArtist, LastFmTag, LastFmTrack
from app.services.text import normalize


def tags(*pairs: tuple[str, int]) -> list[LastFmTag]:
    return [LastFmTag(name=name, count=count) for name, count in pairs]


def track(title: str, artist: str, match: float | None = None) -> LastFmTrack:
    slug = f"{artist}/_/{title}".replace(" ", "+")
    return LastFmTrack(title=title, artist=artist, url=f"https://www.last.fm/music/{slug}", match=match)


class FakeLastFm:
    """Configured with dictionaries keyed by normalized names."""

    def __init__(self) -> None:
        self.similar: dict[tuple[str, str], list[LastFmTrack]] = {}
        self.search: dict[tuple[str, str], LastFmTrack] = {}
        self.track_tag_map: dict[tuple[str, str], list[LastFmTag]] = {}
        self.artist_tag_map: dict[str, list[LastFmTag]] = {}
        self.similar_artist_map: dict[str, list[LastFmArtist]] = {}
        self.top_tracks: dict[str, list[LastFmTrack]] = {}
        self.tag_tracks: dict[str, list[LastFmTrack]] = {}
        self.failing_tags: set[tuple[str, str]] = set()
        self.calls: list[tuple] = []
        self.configured = True

    @staticmethod
    def _key(artist: str, title: str) -> tuple[str, str]:
        return normalize(artist), normalize(title)

    async def similar_tracks(self, artist: str, title: str, limit: int = 50) -> list[LastFmTrack]:
        self.calls.append(("similar_tracks", artist, title))
        return self.similar.get(self._key(artist, title), [])[:limit]

    async def search_track(self, artist: str, title: str) -> LastFmTrack | None:
        self.calls.append(("search_track", artist, title))
        return self.search.get(self._key(artist, title))

    async def track_tags(self, artist: str, title: str) -> list[LastFmTag]:
        self.calls.append(("track_tags", artist, title))
        if self._key(artist, title) in self.failing_tags:
            raise ProviderUnavailableError("temporary")
        return self.track_tag_map.get(self._key(artist, title), [])

    async def artist_tags(self, artist: str) -> list[LastFmTag]:
        self.calls.append(("artist_tags", artist))
        return self.artist_tag_map.get(normalize(artist), [])

    async def similar_artists(self, artist: str, limit: int = 10) -> list[LastFmArtist]:
        self.calls.append(("similar_artists", artist))
        return self.similar_artist_map.get(normalize(artist), [])[:limit]

    async def artist_top_tracks(self, artist: str, limit: int = 5) -> list[LastFmTrack]:
        self.calls.append(("artist_top_tracks", artist))
        return self.top_tracks.get(normalize(artist), [])[:limit]

    async def tag_top_tracks(self, tag: str, limit: int = 20) -> list[LastFmTrack]:
        self.calls.append(("tag_top_tracks", tag))
        return self.tag_tracks.get(tag, [])[:limit]


def m83_world() -> FakeLastFm:
    """Last.fm data around M83 – Midnight City."""
    fm = FakeLastFm()
    seed = ("m83", "midnight city")
    fm.similar[seed] = [
        track("Wait", "M83", 1.0),  # same artist: excluded
        track("Midnight City (Eric Prydz Remix)", "M83", 0.95),  # version of the seed
        track("Midnight City", "Karaoke Masters", 0.9),  # cover/filler
        track("Kids", "MGMT", 0.82),
        track("Electric Feel", "MGMT", 0.80),
        track("Time to Pretend", "MGMT", 0.78),  # third MGMT track: over the per-artist cap
        track("Intro", "The xx", 0.70),
        track("Sun Models", "ODESZA", 0.65),
    ]
    fm.track_tag_map[seed] = tags(("electronic", 100), ("seen live", 90), ("dream pop", 70), ("m83", 40), ("2011", 30), ("synthpop", 25), ("favourites", 10), ("chillwave", 3))
    fm.similar_artist_map["m83"] = [
        LastFmArtist(name="MGMT", match=0.9),
        LastFmArtist(name="Washed Out", match=0.75),
        LastFmArtist(name="M83", match=1.0),
    ]
    fm.top_tracks["washed out"] = [track("Feel It All Around", "Washed Out"), track("Amor Fati", "Washed Out")]
    fm.top_tracks["mgmt"] = [track("Kids", "MGMT")]
    fm.tag_tracks["electronic"] = [track("Strobe", "deadmau5"), track("Intro", "The xx")]
    fm.tag_tracks["dream pop"] = [track("Space Song", "Beach House")]
    fm.track_tag_map[("mgmt", "kids")] = tags(("indie", 100), ("electronic", 80), ("synthpop", 60))
    fm.artist_tag_map["washed out"] = tags(("chillwave", 100), ("dream pop", 70))
    fm.track_tag_map[("beach house", "space song")] = tags(("dream pop", 100), ("shoegaze", 50))
    return fm
