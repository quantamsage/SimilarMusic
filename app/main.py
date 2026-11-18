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
from app.integrations.spotify import SpotifyClient
from app.integrations.youtube import YouTubeClient
from app.routers import pages, songs


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=False) as http:
        app.state.metadata_providers = [
            SpotifyClient(http, settings.spotify_client_id, settings.spotify_client_secret, settings.spotify_market),
            YouTubeClient(http, settings.youtube_api_key),
        ]
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
    return app


app = create_app()
