"""Candidate songs found by music discovery."""

from pydantic import BaseModel, Field


class Tag(BaseModel):
    """A Last.fm community tag (genre, mood, style, era, …)."""

    name: str
    #: How strongly the tag applies, relative to the song's top tag (0-100).
    weight: int = Field(ge=0, le=100)


class Seed(BaseModel):
    """The submitted song as identified on Last.fm."""

    title: str
    artist: str
    found_on_lastfm: bool
    tags: list[Tag] = Field(default_factory=list)


class Candidate(BaseModel):
    title: str
    artist: str
    url: str | None = None
    mbid: str | None = None
    #: How the candidate was found: `similar_track`, `similar_artist` or `tag:<name>`.
    sources: list[str] = Field(default_factory=list)
    #: Last.fm track-to-track similarity to the seed (0-1), if listed as similar.
    track_match: float | None = Field(default=None, ge=0, le=1)
    #: Last.fm similarity of the candidate's artist to the seed's artist (0-1).
    artist_match: float | None = Field(default=None, ge=0, le=1)
    tags: list[Tag] = Field(default_factory=list)


class DiscoveryResult(BaseModel):
    seed: Seed
    candidates: list[Candidate]
