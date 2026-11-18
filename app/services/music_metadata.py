"""Turn a song link into song metadata, using the database as a cache."""

from collections.abc import Iterable
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import UnsupportedPlatformError
from app.models import Song
from app.schemas.song import SongMetadata
from app.services.link_parser import ParsedSongLink, Platform, parse_song_url


class MetadataProvider(Protocol):
    platform: Platform

    async def get_song(self, external_id: str) -> SongMetadata: ...


class MusicMetadataService:
    def __init__(self, providers: Iterable[MetadataProvider], db: Session) -> None:
        self._providers = {provider.platform: provider for provider in providers}
        self._db = db

    async def lookup(self, url: str) -> SongMetadata:
        link = parse_song_url(url)

        cached = self._find(link.platform, link.external_id)
        if cached is not None:
            return SongMetadata.model_validate(cached)

        provider = self._providers.get(link.platform)
        if provider is None:
            raise UnsupportedPlatformError(f"{link.platform.value.title()} links aren't supported yet.")

        song = await provider.get_song(link.external_id)
        self._store(song, link)
        return song

    def _find(self, platform: Platform, external_id: str) -> Song | None:
        return self._db.scalars(
            select(Song).where(Song.platform == platform.value, Song.external_id == external_id)
        ).first()

    def _store(self, song: SongMetadata, link: ParsedSongLink) -> None:
        # Cache under the identifier from the submitted link so repeat lookups hit the cache,
        # even if the provider answered with a different (relinked) identifier.
        if self._find(link.platform, link.external_id) is not None:
            return
        self._db.add(
            Song(
                title=song.title,
                artist=song.artist,
                album=song.album,
                artwork_url=song.artwork_url,
                platform=link.platform.value,
                external_id=link.external_id,
                url=song.url,
                attributes=song.attributes,
            )
        )
        try:
            self._db.commit()
        except IntegrityError:
            # A concurrent request cached the same song first; its row is equivalent.
            self._db.rollback()
