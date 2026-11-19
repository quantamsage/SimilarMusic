"""FastAPI dependencies shared by routers."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.integrations.lastfm import LastFmClient
from app.services.ai_service import AIProvider
from app.services.music_discovery import MusicDiscoveryService
from app.services.music_metadata import MetadataProvider, MusicMetadataService
from app.services.recommendations import RecommendationService


def get_metadata_providers(request: Request) -> list[MetadataProvider]:
    """Providers are created once at startup so they can reuse connections and tokens."""
    return request.app.state.metadata_providers


def get_metadata_service(
    providers: list[MetadataProvider] = Depends(get_metadata_providers),
    db: Session = Depends(get_db),
) -> MusicMetadataService:
    return MusicMetadataService(providers, db)


def get_lastfm(request: Request) -> LastFmClient:
    return request.app.state.lastfm


def get_discovery_service(lastfm: LastFmClient = Depends(get_lastfm)) -> MusicDiscoveryService:
    return MusicDiscoveryService(lastfm)


def get_ai_provider(request: Request) -> AIProvider | None:
    """None when no AI credentials are configured (the service reports it)."""
    return request.app.state.ai


def get_recommendation_service(
    metadata: MusicMetadataService = Depends(get_metadata_service),
    discovery: MusicDiscoveryService = Depends(get_discovery_service),
    ai: AIProvider | None = Depends(get_ai_provider),
    db: Session = Depends(get_db),
) -> RecommendationService:
    settings = get_settings()
    return RecommendationService(
        metadata,
        discovery,
        ai,
        db,
        count=settings.recommendation_count,
        cache_hours=settings.recommendation_cache_hours,
    )
