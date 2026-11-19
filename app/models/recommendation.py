import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.song import Song, utcnow


class RequestStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class RecommendationRequest(Base):
    __tablename__ = "recommendation_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"))
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, values_callable=lambda e: [m.value for m in e]),
        default=RequestStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    #: Validated analysis of the original song (SongAnalysis as JSON).
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: User-facing reason when the request failed.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    original_song: Mapped[Song] = relationship()
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="request",
        order_by="Recommendation.rank",
        cascade="all, delete-orphan",
    )


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (UniqueConstraint("request_id", "rank", name="uq_recommendation_request_rank"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("recommendation_requests.id"))
    song_id: Mapped[int] = mapped_column(ForeignKey("songs.id"))
    similarity_score: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer)
    #: Score breakdown, explanation source, discovery sources and tags.
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    request: Mapped[RecommendationRequest] = relationship(back_populates="recommendations")
    song: Mapped[Song] = relationship()
