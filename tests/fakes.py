"""Deterministic stand-ins for external metadata providers."""

from app.schemas.song import SongMetadata
from app.services.link_parser import Platform

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
YOUTUBE_ID = "dQw4w9WgXcQ"


def spotify_song(external_id: str = SPOTIFY_ID) -> SongMetadata:
    return SongMetadata(
        platform=Platform.SPOTIFY,
        external_id=external_id,
        title="Midnight City",
        artist="M83",
        album="Hurry Up, We're Dreaming",
        artwork_url="https://i.scdn.co/image/large",
        url=f"https://open.spotify.com/track/{external_id}",
        attributes={"artists": ["M83"], "release_date": "2011-10-18"},
    )


class FakeProvider:
    def __init__(self, platform: Platform, song: SongMetadata | None = None, error: Exception | None = None) -> None:
        self.platform = platform
        self.song = song
        self.error = error
        self.calls: list[str] = []

    async def get_song(self, external_id: str) -> SongMetadata:
        self.calls.append(external_id)
        if self.error is not None:
            raise self.error
        assert self.song is not None
        return self.song
