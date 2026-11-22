"""Find candidate songs similar to a seed song, using Last.fm.

Candidates come from three sources, so that discovery is not limited to one
artist or one genre label:

1. **Similar tracks** — Last.fm's listening-based track similarity.
2. **Similar artists** — top tracks of artists Last.fm considers similar.
3. **Shared tags** — top tracks for the seed's strongest genre/style tags.

They are then filtered (no versions or covers of the seed, no tracks by the
seed's own artist, no karaoke/tribute filler, at most a few tracks per
artist), ordered, trimmed, and enriched with their Last.fm tags so that the
ranking step can compare genre, mood and style.

Nothing here is inferred: every field comes from Last.fm.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.errors import NoCandidatesError, ProviderUnavailableError, RateLimitedError, SongLookupError
from app.integrations.lastfm import LastFmClient, LastFmTag, LastFmTrack
from app.schemas.discovery import Candidate, DiscoveryResult, Seed, Tag
from app.schemas.song import SongMetadata
from app.services.text import base_title, clean_title, is_filler, normalize, primary_artist, song_key

logger = logging.getLogger(__name__)

#: Tags that describe listeners' habits rather than the music.
NON_DESCRIPTIVE_TAGS = {
    "seen live", "favorites", "favourites", "favorite", "favourite", "my favorite", "love", "loved", "awesome",
    "beautiful", "amazing", "cool", "good", "great", "best", "albums i own", "spotify", "youtube", "music",
    "under 2000 listeners", "check out", "to listen", "fav", "favs", "songs", "all", "favorite songs",
}


@dataclass(frozen=True)
class DiscoverySettings:
    similar_track_limit: int = 50
    similar_artist_limit: int = 8
    tracks_per_similar_artist: int = 2
    seed_tags_used: int = 2
    tracks_per_tag: int = 15
    max_per_artist: int = 2
    max_candidates: int = 25
    tags_per_song: int = 10
    min_tag_weight: int = 5


class MusicDiscoveryService:
    def __init__(self, lastfm: LastFmClient, settings: DiscoverySettings | None = None) -> None:
        self._lastfm = lastfm
        self._settings = settings or DiscoverySettings()

    async def discover(self, song: SongMetadata) -> DiscoveryResult:
        s = self._settings
        artist = seed_artist(song)
        title = clean_title(song.title)

        similar = await self._lastfm.similar_tracks(artist, title, s.similar_track_limit)
        found = bool(similar)
        if not similar:
            match = await self._lastfm.search_track(artist, title)
            if match is not None:
                artist, title = match.artist, match.title
                similar = await self._lastfm.similar_tracks(artist, title, s.similar_track_limit)
                found = True

        seed_tags = await self._song_tags(artist, title)
        found = found or bool(seed_tags)
        seed = Seed(title=title, artist=artist, found_on_lastfm=found, tags=seed_tags)

        pool = CandidatePool(seed_artist=artist, seed_title=title)
        for track in similar:
            pool.add(track, "similar_track", track_match=track.match)

        # The remaining sources only add candidates: if one lookup fails, the
        # others are still used. If nothing at all is found, the error is
        # reported instead of "no similar songs".
        failures: list[SongLookupError] = []
        similar_artists = await _optional(self._lastfm.similar_artists(artist, s.similar_artist_limit), [], failures)
        top_tracks = await asyncio.gather(
            *(_optional(self._lastfm.artist_top_tracks(a.name, s.tracks_per_similar_artist), [], failures) for a in similar_artists)
        )
        for similar_artist, tracks in zip(similar_artists, top_tracks):
            for track in tracks:
                pool.add(track, "similar_artist", artist_match=similar_artist.match)

        for tag in strongest(seed_tags)[: s.seed_tags_used]:
            for track in await _optional(self._lastfm.tag_top_tracks(tag.name, s.tracks_per_tag), [], failures):
                pool.add(track, f"tag:{tag.name}")

        # Artist similarity also applies to tracks found through other sources.
        artist_matches = {normalize(a.name): a.match for a in similar_artists if a.match is not None}
        pool.apply_artist_matches(artist_matches)

        candidates = pool.select(s.max_per_artist, s.max_candidates)
        if not candidates and failures:
            raise failures[0]
        if not candidates:
            raise NoCandidatesError(
                f"Couldn't find songs similar to “{song.title}” by {song.artist} on Last.fm. "
                "Try a more widely known song."
            )
        await self._add_tags(candidates)
        return DiscoveryResult(seed=seed, candidates=candidates)

    async def _song_tags(self, artist: str, title: str) -> list[Tag]:
        tags = await self._lastfm.track_tags(artist, title)
        if not tags:
            tags = await self._lastfm.artist_tags(artist)
        return self._clean_tags(tags, artist)

    def _clean_tags(self, tags: list[LastFmTag], artist: str) -> list[Tag]:
        s = self._settings
        artist_key = normalize(artist)
        kept: list[Tag] = []
        for tag in tags:
            name = tag.name.strip()
            if (
                tag.count < s.min_tag_weight
                or name in NON_DESCRIPTIVE_TAGS
                or normalize(name) == artist_key
                or name.isdigit()
                or len(name) > 40
            ):
                continue
            if all(t.name != name for t in kept):
                kept.append(Tag(name=name, weight=tag.count))
            if len(kept) >= s.tags_per_song:
                break
        return kept

    async def _add_tags(self, candidates: list[Candidate]) -> None:
        artist_tags: dict[str, list[Tag]] = {}

        async def enrich(candidate: Candidate) -> None:
            try:
                tags = self._clean_tags(await self._lastfm.track_tags(candidate.artist, candidate.title), candidate.artist)
                if not tags:
                    key = normalize(candidate.artist)
                    if key not in artist_tags:
                        artist_tags[key] = self._clean_tags(await self._lastfm.artist_tags(candidate.artist), candidate.artist)
                    tags = artist_tags[key]
            except (ProviderUnavailableError, RateLimitedError):
                # One failed lookup should not lose the whole discovery; the
                # candidate is ranked on the data that is available.
                tags = []
            candidate.tags = tags

        await asyncio.gather(*(enrich(c) for c in candidates))


def seed_artist(song: SongMetadata) -> str:
    """The main artist: the first in the provider's artist list when there is
    one (so "Earth, Wind & Fire" stays intact), otherwise the first credit."""
    artists = song.attributes.get("artists")
    if isinstance(artists, list) and artists and isinstance(artists[0], str) and artists[0].strip():
        return artists[0].strip()
    return primary_artist(song.artist)


def strongest(tags: list[Tag]) -> list[Tag]:
    """Cleaned tags, strongest first."""
    return sorted(tags, key=lambda t: -t.weight)


class CandidatePool:
    """Collects candidates from all sources, merging duplicates."""

    def __init__(self, seed_artist: str, seed_title: str) -> None:
        self._seed_artist = normalize(primary_artist(seed_artist))
        self._seed_title = base_title(seed_title)
        self._candidates: dict[tuple[str, str], Candidate] = {}

    def add(
        self,
        track: LastFmTrack,
        source: str,
        track_match: float | None = None,
        artist_match: float | None = None,
    ) -> None:
        artist_key, title_key = song_key(track.artist, track.title)
        if not artist_key or not title_key:
            return
        if artist_key == self._seed_artist or title_key == self._seed_title:
            return  # the seed itself, its versions and covers, or its own artist
        if is_filler(track.artist, track.title):
            return
        key = (artist_key, title_key)
        existing = self._candidates.get(key)
        if existing is None:
            self._candidates[key] = Candidate(
                title=track.title,
                artist=track.artist,
                url=track.url,
                mbid=track.mbid,
                sources=[source],
                track_match=track_match,
                artist_match=artist_match,
            )
            return
        if source not in existing.sources:
            existing.sources.append(source)
        existing.track_match = _max(existing.track_match, track_match)
        existing.artist_match = _max(existing.artist_match, artist_match)
        existing.url = existing.url or track.url
        existing.mbid = existing.mbid or track.mbid

    def apply_artist_matches(self, matches: dict[str, float]) -> None:
        for (artist_key, _), candidate in self._candidates.items():
            candidate.artist_match = _max(candidate.artist_match, matches.get(artist_key))

    def select(self, max_per_artist: int, limit: int) -> list[Candidate]:
        """Strongest candidates first, with at most `max_per_artist` per artist."""
        ordered = sorted(self._candidates.items(), key=lambda item: -prior(item[1]))
        per_artist: dict[str, int] = {}
        selected: list[Candidate] = []
        for (artist_key, _), candidate in ordered:
            if per_artist.get(artist_key, 0) >= max_per_artist:
                continue
            per_artist[artist_key] = per_artist.get(artist_key, 0) + 1
            selected.append(candidate)
            if len(selected) >= limit:
                break
        return selected


def prior(candidate: Candidate) -> float:
    """Ordering used only to decide which candidates go on to ranking.

    Direct track similarity is the strongest signal; artist similarity counts
    for a little less; tag-chart membership is a weak signal on its own.
    Being found by several sources adds a small bonus.
    """
    signals = [
        candidate.track_match or 0.0,
        0.8 * (candidate.artist_match or 0.0),
        0.35 if any(s.startswith("tag:") for s in candidate.sources) else 0.0,
    ]
    kinds = {s.split(":")[0] for s in candidate.sources}
    return max(signals) + 0.05 * (len(kinds) - 1)


def _max(a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


async def _optional(coroutine, default, failures: list):
    """The coroutine's result, or `default` if the provider is unavailable
    or rate limited (the error is recorded in `failures`)."""
    try:
        return await coroutine
    except (ProviderUnavailableError, RateLimitedError) as exc:
        logger.warning("Optional Last.fm lookup failed: %s", exc.message)
        failures.append(exc)
        return default
