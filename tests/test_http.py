import pytest

from app.integrations.http import https_url


@pytest.mark.parametrize(
    "value",
    ["https://i.scdn.co/image/abc", "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", "https://i.ytimg.com/vi/x/hqdefault.jpg"],
)
def test_https_urls_are_kept(value: str) -> None:
    assert https_url(value) == value


@pytest.mark.parametrize(
    "value",
    [None, "", "javascript:alert(1)", "data:image/png;base64,AAAA", "http://i.scdn.co/image/abc", "https://", "//evil.example/x", "https://" + "a" * 1000],
)
def test_other_urls_are_dropped(value: str | None) -> None:
    assert https_url(value) is None
