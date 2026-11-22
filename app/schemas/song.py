"""Validated song representations shared by integrations, services and the API."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.link_parser import Platform


class SongMetadata(BaseModel):
    """Song information exactly as retrieved from a provider; nothing here is inferred."""

    model_config = ConfigDict(from_attributes=True)

    platform: Platform
    external_id: str
    title: str = Field(min_length=1, max_length=300)
    artist: str = Field(min_length=1, max_length=300)
    album: str | None = Field(default=None, max_length=300)
    artwork_url: str | None = Field(default=None, max_length=1000)
    #: The song's page; None for a song found only on Last.fm without a page link.
    url: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class SongLookupRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ErrorResponse(BaseModel):
    error: str
    message: str
