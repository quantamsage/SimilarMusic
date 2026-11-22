"""Security headers, cross-site request blocking, body limits and the
per-client recommendation limit."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import CSP, SlidingWindowLimiter, TooManyRequestsError

URL = "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"


@pytest.mark.parametrize("path", ["/", "/results/999", "/static/js/app.js", "/api/recommendations/999", "/nope"])
def test_security_headers(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.headers["content-security-policy"] == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"


def test_api_docs_keep_working(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers


@pytest.mark.parametrize(
    "headers",
    [{"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}, {"origin": "https://evil.example"}, {"origin": "null"}],
)
def test_cross_site_posts_are_rejected(client: TestClient, headers: dict) -> None:
    form = client.post("/recommend", data={"url": URL}, headers=headers)
    assert form.status_code == 403
    api = client.post("/api/recommendations", json={"url": URL}, headers=headers)
    assert api.status_code == 403 and api.json()["error"] == "forbidden"


@pytest.mark.parametrize("headers", [{"sec-fetch-site": "same-origin"}, {"origin": "http://testserver"}, {}])
def test_same_origin_and_non_browser_posts_are_allowed(client: TestClient, headers: dict) -> None:
    response = client.post("/api/recommendations", json={"url": URL}, headers=headers)
    assert response.status_code == 503, response.text  # reaches the app: no AI key in tests
    assert response.json()["error"] == "ai_not_configured"


def test_oversized_bodies_are_rejected_before_reading(client: TestClient) -> None:
    response = client.post("/api/recommendations", content=b"{" + b" " * 20000 + b"}", headers={"content-type": "application/json"})
    assert response.status_code == 413
    response = client.post("/recommend", content=b"url=" + b"x" * 20000, headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413


def test_limiter_counts_per_client_within_the_window() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(2, window_seconds=3600, clock=lambda: now[0])
    limiter.hit("a")
    limiter.hit("a")
    limiter.hit("b")
    with pytest.raises(TooManyRequestsError) as caught:
        limiter.hit("a")
    assert caught.value.retry_after == 3600 and "60 minutes" in caught.value.message
    now[0] = 1800
    with pytest.raises(TooManyRequestsError) as caught:
        limiter.hit("a")
    assert caught.value.retry_after == 1800
    now[0] = 3600
    limiter.hit("a")  # the first event left the window
    SlidingWindowLimiter(0).hit("a")  # 0 = unlimited


def test_recommendation_requests_are_limited_per_client(client: TestClient) -> None:
    app.state.recommendation_limiter = SlidingWindowLimiter(2)
    for _ in range(2):
        assert client.post("/api/recommendations", json={"url": URL}).status_code == 503
    response = client.post("/api/recommendations", json={"url": URL})
    assert response.status_code == 429
    assert response.json()["error"] == "too_many_requests"
    assert int(response.headers["retry-after"]) > 0

    page = client.post("/recommend", data={"url": URL})
    assert page.status_code == 429
    assert "a lot of recommendations" in page.text
    assert "keeps the service available" in page.text
