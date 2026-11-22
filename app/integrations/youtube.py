"""YouTube Data API v3 integration (videos.list, 1 quota unit per call).

YouTube has no structured song fields, so the artist and title are read from the
video title ("Artist - Title") or, for auto-generated "Artist - Topic" channels,
from the channel name. The raw title and channel are kept in `attributes` so the
derivation can be checked later.
"""

import re

import httpx
from pydantic import BaseModel, ValidationError

from app.errors import (
    ProviderNotConfiguredError,
    ProviderUnavailableError,
    RateLimitedError,
    SongNotFoundError,
)
from app.integrations.http import https_url, send
from app.schemas.song import SongMetadata
from app.services.link_parser import Platform

API_URL = "https://www.googleapis.com/youtube/v3/videos"
PROVIDER = "YouTube"
MUSIC_CATEGORY_ID = "10"

TITLE_SEPARATOR = re.compile(r"\s+[-–—]\s+")
# Bracketed or trailing marketing labels such as "(Official Video)" or "[Lyrics]".
TITLE_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|remaster(ed)?|mv)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
TOPIC_SUFFIX = re.compile(r"\s+-\s+Topic$", re.IGNORECASE)
CHANNEL_SUFFIX = re.compile(r"(VEVO|\s+Official)$", re.IGNORECASE)
ISO_DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
QUOTA_REASONS = {"quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded"}
KEY_REASONS = {
    "keyInvalid",
    "keyExpired",
    "accessNotConfigured",
    "forbidden",
    "ipRefererBlocked",
    # Google's newer error format reports the precise cause in
    # error.details[].reason (errors[].reason is just "badRequest").
    "API_KEY_INVALID",
    "API_KEY_EXPIRED",
    "API_KEY_SERVICE_BLOCKED",
    "API_KEY_HTTP_REFERRER_BLOCKED",
    "API_KEY_IP_ADDRESS_BLOCKED",
    "API_KEY_ANDROID_APP_BLOCKED",
    "API_KEY_IOS_APP_BLOCKED",
    "SERVICE_DISABLED",
}


class _Thumbnail(BaseModel):
    url: str
    width: int | None = None


class _Snippet(BaseModel):
    title: str
    channelTitle: str
    publishedAt: str | None = None
    categoryId: str | None = None
    tags: list[str] = []
    defaultAudioLanguage: str | None = None
    thumbnails: dict[str, _Thumbnail] = {}


class _ContentDetails(BaseModel):
    duration: str | None = None


class _Video(BaseModel):
    id: str
    snippet: _Snippet
    contentDetails: _ContentDetails = _ContentDetails()


class _VideoList(BaseModel):
    items: list[_Video]


class YouTubeClient:
    platform = Platform.YOUTUBE

    def __init__(self, http: httpx.AsyncClient, api_key: str | None) -> None:
        self._http = http
        self._api_key = api_key

    async def get_song(self, video_id: str) -> SongMetadata:
        if not self._api_key:
            raise ProviderNotConfiguredError(
                "YouTube links aren't available: the server has no YouTube API key configured."
            )
        request = self._http.build_request(
            "GET",
            API_URL,
            params={"part": "snippet,contentDetails", "id": video_id, "key": self._api_key},
        )
        response = await send(self._http, request, PROVIDER)
        if response.status_code != 200:
            _raise_for_error(response)

        try:
            videos = _VideoList.model_validate(response.json()).items
            if not videos:
                raise SongNotFoundError("YouTube couldn't find that video. It may be private or removed.")
            return _to_song(videos[0])
        except (ValidationError, ValueError) as exc:
            raise ProviderUnavailableError("YouTube returned video data in an unexpected format.") from exc


def _raise_for_error(response: httpx.Response) -> None:
    reasons = _error_reasons(response)
    if reasons & QUOTA_REASONS:
        raise RateLimitedError("The YouTube API quota has been used up. Please try again later.")
    if reasons & KEY_REASONS or response.status_code in (401, 403):
        raise ProviderNotConfiguredError("YouTube rejected the server's API key.")
    if response.status_code in (400, 404):
        raise SongNotFoundError("YouTube couldn't find that video.")
    raise ProviderUnavailableError(f"YouTube returned an unexpected error ({response.status_code}).")


def _error_reasons(response: httpx.Response) -> set[str]:
    """Error reasons from both `error.errors[]` and `error.details[]`."""
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return set()
    if not isinstance(error, dict):
        return set()
    reasons: set[str] = set()
    for key in ("errors", "details"):
        items = error.get(key)
        if isinstance(items, list):
            reasons.update(str(item["reason"]) for item in items if isinstance(item, dict) and item.get("reason"))
    return reasons


def _to_song(video: _Video) -> SongMetadata:
    snippet = video.snippet
    artist, title = split_artist_and_title(snippet.title, snippet.channelTitle)

    attributes: dict = {
        "raw_title": snippet.title,
        "channel_title": snippet.channelTitle,
        "is_music_category": snippet.categoryId == MUSIC_CATEGORY_ID,
    }
    optional = {
        "tags": snippet.tags or None,
        "language": snippet.defaultAudioLanguage,
        "published_at": snippet.publishedAt,
        "duration_ms": _duration_ms(video.contentDetails.duration),
    }
    attributes.update({key: value for key, value in optional.items() if value is not None})

    return SongMetadata(
        platform=Platform.YOUTUBE,
        external_id=video.id,
        title=title,
        artist=artist,
        album=None,
        artwork_url=_best_thumbnail(snippet.thumbnails),
        url=f"https://www.youtube.com/watch?v={video.id}",
        attributes=attributes,
    )


def split_artist_and_title(video_title: str, channel_title: str) -> tuple[str, str]:
    """Return (artist, title) using only the video title and channel name."""
    title = TITLE_NOISE.sub("", video_title).strip() or video_title.strip()

    topic_match = TOPIC_SUFFIX.search(channel_title)
    if topic_match:
        return channel_title[: topic_match.start()].strip(), title

    parts = TITLE_SEPARATOR.split(title, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()

    artist = CHANNEL_SUFFIX.sub("", channel_title).strip() or channel_title.strip()
    return artist, title


def _best_thumbnail(thumbnails: dict[str, _Thumbnail]) -> str | None:
    if not thumbnails:
        return None
    return https_url(max(thumbnails.values(), key=lambda thumbnail: thumbnail.width or 0).url)


def _duration_ms(value: str | None) -> int | None:
    if not value:
        return None
    match = ISO_DURATION.match(value)
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return (((days * 24 + hours) * 60 + minutes) * 60 + seconds) * 1000
