"""Errors raised while turning a song link into song metadata.

Each error carries a user-facing message and the HTTP status the API should return.
"""


class SongLookupError(Exception):
    status_code: int = 500
    code: str = "lookup_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidSongUrlError(SongLookupError):
    status_code = 400
    code = "invalid_url"


class UnsupportedPlatformError(SongLookupError):
    status_code = 400
    code = "unsupported_platform"


class SongNotFoundError(SongLookupError):
    status_code = 404
    code = "song_not_found"


class ProviderNotConfiguredError(SongLookupError):
    status_code = 503
    code = "provider_not_configured"


class ProviderUnavailableError(SongLookupError):
    status_code = 502
    code = "provider_unavailable"


class RateLimitedError(SongLookupError):
    status_code = 503
    code = "rate_limited"

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
