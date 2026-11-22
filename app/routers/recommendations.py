"""JSON API for AI-ranked recommendations."""

from fastapi import APIRouter, Depends, Path, Request

from app.dependencies import get_recommendation_service
from app.schemas.recommendation import MAX_ID, RecommendationRequestBody, RecommendationResponse
from app.schemas.song import ErrorResponse
from app.security import limit_recommendations
from app.services.recommendations import RecommendationService

router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])

ERRORS: dict[int | str, dict] = {status: {"model": ErrorResponse} for status in (400, 403, 404, 413, 429, 502, 503)}


@router.post("", response_model=RecommendationResponse, responses=ERRORS)
async def create_recommendations(
    body: RecommendationRequestBody,
    request: Request,
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    """Recommends songs similar to a song link, or to a song from an earlier
    result (`song_id`, used for "discover more like this")."""
    limit_recommendations(request)
    return await service.recommend(body)


@router.get("/{request_id}", response_model=RecommendationResponse, responses=ERRORS)
async def get_recommendations(
    request_id: int = Path(ge=1, le=MAX_ID),
    service: RecommendationService = Depends(get_recommendation_service),
) -> RecommendationResponse:
    return service.get(request_id)
