"""Spotify Web API integration (Client Credentials flow, track metadata only).

Uses GET /v1/tracks/{id}, which remains available to Development Mode apps after
Spotify's February 2026 changes. Audio features and recommendations are not used:
those endpoints are unavailable to new apps.
"""

import base64
import time

import httpx
from pydantic import BaseModel, ValidationError

from app.errors import ProviderNotConfiguredError, ProviderUnavailableError, SongNotFoundError
from app.integrations.http import send
from app.schemas.song import SongMetadata
from app.services.link_parser import Platform

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
PROVIDER = "Spotify"
TOKEN_EXPIRY_MARGIN_SECONDS = 60


class _Image(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None


class _Artist(BaseModel):
    id: str | None = None
    name: str


class _Album(BaseModel):
    name: str
    images: list[_Image] = []
    release_date: str | None = None


class _ExternalUrls(BaseModel):
    spotify: str | None = None


class _ExternalIds(BaseModel):
    isrc: str | None = None


class _Track(BaseModel):
    id: str
    name: str
    artists: list[_Artist]
    album: _Album
    duration_ms: int | None = None
    explicit: bool | None = None
    external_urls: _ExternalUrls = _ExternalUrls()
    external_ids: _ExternalIds = _ExternalIds()


class _Token(BaseModel):
    access_token: str
    expires_in: int


class SpotifyClient:
    platform = Platform.SPOTIFY

    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str | None,
        client_secret: str | None,
        market: str = "US",
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._market = market
        self._token: str | None = None
        self._token_expires_at = 0.0

    async def get_song(self, track_id: str) -> SongMetadata:
        response = await self._get(f"{API_BASE}/tracks/{track_id}", {"market": self._market})
        if response.status_code in (400, 404):
            raise SongNotFoundError("Spotify couldn't find that song. Check the link and try again.")
        if response.status_code != 200:
            raise ProviderUnavailableError(f"Spotify returned an unexpected error ({response.status_code}).")

        try:
            track = _Track.model_validate(response.json())
            if not track.artists:
                raise SongNotFoundError("Spotify returned that song without an artist.")
            return _to_song(track)
        except (ValidationError, ValueError) as exc:
            raise ProviderUnavailableError("Spotify returned song data in an unexpected format.") from exc

    async def _get(self, url: str, params: dict[str, str]) -> httpx.Response:
        """GET with a bearer token, refreshing the token once if Spotify rejects it."""
        for attempt in range(2):
            token = await self._access_token(force_refresh=attempt > 0)
            request = self._http.build_request(
                "GET", url, params=params, headers={"Authorization": f"Bearer {token}"}
            )
            response = await send(self._http, request, PROVIDER)
            if response.status_code != 401:
                return response
        raise ProviderUnavailableError("Spotify rejected the app's access token.")

    async def _access_token(self, force_refresh: bool = False) -> str:
        if not self._client_id or not self._client_secret:
            raise ProviderNotConfiguredError(
                "Spotify links aren't available: the server has no Spotify API credentials configured."
            )
        if not force_refresh and self._token and time.monotonic() < self._token_expires_at:
            return self._token

        request = self._http.build_request(
            "POST",
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={"Authorization": f"Basic {_basic_credentials(self._client_id, self._client_secret)}"},
        )
        response = await send(self._http, request, PROVIDER)
        if response.status_code in (400, 401):
            raise ProviderNotConfiguredError("Spotify rejected the server's API credentials.")
        if response.status_code != 200:
            raise ProviderUnavailableError(f"Spotify authentication failed ({response.status_code}).")
        try:
            token = _Token.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ProviderUnavailableError("Spotify returned an unexpected authentication response.") from exc

        self._token = token.access_token
        self._token_expires_at = time.monotonic() + max(0, token.expires_in - TOKEN_EXPIRY_MARGIN_SECONDS)
        return self._token


def _basic_credentials(client_id: str, client_secret: str) -> str:
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def _to_song(track: _Track) -> SongMetadata:
    images = sorted(track.album.images, key=lambda image: image.width or 0, reverse=True)
    attributes: dict = {
        "artists": [artist.name for artist in track.artists],
        "artist_ids": [artist.id for artist in track.artists if artist.id],
    }
    optional = {
        "duration_ms": track.duration_ms,
        "explicit": track.explicit,
        "release_date": track.album.release_date,
        "isrc": track.external_ids.isrc,
    }
    attributes.update({key: value for key, value in optional.items() if value is not None})

    return SongMetadata(
        platform=Platform.SPOTIFY,
        external_id=track.id,
        title=track.name,
        artist=", ".join(artist.name for artist in track.artists),
        album=track.album.name or None,
        artwork_url=images[0].url if images else None,
        url=track.external_urls.spotify or f"https://open.spotify.com/track/{track.id}",
        attributes=attributes,
    )
