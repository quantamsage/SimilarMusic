"""The recommendation pipeline.

1. Identify the original song (from a link, or a song already in the database).
2. Find candidate songs with music discovery (Last.fm).
3. In parallel, ask the AI to analyse the original song and to evaluate the
   candidates.
4. Score and rank the candidates, keep the best, and store the result.

Completed results are reused for the same original song for a configurable
time, so repeated requests cost no API calls.
"""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AINotConfiguredError, NoCandidatesError, SongLookupError, SongNotFoundError
from app.models import Recommendation, RecommendationRequest, RequestStatus, Song
from app.schemas.discovery import Candidate, Tag
from app.schemas.recommendation import (
    Recommendation as RecommendationOut,
)
from app.schemas.recommendation import (
    RecommendationRequestBody,
    RecommendationResponse,
    ScoreBreakdown,
    SongAnalysis,
)
from app.schemas.song import SongMetadata
from app.services.ai_service import AIProvider
from app.services.link_parser import Platform
from app.services.music_discovery import MusicDiscoveryService
from app.services.music_metadata import MusicMetadataService
from app.services.recommendation_engine import SCORING_METHOD, RecommendationEngine
from app.services.song_analyzer import SongAnalyzer
from app.services.text import normalize, primary_artist


class RecommendationService:
    def __init__(
        self,
        metadata: MusicMetadataService,
        discovery: MusicDiscoveryService,
        ai: AIProvider | None,
        db: Session,
        count: int = 10,
        cache_hours: float = 24.0,
    ) -> None:
        self._metadata = metadata
        self._discovery = discovery
        self._ai = ai
        self._db = db
        self._count = count
        self._cache_age = timedelta(hours=cache_hours)

    async def recommend(self, body: RecommendationRequestBody) -> RecommendationResponse:
        if self._ai is None:
            raise AINotConfiguredError(
                "Recommendations aren't available: the server has no AI API key (ANTHROPIC_API_KEY) configured."
            )
        song_row, song = await self._resolve(body)

        cached = self._recent_result(song_row.id)
        if cached is not None:
            return self.response(cached, cached=True)

        request = RecommendationRequest(original_song=song_row, status=RequestStatus.PENDING)
        self._db.add(request)
        self._db.commit()
        try:
            discovery = await self._discovery.discover(song)
            engine = RecommendationEngine(self._ai)
            analysis, evaluations = await asyncio.gather(
                SongAnalyzer(self._ai).analyze(song, discovery.seed),
                engine.evaluate(song, discovery),
            )
            ranked = engine.rank(discovery, evaluations, self._count)
            if not ranked:
                raise NoCandidatesError("None of the songs found had enough data to recommend. Try another song.")

            for position, item in enumerate(ranked, start=1):
                self._db.add(
                    Recommendation(
                        request=request,
                        song=self._candidate_song(item.candidate),
                        similarity_score=item.score,
                        explanation=item.explanation,
                        rank=position,
                        details={
                            "breakdown": item.breakdown.model_dump(),
                            "explanation_source": item.explanation_source,
                            "found_via": item.candidate.sources,
                            "tags": [t.model_dump() for t in item.candidate.tags],
                        },
                    )
                )
            request.analysis = analysis.model_dump()
            request.status = RequestStatus.COMPLETED
            self._db.commit()
        except SongLookupError as exc:
            self._fail(request, exc.message)
            raise
        except Exception:
            self._fail(request, "Something went wrong while finding recommendations.")
            raise
        return self.response(request, cached=False)

    def get(self, request_id: int) -> RecommendationResponse:
        request = self._db.get(RecommendationRequest, request_id)
        if request is None or request.status is not RequestStatus.COMPLETED:
            raise SongNotFoundError("Those recommendations don't exist or did not complete.")
        return self.response(request, cached=True)

    # ----- Helpers -----

    async def _resolve(self, body: RecommendationRequestBody) -> tuple[Song, SongMetadata]:
        if body.song_id is not None:
            row = self._db.get(Song, body.song_id)
            if row is None:
                raise SongNotFoundError("That song is not known. Paste its link instead.")
            return row, SongMetadata.model_validate(row)
        assert body.url is not None
        song = await self._metadata.lookup(body.url)
        row = self._metadata.stored(body.url)
        if row is None:
            raise SongNotFoundError("The song could not be stored. Please try again.")
        return row, song

    def _recent_result(self, song_id: int) -> RecommendationRequest | None:
        if self._cache_age <= timedelta(0):
            return None
        latest = self._db.scalars(
            select(RecommendationRequest)
            .where(
                RecommendationRequest.original_song_id == song_id,
                RecommendationRequest.status == RequestStatus.COMPLETED,
            )
            .order_by(RecommendationRequest.id.desc())
        ).first()
        if latest is None:
            return None
        created = latest.created_at
        if created.tzinfo is None:  # SQLite returns naive UTC datetimes
            created = created.replace(tzinfo=timezone.utc)
        return latest if datetime.now(timezone.utc) - created <= self._cache_age else None

    def _candidate_song(self, candidate: Candidate) -> Song:
        key = f"{normalize(primary_artist(candidate.artist))}|{normalize(candidate.title)}"
        external_id = "lf:" + hashlib.sha1(key.encode()).hexdigest()[:32]
        attributes = {"tags": [t.model_dump() for t in candidate.tags]}
        if candidate.mbid:
            attributes["mbid"] = candidate.mbid
        row = self._db.scalars(
            select(Song).where(Song.platform == Platform.LASTFM.value, Song.external_id == external_id)
        ).first()
        if row is None:
            row = Song(platform=Platform.LASTFM.value, external_id=external_id)
            self._db.add(row)
        row.title = candidate.title[:300]
        row.artist = candidate.artist[:300]
        row.url = candidate.url
        row.attributes = attributes
        return row

    def _fail(self, request: RecommendationRequest, message: str) -> None:
        self._db.rollback()
        request.status = RequestStatus.FAILED
        request.error = message[:1000]
        self._db.commit()

    @staticmethod
    def response(request: RecommendationRequest, cached: bool) -> RecommendationResponse:
        original = request.original_song
        recommendations = []
        for row in request.recommendations:
            details = row.details or {}
            recommendations.append(
                RecommendationOut(
                    rank=row.rank,
                    song_id=row.song.id,
                    title=row.song.title,
                    artist=row.song.artist,
                    platform=row.song.platform,
                    url=row.song.url,
                    similarity_score=row.similarity_score,
                    score_breakdown=ScoreBreakdown(**details.get("breakdown", {})),
                    explanation=row.explanation,
                    explanation_source=details.get("explanation_source", "ai"),
                    tags=[Tag(**t) for t in details.get("tags", [])],
                    found_via=details.get("found_via", []),
                )
            )
        return RecommendationResponse(
            request_id=request.id,
            created_at=request.created_at,
            cached=cached,
            song=SongMetadata.model_validate(original),
            song_id=original.id,
            analysis=SongAnalysis(**(request.analysis or {})),
            recommendations=recommendations,
            scoring=SCORING_METHOD,
        )
