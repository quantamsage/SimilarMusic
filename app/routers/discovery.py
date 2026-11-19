"""JSON API for inspecting music discovery on its own."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_discovery_service, get_metadata_service
from app.schemas.discovery import DiscoveryResult
from app.schemas.song import ErrorResponse, SongLookupRequest, SongMetadata
from app.services.music_discovery import MusicDiscoveryService
from app.services.music_metadata import MusicMetadataService

router = APIRouter(prefix="/api/discovery", tags=["discovery"])


class DiscoveryResponse(BaseModel):
    song: SongMetadata
    discovery: DiscoveryResult


@router.post(
    "",
    response_model=DiscoveryResponse,
    responses={status: {"model": ErrorResponse} for status in (400, 404, 502, 503)},
)
async def discover(
    payload: SongLookupRequest,
    metadata: MusicMetadataService = Depends(get_metadata_service),
    discovery: MusicDiscoveryService = Depends(get_discovery_service),
) -> DiscoveryResponse:
    """Identifies the song and returns the candidate songs found for it (before AI ranking)."""
    song = await metadata.lookup(payload.url)
    return DiscoveryResponse(song=song, discovery=await discovery.discover(song))
