"""HTTP protections: security headers, cross-site request blocking, a request
body cap and a per-client limit on recommendation requests.

Recommendations are expensive (dozens of Last.fm calls and two AI calls per
new song), so other sites must not be able to trigger them through a
visitor's browser, and one client must not be able to run unlimited
requests.
"""

import math
import time
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.errors import SongLookupError

#: Largest request body accepted (the forms and JSON bodies are tiny).
MAX_BODY_BYTES = 16 * 1024

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}
#: FastAPI's interactive API docs load their own scripts from a CDN.
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class TooManyRequestsError(SongLookupError):
    status_code = 429
    code = "too_many_requests"

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SlidingWindowLimiter:
    """At most `limit` events per client in any `window_seconds` (in memory,
    per process; 0 means unlimited)."""

    MAX_CLIENTS = 10_000

    def __init__(self, limit: int, window_seconds: float = 3600, clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}

    def hit(self, client: str) -> None:
        """Counts one event, or raises TooManyRequestsError."""
        if self.limit <= 0:
            return
        now = self._clock()
        if len(self._events) >= self.MAX_CLIENTS:
            self._sweep(now)
        events = self._events.setdefault(client, deque())
        while events and now - events[0] >= self.window:
            events.popleft()
        if len(events) >= self.limit:
            retry = max(1, math.ceil(self.window - (now - events[0])))
            minutes = math.ceil(retry / 60)
            raise TooManyRequestsError(
                f"You've asked for a lot of recommendations. Please try again in {minutes} minute{'s' if minutes != 1 else ''}.",
                retry_after=retry,
            )
        events.append(now)

    def _sweep(self, now: float) -> None:
        for key in [k for k, v in self._events.items() if not v or now - v[-1] >= self.window]:
            del self._events[key]


def client_id(request: Request) -> str:
    """The client's address (the direct peer; proxy headers aren't trusted)."""
    return request.client.host if request.client else "unknown"


def limit_recommendations(request: Request) -> None:
    limiter: SlidingWindowLimiter | None = getattr(request.app.state, "recommendation_limiter", None)
    if limiter is not None:
        limiter.hit(client_id(request))


def is_cross_site(request: Request) -> bool:
    """True for a state-changing request sent by another site's page."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        # Non-browser clients (curl, scripts) send no Origin; browsers always
        # send one on cross-origin POSTs. "null" comes from sandboxed pages.
        return origin == "null"
    return origin != f"{request.url.scheme}://{request.headers.get('host', '')}"


async def protect(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Middleware: rejects cross-site and oversized unsafe requests, and adds
    security headers to every response."""
    if request.method in UNSAFE_METHODS:
        if is_cross_site(request):
            return _json_or_text(request, 403, "forbidden", "Requests from other websites are not allowed.")
        length = request.headers.get("content-length")
        if length is None and request.headers.get("transfer-encoding"):
            return _json_or_text(request, 411, "length_required", "Send the request with a Content-Length.")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return _json_or_text(request, 413, "too_large", "The request is too large.")
    response = await call_next(request)
    if not request.url.path.startswith(DOCS_PATHS):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
    return response


def _json_or_text(request: Request, status: int, code: str, message: str) -> Response:
    if request.url.path.startswith("/api/"):
        response: Response = JSONResponse({"error": code, "message": message}, status_code=status)
    else:
        response = PlainTextResponse(message, status_code=status)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response
