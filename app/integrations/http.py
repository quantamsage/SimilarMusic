"""Shared helper for calling external APIs with consistent error handling."""

import httpx

from app.errors import ProviderUnavailableError, RateLimitedError


async def send(client: httpx.AsyncClient, request: httpx.Request, provider: str) -> httpx.Response:
    """Send a request, translating network failures, rate limits and server errors.

    Other 4xx responses are returned so the caller can interpret them.
    """
    try:
        response = await client.send(request)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailableError(f"{provider} took too long to respond. Please try again.") from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(f"Couldn't reach {provider}. Please try again later.") from exc

    if response.status_code == 429:
        raise RateLimitedError(
            f"{provider} is receiving too many requests. Please wait a moment and try again.",
            retry_after=_parse_retry_after(response.headers.get("Retry-After")),
        )
    if response.status_code >= 500:
        raise ProviderUnavailableError(f"{provider} is having problems right now. Please try again later.")
    return response


def _parse_retry_after(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


def https_url(value: str | None) -> str | None:
    """The URL if it is an https URL with a host, else None. Provider data
    ends up in href/src attributes, so other schemes (javascript:, data:)
    are never passed on."""
    if not value or len(value) > 1000:
        return None
    try:
        parsed = httpx.URL(value)
    except (httpx.InvalidURL, TypeError, ValueError):
        return None
    return value if parsed.scheme == "https" and parsed.host else None
