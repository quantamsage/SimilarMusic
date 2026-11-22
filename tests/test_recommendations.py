from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect

from app.database import add_missing_columns
from app.dependencies import get_ai_provider, get_lastfm, get_metadata_providers
from app.errors import AIServiceError
from app.main import app
from app.models import RecommendationRequest, RequestStatus
from app.services.link_parser import Platform
from tests.fake_ai import FakeAI, analysis_response, evaluations_for
from tests.fake_lastfm import FakeLastFm, m83_world, tags, track
from tests.fakes import SPOTIFY_ID, FakeProvider, spotify_song

URL = f"https://open.spotify.com/track/{SPOTIFY_ID}"


def full_analysis(prompt: str) -> dict:
    fid = next(line.split(":")[0] for line in prompt.splitlines() if "Last.fm tag: electronic" in line)
    return analysis_response(genre={"value": "electronic", "evidence": [fid]}, mood={"value": "euphoric", "evidence": []})


class Env:
    def __init__(self, client: TestClient, fm: FakeLastFm, ai: FakeAI | None) -> None:
        self.client = client
        self.fm = fm
        self.ai = ai

    def post(self, **body):
        return self.client.post("/api/recommendations", json=body)


@pytest.fixture
def env(client: TestClient) -> Iterator[Env]:
    fm = m83_world()
    ai = FakeAI()
    providers = [FakeProvider(Platform.SPOTIFY, spotify_song())]
    holder = Env(client, fm, ai)
    app.dependency_overrides[get_metadata_providers] = lambda: providers
    app.dependency_overrides[get_lastfm] = lambda: holder.fm
    app.dependency_overrides[get_ai_provider] = lambda: holder.ai
    yield holder
    app.dependency_overrides.clear()


def queue_success(ai: FakeAI) -> None:
    # Analysis and evaluation run concurrently; answer by prompt content.
    def respond(prompt: str) -> dict:
        return evaluations_for(prompt) if "<candidates>" in prompt else full_analysis(prompt)

    ai.responses.extend([respond, respond])


def test_full_pipeline(env: Env) -> None:
    queue_success(env.ai)
    response = env.post(url=URL)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["cached"] is False
    assert body["song"]["title"] == "Midnight City"
    assert body["analysis"]["characteristics"] == {
        "genre": {"value": "electronic", "evidence": ["Last.fm tag: electronic (weight 100)"]}
    }
    assert "mood" in body["analysis"]["unknown"], "uncited characteristics are dropped"
    assert body["scoring"]["weights"] == {"listening": 0.4, "tags": 0.3, "ai": 0.3}

    recs = body["recommendations"]
    assert 0 < len(recs) <= 10
    assert [r["rank"] for r in recs] == list(range(1, len(recs) + 1))
    scores = [r["similarity_score"] for r in recs]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)
    first = recs[0]
    assert {"title", "artist", "platform", "url", "similarity_score", "explanation"} <= first.keys()
    assert first["platform"] == "lastfm"
    assert first["url"].startswith("https://www.last.fm/music/")
    assert all(r["artist"] != "M83" for r in recs)

    by_title = {r["title"]: r for r in recs}
    kids = by_title["Kids"]
    assert kids["explanation_source"] == "ai"
    assert kids["score_breakdown"]["listening"] == pytest.approx(0.82)
    assert kids["score_breakdown"]["ai"] == 1.0
    sun = by_title["Sun Models"]
    assert sun["explanation_source"] == "listening_data", "no tags: the AI's explanation is not used"
    assert sun["score_breakdown"]["ai"] is None

    assert len(env.ai.calls) == 2
    assert env.client.get(f"/api/recommendations/{body['request_id']}").json()["recommendations"] == recs


def test_results_are_cached(env: Env) -> None:
    queue_success(env.ai)
    first = env.post(url=URL).json()
    calls_before = len(env.fm.calls)
    second = env.post(url=URL)
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert second.json()["request_id"] == first["request_id"]
    assert len(env.ai.calls) == 2, "no AI calls for a cached result"
    assert len(env.fm.calls) == calls_before, "no Last.fm calls for a cached result"


