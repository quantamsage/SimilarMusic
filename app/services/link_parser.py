"""Validate song links and extract the platform and track identifier.

Only known hosts are accepted and the URL is never fetched, so user input cannot
make the server request arbitrary addresses.
"""

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, urlsplit

from app.errors import InvalidSongUrlError, UnsupportedPlatformError

MAX_URL_LENGTH = 2048

SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{22}$")
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
SPOTIFY_URI = re.compile(r"^spotify:track:([A-Za-z0-9]{22})$")
BARE_DOMAIN_LINK = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/|$)")
HOSTNAME = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
SPOTIFY_LOCALE_SEGMENT = re.compile(r"^intl-[a-z]{2}(-[a-z]{2})?$", re.IGNORECASE)

SPOTIFY_HOSTS = {"open.spotify.com", "play.spotify.com"}
SPOTIFY_SHORT_HOSTS = {"spotify.link", "spotify.app.link"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
YOUTUBE_NOCOOKIE_HOSTS = {"youtube-nocookie.com", "www.youtube-nocookie.com"}
YOUTUBE_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
YOUTUBE_PATH_PREFIXES = ("shorts", "embed", "live", "v")


class Platform(str, Enum):
    SPOTIFY = "spotify"
    YOUTUBE = "youtube"


@dataclass(frozen=True)
class ParsedSongLink:
    platform: Platform
    external_id: str

    @property
    def canonical_url(self) -> str:
        if self.platform is Platform.SPOTIFY:
            return f"https://open.spotify.com/track/{self.external_id}"
        return f"https://www.youtube.com/watch?v={self.external_id}"


def parse_song_url(raw_url: str) -> ParsedSongLink:
    url = raw_url.strip()
    if not url:
        raise InvalidSongUrlError("Please paste a song link.")
    if len(url) > MAX_URL_LENGTH:
        raise InvalidSongUrlError("That link is too long to be a song link.")

    uri_match = SPOTIFY_URI.match(url)
    if uri_match:
        return ParsedSongLink(Platform.SPOTIFY, uri_match.group(1))

    if url.lower().startswith("spotify:"):
        raise InvalidSongUrlError("That Spotify URI doesn't point to a valid track.")
    if "://" not in url:
        if not BARE_DOMAIN_LINK.match(url):
            raise InvalidSongUrlError("That doesn't look like a valid link.")
        url = f"https://{url}"

    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise InvalidSongUrlError("That doesn't look like a valid link.") from exc

    if parts.scheme.lower() not in ("http", "https") or not HOSTNAME.match(host) or parts.username or parts.password:
        raise InvalidSongUrlError("That doesn't look like a valid link.")

    segments = [segment for segment in parts.path.split("/") if segment]

    if host in SPOTIFY_HOSTS:
        return _parse_spotify(segments)
    if host in SPOTIFY_SHORT_HOSTS:
        raise UnsupportedPlatformError(
            "Shortened spotify.link links aren't supported. Open the link and copy the full "
            "open.spotify.com/track/… address instead."
        )
    if host in YOUTUBE_HOSTS or host in YOUTUBE_NOCOOKIE_HOSTS:
        return _parse_youtube(segments, parts.query)
    if host in YOUTUBE_SHORT_HOSTS:
        return _youtube_link(segments[0] if segments else "")

    raise UnsupportedPlatformError("Only Spotify and YouTube song links are supported right now.")


def _parse_spotify(segments: list[str]) -> ParsedSongLink:
    if segments and SPOTIFY_LOCALE_SEGMENT.match(segments[0]):
        segments = segments[1:]
    if segments and segments[0] == "embed":
        segments = segments[1:]

    if len(segments) >= 2 and segments[0] == "track":
        if SPOTIFY_ID.match(segments[1]):
            return ParsedSongLink(Platform.SPOTIFY, segments[1])
        raise InvalidSongUrlError("That Spotify link has an invalid track ID.")

    if segments and segments[0] in ("album", "playlist", "artist", "show", "episode"):
        raise UnsupportedPlatformError(
            f"That's a Spotify {segments[0]} link. Please paste a link to a single song."
        )
    raise InvalidSongUrlError("That Spotify link doesn't point to a song.")


def _parse_youtube(segments: list[str], query: str) -> ParsedSongLink:
    if segments == ["watch"]:
        video_ids = parse_qs(query).get("v", [])
        return _youtube_link(video_ids[0] if video_ids else "")
    if len(segments) >= 2 and segments[0] in YOUTUBE_PATH_PREFIXES:
        return _youtube_link(segments[1])
    if segments and (segments[0] in ("playlist", "channel", "c", "user") or segments[0].startswith("@")):
        raise UnsupportedPlatformError("Please paste a link to a single YouTube video, not a playlist or channel.")
    raise InvalidSongUrlError("That YouTube link doesn't point to a video.")


def _youtube_link(video_id: str) -> ParsedSongLink:
    if not YOUTUBE_ID.match(video_id):
        raise InvalidSongUrlError("That YouTube link has an invalid video ID.")
    return ParsedSongLink(Platform.YOUTUBE, video_id)
