"""Artwork and listening links for recommended songs.

Recommendations come from Last.fm, which links to its own pages and no
longer provides album artwork. Each recommended song is therefore looked up
on Spotify by title and artist; when Spotify returns the same song (checked
by `SpotifyClient.search_track`), its album artwork and Spotify link are
stored with the song. Songs Spotify doesn't have keep their Last.fm link and
are shown without artwork. Nothing is guessed.

Lookups are best effort: a failing or unconfigured Spotify never stops
recommendations from being shown. Results are stored in `Song.attributes`
so each song is searched once; songs not found are searched again after
`RECHECK_AFTER`.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.errors import ProviderNotConfiguredError, SongLookupError
from app.models import Song
from app.schemas.song import SongMetadata

logger = logging.getLogger(__name__)

#: How long a "not on Spotify" result is trusted before searching again.
RECHECK_AFTER = timedelta(days=7)
#: Searches run at the same time (Spotify rate limits per app).
CONCURRENCY = 4


class TrackSearcher(Protocol):
    platform: object

    async def search_track(self, title: str, artist: str) -> SongMetadata | None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ListeningLinkService:
    def __init__(self, searcher: TrackSearcher, concurrency: int = CONCURRENCY) -> None:
        self._searcher = searcher
        self._limit = asyncio.Semaphore(concurrency)

    def _needs_lookup(self, song: Song) -> bool:
        listen = (song.attributes or {}).get("listen")
        if not listen:
            return True
        if listen.get("url"):
            return False
        try:
            checked = datetime.fromisoformat(listen.get("checked_at", ""))
        except ValueError:
            return True
        return _now() - checked > RECHECK_AFTER

    async def enrich(self, songs: list[Song]) -> None:
        """Looks the songs up and records what was found on each `Song`."""
        pending = [song for song in songs if self._needs_lookup(song)]
        if not pending:
            return
        results = await asyncio.gather(*(self._search(song) for song in pending), return_exceptions=True)
        for song, result in zip(pending, results, strict=True):
            if isinstance(result, ProviderNotConfiguredError):
                logger.info("Spotify is not configured; recommendations are shown without artwork")
                return
            if isinstance(result, SongLookupError):
                # Leave the song unchecked so a later request tries again.
                logger.warning("Spotify lookup failed for a recommended song: %s", result.message)
                continue
            if isinstance(result, BaseException):
                logger.exception("Unexpected error looking up a recommended song", exc_info=result)
                continue
            self._record(song, result)

    async def _search(self, song: Song) -> SongMetadata | None:
        async with self._limit:
            return await self._searcher.search_track(song.title, song.artist)

    @staticmethod
    def _record(song: Song, match: SongMetadata | None) -> None:
        listen: dict = {"checked_at": _now().isoformat()}
        if match is not None:
            listen.update({"platform": str(match.platform.value), "url": match.url, "external_id": match.external_id})
            if match.artwork_url:
                song.artwork_url = match.artwork_url
            if match.album and not song.album:
                song.album = match.album
        # Assign a new dict so SQLAlchemy sees the JSON column change.
        song.attributes = {**(song.attributes or {}), "listen": listen}
