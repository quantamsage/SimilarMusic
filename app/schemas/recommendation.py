"""Song analysis and recommendation results."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

#: Largest id SQLite can store (signed 64-bit).
MAX_ID = 2**63 - 1

from app.schemas.discovery import Tag
from app.schemas.song import SongMetadata

CHARACTERISTICS = (
    "genre",
    "subgenre",
    "mood",
    "style",
    "tempo",
    "energy",
    "danceability",
    "instrumentation",
    "vocals",
    "language",
)

RATED_DIMENSIONS = ("genre", "mood", "style", "energy", "instrumentation", "vocals")


class Characteristic(BaseModel):
    value: str
    #: The retrieved facts that support the value (shown to the user).
    evidence: list[str] = Field(default_factory=list)


class SongAnalysis(BaseModel):
    """Characteristics of the submitted song. Only characteristics supported
    by retrieved metadata are present; the rest are listed as unknown."""

    characteristics: dict[str, Characteristic] = Field(default_factory=dict)
    unknown: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Components of the similarity score, each 0-1 (None = no data)."""

    listening: float | None = Field(default=None, ge=0, le=1)
    tags: float | None = Field(default=None, ge=0, le=1)
    ai: float | None = Field(default=None, ge=0, le=1)


class Recommendation(BaseModel):
    rank: int
    song_id: int | None = None
    title: str
    artist: str
    platform: str
    url: str | None
    album: str | None = None
    #: Album artwork, when the song was found on a streaming platform.
    artwork_url: str | None = None
    #: Where to listen: the song on Spotify when it was matched there,
    #: otherwise its Last.fm page.
    listen_url: str | None = None
    listen_platform: str | None = None
    similarity_score: float = Field(ge=0, le=1)
    score_breakdown: ScoreBreakdown
    explanation: str
    #: `ai` when written by the AI, otherwise the data it was derived from.
    explanation_source: str
    tags: list[Tag] = Field(default_factory=list)
    found_via: list[str] = Field(default_factory=list)


class ScoringMethod(BaseModel):
    description: str
    weights: dict[str, float]


class RecommendationResponse(BaseModel):
    request_id: int
    created_at: datetime
    cached: bool
    song: SongMetadata
    song_id: int | None = None
    analysis: SongAnalysis
    recommendations: list[Recommendation]
    scoring: ScoringMethod


class RecommendationRequestBody(BaseModel):
    """Either a song link or a song the editor already knows (from a previous result)."""

    url: str | None = Field(default=None, min_length=1, max_length=2048)
    song_id: int | None = Field(default=None, ge=1, le=MAX_ID)

    @model_validator(mode="after")
    def exactly_one(self) -> "RecommendationRequestBody":
        if (self.url is None) == (self.song_id is None):
            raise ValueError("Provide either a song URL or a song_id.")
        return self
