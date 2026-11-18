import pytest

from app.services.errors import InvalidSongUrlError, UnsupportedPlatformError
from app.services.link_parser import ParsedSongLink, Platform, parse_song_url

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
YOUTUBE_ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://open.spotify.com/track/{SPOTIFY_ID}",
        f"https://open.spotify.com/track/{SPOTIFY_ID}?si=abcdef123456",
        f"http://open.spotify.com/track/{SPOTIFY_ID}/",
        f"open.spotify.com/track/{SPOTIFY_ID}",
        f"  https://open.spotify.com/track/{SPOTIFY_ID}  ",
        f"https://OPEN.SPOTIFY.COM/track/{SPOTIFY_ID}",
        f"https://open.spotify.com/intl-de/track/{SPOTIFY_ID}",
        f"https://open.spotify.com/intl-pt-br/track/{SPOTIFY_ID}",
        f"https://open.spotify.com/embed/track/{SPOTIFY_ID}",
        f"https://play.spotify.com/track/{SPOTIFY_ID}",
        f"spotify:track:{SPOTIFY_ID}",
    ],
)
def test_spotify_track_links(url: str) -> None:
    assert parse_song_url(url) == ParsedSongLink(Platform.SPOTIFY, SPOTIFY_ID)


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}",
        f"https://youtube.com/watch?v={YOUTUBE_ID}&t=42s",
        f"https://www.youtube.com/watch?list=PL123&v={YOUTUBE_ID}",
        f"https://m.youtube.com/watch?v={YOUTUBE_ID}",
        f"https://music.youtube.com/watch?v={YOUTUBE_ID}&feature=share",
        f"https://youtu.be/{YOUTUBE_ID}",
        f"https://youtu.be/{YOUTUBE_ID}?si=xyz",
        f"https://www.youtube.com/shorts/{YOUTUBE_ID}",
        f"https://www.youtube.com/embed/{YOUTUBE_ID}",
        f"https://www.youtube.com/live/{YOUTUBE_ID}",
        f"https://www.youtube-nocookie.com/embed/{YOUTUBE_ID}",
        f"youtu.be/{YOUTUBE_ID}",
    ],
)
def test_youtube_video_links(url: str) -> None:
    assert parse_song_url(url) == ParsedSongLink(Platform.YOUTUBE, YOUTUBE_ID)


def test_canonical_urls() -> None:
    assert ParsedSongLink(Platform.SPOTIFY, SPOTIFY_ID).canonical_url == (
        f"https://open.spotify.com/track/{SPOTIFY_ID}"
    )
    assert ParsedSongLink(Platform.YOUTUBE, YOUTUBE_ID).canonical_url == (
        f"https://www.youtube.com/watch?v={YOUTUBE_ID}"
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "ftp://open.spotify.com/track/" + SPOTIFY_ID,
        "javascript:alert(1)",
        f"https://user:pass@open.spotify.com/track/{SPOTIFY_ID}",
        "https://open.spotify.com/track/short",
        "https://open.spotify.com/track/" + "a" * 23,
        "https://open.spotify.com/",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/watch?v=tooshort",
        "https://www.youtube.com/watch?v=" + YOUTUBE_ID + "x",
        "https://youtu.be/",
        "https://www.youtube.com/",
        "https://www.youtube.com/embed/bad id here",
        "https://open.spotify.com/track/" + SPOTIFY_ID + "x" * 3000,
        "spotify:track:short",
    ],
)
def test_invalid_links(url: str) -> None:
    with pytest.raises(InvalidSongUrlError):
        parse_song_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://soundcloud.com/artist/song",
        "https://music.apple.com/us/album/x/123?i=456",
        "https://evil.example/open.spotify.com/track/" + SPOTIFY_ID,
        "https://open.spotify.com.evil.example/track/" + SPOTIFY_ID,
        "https://127.0.0.1/track/" + SPOTIFY_ID,
        "https://spotify.link/AbCdEf",
        "https://open.spotify.com/album/" + SPOTIFY_ID,
        "https://open.spotify.com/playlist/" + SPOTIFY_ID,
        "https://open.spotify.com/artist/" + SPOTIFY_ID,
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/channel/UC123",
    ],
)
def test_unsupported_links(url: str) -> None:
    with pytest.raises(UnsupportedPlatformError):
        parse_song_url(url)


def test_short_spotify_link_explains_workaround() -> None:
    with pytest.raises(UnsupportedPlatformError, match="open.spotify.com/track"):
        parse_song_url("https://spotify.link/AbCdEf")
