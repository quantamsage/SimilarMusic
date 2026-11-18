"""JSON API for identifying songs from links."""

from fastapi import APIRouter, Depends

from app.dependencies import get_metadata_service
from app.schemas.song import ErrorResponse, SongLookupRequest, SongMetadata
from app.services.music_metadata import MusicMetadataService

router = APIRouter(prefix="/api/songs", tags=["songs"])

ERROR_RESPONSES: dict[int | str, dict] = {
    status: {"model": ErrorResponse} for status in (400, 404, 502, 503)
}


@router.post("/lookup", response_model=SongMetadata, responses=ERROR_RESPONSES)
async def lookup_song(
    payload: SongLookupRequest,
    service: MusicMetadataService = Depends(get_metadata_service),
) -> SongMetadata:
    return await service.lookup(payload.url)