def test_discover_more_from_a_recommendation(env: Env) -> None:
    queue_success(env.ai)
    kids = next(r for r in env.post(url=URL).json()["recommendations"] if r["title"] == "Kids")

    env.fm.similar[("mgmt", "kids")] = [track("Pumped Up Kicks", "Foster the People", 0.9), track("Midnight City", "M83", 0.8)]
    env.fm.track_tag_map[("foster the people", "pumped up kicks")] = tags(("indie pop", 100))
    queue_success(env.ai)
    response = env.post(song_id=kids["song_id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["song"]["title"] == "Kids" and body["song"]["platform"] == "lastfm"
    titles = [r["title"] for r in body["recommendations"]]
    assert "Pumped Up Kicks" in titles
    assert "Midnight City" in titles, "the earlier seed can be recommended from a new song"
    assert all(r["artist"] != "MGMT" for r in body["recommendations"])


def test_fabricated_ai_output_cannot_add_songs(env: Env) -> None:
    def respond(prompt: str) -> dict:
        if "<candidates>" not in prompt:
            return analysis_response()
        data = evaluations_for(prompt)
        data["evaluations"].append(
            {"candidate_id": "c99", "ratings": dict.fromkeys(("genre", "mood", "style", "energy", "instrumentation", "vocals"), "strong"), "explanation": "Invented song"}
        )
        return data

    env.ai.responses.extend([respond, respond])
    recs = env.post(url=URL).json()["recommendations"]
    known = {("MGMT", "Kids"), ("MGMT", "Electric Feel"), ("The xx", "Intro"), ("ODESZA", "Sun Models"),
             ("Washed Out", "Feel It All Around"), ("Washed Out", "Amor Fati"), ("deadmau5", "Strobe"), ("Beach House", "Space Song")}
    assert {(r["artist"], r["title"]) for r in recs} <= known
    assert all("Invented" not in r["explanation"] for r in recs)


def test_ai_not_configured(env: Env) -> None:
    env.ai = None
    response = env.post(url=URL)
    assert response.status_code == 503
    assert response.json()["error"] == "ai_not_configured"
    assert env.fm.calls == [], "nothing is fetched when recommendations cannot be produced"


def test_bad_links_are_reported_before_missing_ai(env: Env) -> None:
    env.ai = None
    response = env.post(url="https://example.com/not-a-song")
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "unsupported_platform"


def test_ai_failure_is_reported_and_recorded(env: Env, client: TestClient) -> None:
    env.ai.responses.extend([AIServiceError("The AI service took too long to respond."), analysis_response()])
    response = env.post(url=URL)
    assert response.status_code == 502
    assert response.json() == {"error": "ai_failed", "message": "The AI service took too long to respond."}

    from app.database import SessionLocal

    with SessionLocal() as db:
        request = db.query(RecommendationRequest).one()
        assert request.status is RequestStatus.FAILED
        assert request.error == "The AI service took too long to respond."
        assert request.recommendations == []


def test_no_candidates(env: Env) -> None:
    env.fm = FakeLastFm()
    response = env.post(url=URL)
    assert response.status_code == 404
    assert response.json()["error"] == "no_recommendations"
    assert env.ai.calls == []


@pytest.mark.parametrize("body", [{}, {"url": URL, "song_id": 1}, {"song_id": 0}, {"url": ""}])
def test_request_validation(env: Env, body: dict) -> None:
    assert env.client.post("/api/recommendations", json=body).status_code == 422


def test_unknown_song_and_result(env: Env) -> None:
    assert env.post(song_id=999).status_code == 404
    assert env.client.get("/api/recommendations/999").status_code == 404


def test_add_missing_columns(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    old = MetaData()
    Table("songs", old, Column("id", Integer, primary_key=True), Column("title", String), Column("artist", String),
          Column("album", String), Column("artwork_url", String), Column("platform", String), Column("external_id", String),
          Column("url", String), Column("attributes", String), Column("created_at", String))
    Table("recommendation_requests", old, Column("id", Integer, primary_key=True), Column("original_song_id", Integer),
          Column("status", String), Column("created_at", String))
    old.create_all(engine)

    added = add_missing_columns(engine)
    assert set(added) == {"recommendation_requests.analysis", "recommendation_requests.error"}
    columns = {c["name"] for c in inspect(engine).get_columns("recommendation_requests")}
    assert {"analysis", "error"} <= columns
    assert add_missing_columns(engine) == []
