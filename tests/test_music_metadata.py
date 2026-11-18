import pytest
from sqlalchemy.orm import Session

from app.errors import InvalidSongUrlError, ProviderUnavailableError, UnsupportedPlatformError
from app.models import Song
from app.services.link_parser import Platform
from app.services.music_metadata import MusicMetadataService
from tests.fakes import SPOTIFY_ID, YOUTUBE_ID, FakeProvider, spotify_song

pytestmark = pytest.mark.anyio

SPOTIFY_URL = f"https://open.spotify.com/track/{SPOTIFY_ID}?si=abc"


async def test_lookup_fetches_and_caches_song(db_session: Session) -> None:
    provider = FakeProvider(Platform.SPOTIFY, spotify_song())
    service = MusicMetadataService([provider], db_session)

    song = await service.lookup(SPOTIFY_URL)

    assert song == spotify_song()
    assert provider.calls == [SPOTIFY_ID]
    stored = db_session.query(Song).one()
    assert (stored.platform, stored.external_id, stored.title) == ("spotify", SPOTIFY_ID, "Midnight City")
    assert stored.attributes == {"artists": ["M83"], "release_date": "2011-10-18"}


async def test_second_lookup_uses_cache(db_session: Session) -> None:
    provider = FakeProvider(Platform.SPOTIFY, spotify_song())
    service = MusicMetadataService([provider], db_session)

    first = await service.lookup(SPOTIFY_URL)
    second = await service.lookup(f"spotify:track:{SPOTIFY_ID}")

    assert second == first
    assert provider.calls == [SPOTIFY_ID]
    assert db_session.query(Song).count() == 1


async def test_relinked_track_is_cached_under_submitted_id(db_session: Session) -> None:
    provider = FakeProvider(Platform.SPOTIFY, spotify_song(external_id="6habFhsOp2NvshLv26DqMb"))
    service = MusicMetadataService([provider], db_session)

    await service.lookup(SPOTIFY_URL)
    await service.lookup(SPOTIFY_URL)

    assert provider.calls == [SPOTIFY_ID]
    assert db_session.query(Song).one().external_id == SPOTIFY_ID


async def test_provider_errors_are_not_cached(db_session: Session) -> None:
    provider = FakeProvider(Platform.SPOTIFY, error=ProviderUnavailableError("down"))
    service = MusicMetadataService([provider], db_session)

    with pytest.raises(ProviderUnavailableError):
        await service.lookup(SPOTIFY_URL)
    assert db_session.query(Song).count() == 0


async def test_invalid_url_never_reaches_provider(db_session: Session) -> None:
    provider = FakeProvider(Platform.SPOTIFY, spotify_song())
    service = MusicMetadataService([provider], db_session)

    with pytest.raises(InvalidSongUrlError):
        await service.lookup("not a link")
    assert provider.calls == []


async def test_platform_without_provider_is_unsupported(db_session: Session) -> None:
    service = MusicMetadataService([FakeProvider(Platform.SPOTIFY, spotify_song())], db_session)

    with pytest.raises(UnsupportedPlatformError):
        await service.lookup(f"https://youtu.be/{YOUTUBE_ID}")
