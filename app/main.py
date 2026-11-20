"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, get_settings
from app.database import init_db
from app.errors import RateLimitedError, SongLookupError
from app.integrations.lastfm import LastFmClient
from app.integrations.spotify import SpotifyClient
from app.integrations.youtube import YouTubeClient
from app.routers import discovery, pages, recommendations, songs
from app.services.ai_service import ClaudeProvider


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=False) as http:
        spotify = SpotifyClient(http, settings.spotify_client_id, settings.spotify_client_secret, settings.spotify_market)
        app.state.metadata_providers = [spotify, YouTubeClient(http, settings.youtube_api_key)]
        app.state.spotify = spotify if settings.spotify_client_id and settings.spotify_client_secret else None
        app.state.lastfm = LastFmClient(http, settings.lastfm_api_key)
        app.state.ai = (
            ClaudeProvider(
                settings.anthropic_api_key,
                model=settings.ai_model,
                effort=settings.ai_effort,
                timeout_seconds=settings.ai_timeout_seconds,
            )
            if settings.anthropic_api_key
            else None
        )
        yield


async def handle_lookup_error(_: Request, exc: SongLookupError) -> JSONResponse:
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitedError) and exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.code, "message": exc.message},
        headers=headers,
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.add_exception_handler(SongLookupError, handle_lookup_error)
    app.include_router(pages.router)
    app.include_router(songs.router)
    app.include_router(discovery.router)
    app.include_router(recommendations.router)
    return app


app = create_app()
