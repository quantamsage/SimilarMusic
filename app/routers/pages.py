"""Server-rendered HTML pages.

* `GET /` — the home page with the song link form.
* `POST /recommend` — the form's target when JavaScript is off (and for
  "discover more"): runs the recommendation pipeline and redirects to the
  results, or shows the home page again with an explanation.
* `GET /results/{request_id}` — a stored recommendation result.

With JavaScript on, the forms call the JSON API instead and show progress
while it works; both paths use the same service and produce the same pages.
"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError

from app.config import get_settings
from app.dependencies import get_recommendation_service
from app.errors import SongLookupError, SongNotFoundError
from app.schemas.recommendation import MAX_ID, RecommendationRequestBody, RecommendationResponse
from app.security import limit_recommendations
from app.services.recommendations import RecommendationService
from app.templating import templates

router = APIRouter()

#: Largest form body accepted by POST /recommend.
MAX_FORM_BYTES = 8 * 1024

PLATFORM_NAMES = {"spotify": "Spotify", "youtube": "YouTube", "lastfm": "Last.fm"}

CHARACTERISTIC_LABELS = {
    "genre": "Genre",
    "subgenre": "Subgenre",
    "mood": "Mood",
    "style": "Style",
    "tempo": "Tempo",
    "energy": "Energy",
    "danceability": "Danceability",
    "instrumentation": "Instruments",
    "vocals": "Vocals",
    "language": "Language",
}

#: What the user can do about each error, shown under the message.
ERROR_HINTS = {
    "invalid_url": "Copy the song's share link from Spotify or YouTube and paste it here.",
    "unsupported_platform": "Spotify track links and YouTube video links are supported.",
    "song_not_found": "Check that the link points to a single song that is still available.",
    "provider_not_configured": "The server is missing API credentials for this music service. See the README.",
    "provider_unavailable": "A music service didn't respond as expected. Try again in a moment.",
    "rate_limited": "Too many requests were made to a music service. Wait a minute and try again.",
    "no_recommendations": "This song doesn't have enough listening data yet. Try a more widely known song.",
    "ai_not_configured": "The server has no AI API key configured. See the README.",
    "ai_failed": "The AI analysis didn't complete. Try again in a moment.",
    "too_many_requests": "This limit keeps the service available for everyone.",
}


def _home(request: Request, status: int = 200, url: str = "", error: dict | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "home.html",
        {"app_name": get_settings().app_name, "url": url, "error": error},
        status_code=status,
    )


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return _home(request)


async def _form(request: Request) -> dict[str, str]:
    body = await request.body()
    if len(body) > MAX_FORM_BYTES:
        return {}
    fields = parse_qs(body.decode("utf-8", errors="replace"), max_num_fields=10)
    return {key: values[0].strip() for key, values in fields.items() if values}


def _parse_id(text: str) -> int | None:
    """A database id from form or path text, or None (no Unicode digits, no overflow)."""
    if not (text.isascii() and text.isdigit() and len(text) <= 19):
        return None
    value = int(text)
    return value if 1 <= value <= MAX_ID else None


@router.post("/recommend", response_class=HTMLResponse)
async def recommend(
    request: Request,
    service: RecommendationService = Depends(get_recommendation_service),
) -> Response:
    form = await _form(request)
    url = form.get("url", "")
    song_id = form.get("song_id", "")
    try:
        body = RecommendationRequestBody(
            url=url or None,
            song_id=_parse_id(song_id),
        )
    except ValidationError:
        error = {"code": "invalid_url", "message": "Paste a link to a song first.", "hint": ERROR_HINTS["invalid_url"]}
        return _home(request, 400, url, error)
    try:
        limit_recommendations(request)
        result = await service.recommend(body)
    except SongLookupError as exc:
        error = {"code": exc.code, "message": exc.message, "hint": ERROR_HINTS.get(exc.code, "")}
        return _home(request, exc.status_code, url, error)
    return RedirectResponse(f"/results/{result.request_id}", status_code=303)


def _results_context(result: RecommendationResponse) -> dict:
    return {
        "app_name": get_settings().app_name,
        "result": result,
        "platform_names": PLATFORM_NAMES,
        "labels": CHARACTERISTIC_LABELS,
    }


@router.get("/results/{request_id}", response_class=HTMLResponse)
async def results(
    request: Request,
    request_id: str,
    service: RecommendationService = Depends(get_recommendation_service),
) -> HTMLResponse:
    try:
        parsed = _parse_id(request_id)
        if parsed is None:
            raise SongNotFoundError("Those recommendations don't exist.")
        result = service.get(parsed)
    except SongLookupError:
        error = {
            "code": "not_found",
            "message": "Those recommendations don't exist.",
            "hint": "Paste a song link to get new recommendations.",
        }
        return _home(request, 404, "", error)
    return templates.TemplateResponse(request, "results.html", _results_context(result))
