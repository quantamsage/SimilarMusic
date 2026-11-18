"""FastAPI dependencies shared by routers."""

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.music_metadata import MetadataProvider, MusicMetadataService


def get_metadata_providers(request: Request) -> list[MetadataProvider]:
    """Providers are created once at startup so they can reuse connections and tokens."""
    return request.app.state.metadata_providers


def get_metadata_service(
    providers: list[MetadataProvider] = Depends(get_metadata_providers),
    db: Session = Depends(get_db),
) -> MusicMetadataService:
    return MusicMetadataService(providers, db)
