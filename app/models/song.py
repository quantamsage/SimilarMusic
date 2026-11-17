from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Song(Base):
    """A song identified on a music platform, cached to avoid repeated metadata lookups."""

    __tablename__ = "songs"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_song_platform_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    artist: Mapped[str] = mapped_column(String(300))
    album: Mapped[str | None] = mapped_column(String(300))
    artwork_url: Mapped[str | None] = mapped_column(String(1000))
    platform: Mapped[str] = mapped_column(String(50))
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(1000))
    # Provider metadata that was actually retrieved (genres, tags, tempo, ...).
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
