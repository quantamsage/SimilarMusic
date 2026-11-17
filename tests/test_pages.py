from fastapi.testclient import TestClient


def test_home_page_renders(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<h1>SimilarMusic</h1>" in body
    assert 'id="song-url"' in body
    assert "Find similar songs" in body
    assert "How it works" in body


def test_home_page_links_stylesheet(client: TestClient) -> None:
    body = client.get("/").text
    assert "/static/css/styles.css" in body


def test_static_css_is_served(client: TestClient) -> None:
    response = client.get("/static/css/styles.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


def test_unknown_route_returns_404(client: TestClient) -> None:
    assert client.get("/does-not-exist").status_code == 404


def test_missing_static_file_returns_404(client: TestClient) -> None:
    assert client.get("/static/css/missing.css").status_code == 404
