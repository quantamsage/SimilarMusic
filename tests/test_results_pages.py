"""HTML pages: the search form, the no-JavaScript form flow, the results
page and error pages."""

import re
from markupsafe import escape
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_ai_provider, get_lastfm, get_metadata_providers, get_track_searcher
from app.errors import AIServiceError, ProviderUnavailableError
from app.main import app
from app.services.link_parser import Platform
from tests.fake_ai import FakeAI
from tests.fake_lastfm import m83_world
from tests.fakes import FakeProvider, spotify_song
from tests.test_listening_links import FakeSearcher, kids_on_spotify
from tests.test_recommendations import URL, queue_success


class Env:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.ai: FakeAI | None = FakeAI()
        self.provider = FakeProvider(Platform.SPOTIFY, spotify_song())
        self.searcher = FakeSearcher({("Kids", "MGMT"): kids_on_spotify()})

    def form(self, **fields: str):
        return self.client.post("/recommend", data=fields, follow_redirects=False)


@pytest.fixture
def env(client: TestClient) -> Iterator[Env]:
    holder = Env(client)
    app.dependency_overrides[get_metadata_providers] = lambda: [holder.provider]
    app.dependency_overrides[get_lastfm] = m83_world
    app.dependency_overrides[get_ai_provider] = lambda: holder.ai
    app.dependency_overrides[get_track_searcher] = lambda: holder.searcher
    yield holder
    app.dependency_overrides.clear()


def results_page(env: Env) -> tuple[str, str]:
    queue_success(env.ai)
    response = env.form(url=URL)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert re.fullmatch(r"/results/\d+", location)
    page = env.client.get(location)
    assert page.status_code == 200
    return location, page.text


def test_home_form_is_enabled_and_works_without_javascript(env: Env) -> None:
    body = env.client.get("/").text
    assert 'action="/recommend"' in body and 'method="post"' in body
    assert "disabled" not in body.split('id="song-form"')[1].split("</form>")[0]
    assert "isn't available yet" not in body
    assert "/static/js/app.js" in body
    assert 'id="loading"' in body and "Finding similar music on Last.fm" in body


def test_results_page_shows_song_recommendations_and_actions(env: Env) -> None:
    _, page = results_page(env)
    # The original song, with its artwork and a link back to its platform.
    assert "<h1 id=\"original-heading\" class=\"song-title\">Midnight City</h1>" in page
    assert 'src="https://i.scdn.co/image/large"' in page
    assert 'href="https://open.spotify.com/track/' in page and "Open on Spotify" in page
    assert "<dt>Genre</dt>" in page and "<dd>electronic</dd>" in page
    assert 'title="Based on: Last.fm tag: electronic (weight 100)"' in page

    # Recommendations: artwork when found, scores, explanations, listen links, discover-more.
    assert "similar songs</h2>" in page
    assert 'src="https://i.scdn.co/image/kids"' in page
    assert "Listen on Spotify" in page and 'href="https://open.spotify.com/track/kids1"' in page
    assert "Listen on Last.fm" in page, "songs not on Spotify link to Last.fm"
    assert "artwork-placeholder" in page, "songs without artwork get a placeholder"
    assert re.search(r"\d+% similar", page)
    assert '<meter min="0" max="1"' in page
    assert "Explanation written by AI" in page and "Explanation based on listening data" in page
    for link in re.findall(r'<a class="button button-secondary" href="https://[^"]+"[^>]*>', page):
        assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link, link
    discover = re.findall(r'<input type="hidden" name="song_id" value="(\d+)">', page)
    assert discover, "each recommendation can be used for discover-more"
    assert "How similarity scores work" in page
    assert "Search another song" in page


def test_discover_more_through_the_form(env: Env) -> None:
    _, page = results_page(env)
    song_id = re.findall(r'name="song_id" value="(\d+)"', page)[0]
    queue_success(env.ai)
    response = env.form(song_id=song_id)
    # The new seed has no Last.fm data in the fake world, so this reports it clearly.
    assert response.status_code in (303, 404)
    if response.status_code == 404:
        assert "enough" in response.text


def test_errors_are_shown_on_the_home_page_with_the_link_kept(env: Env) -> None:
    response = env.form(url="https://example.com/<b>song</b>")
    assert response.status_code == 400
    assert "Spotify track links and YouTube video links are supported." in response.text
    assert 'value="https://example.com/&lt;b&gt;song&lt;/b&gt;"' in response.text, "the link is kept, escaped"
    assert 'aria-invalid="true"' in response.text

    assert env.form().status_code == 400
    assert "Paste a link to a song first." in env.form(url="   ").text

    env.provider.error = ProviderUnavailableError("Spotify returned an unexpected error (500).")
    response = env.form(url=URL)
    assert response.status_code == 502
    assert "Spotify returned an unexpected error" in response.text and "Try again in a moment" in response.text

    env.provider.error = None
    env.ai = None
    response = env.form(url=URL)
    assert response.status_code == 503 and "no AI API key" in response.text


def test_ai_failure_is_explained(env: Env) -> None:
    env.ai.responses.extend([AIServiceError("The AI service failed."), AIServiceError("The AI service failed.")])
    response = env.form(url=URL)
    assert response.status_code == 502
    assert escape("The AI analysis didn't complete") in response.text


def test_ai_output_is_escaped(env: Env) -> None:
    def respond(prompt: str) -> dict:
        from tests.fake_ai import evaluations_for
        from tests.test_recommendations import full_analysis

        if "<candidates>" not in prompt:
            return full_analysis(prompt)
        result = evaluations_for(prompt)
        for item in result["evaluations"]:
            item["explanation"] = "<script>alert(1)</script> Similar dreamy synths and euphoric mood."
        return result

    env.ai.responses.extend([respond, respond])
    location = env.form(url=URL).headers["location"]
    page = env.client.get(location).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_unknown_results_and_oversized_forms(env: Env) -> None:
    response = env.client.get("/results/999")
    assert response.status_code == 404
    assert escape("Those recommendations don't exist.") in response.text
    assert env.client.get("/results/abc").status_code == 422
    response = env.client.post("/recommend", content=b"url=" + b"x" * 9000, headers={"content-type": "application/x-www-form-urlencoded"})
    assert response.status_code == 400


def test_script_is_served(client: TestClient) -> None:
    response = client.get("/static/js/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
