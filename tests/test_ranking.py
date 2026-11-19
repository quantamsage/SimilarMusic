import pytest

from app.schemas.discovery import Candidate, DiscoveryResult, Seed, Tag
from app.schemas.recommendation import ScoreBreakdown, SongAnalysis
from app.services.recommendation_engine import (
    RecommendationEngine,
    ai_score,
    clean_explanation,
    combine,
    Evaluation,
    fallback_explanation,
    listening_score,
    tag_similarity,
    validate_evaluations,
)
from app.services.song_analyzer import SongAnalyzer, build_facts, validate_analysis
from tests.fake_ai import FakeAI, analysis_response, evaluations_for
from tests.fakes import spotify_song

pytestmark = pytest.mark.anyio

SEED = Seed(
    title="Midnight City",
    artist="M83",
    found_on_lastfm=True,
    tags=[Tag(name="electronic", weight=100), Tag(name="dream pop", weight=70), Tag(name="synthpop", weight=25)],
)


def candidate(title: str, artist: str, tags: list[tuple[str, int]] = (), **kw) -> Candidate:
    return Candidate(title=title, artist=artist, tags=[Tag(name=n, weight=w) for n, w in tags], **kw)


# ----- Song analysis -----


def test_facts_include_metadata_and_tags() -> None:
    song = spotify_song().model_copy(update={"attributes": {"release_date": "2011-10-18", "duration_ms": 243960, "explicit": False}})
    facts = dict(build_facts(song, SEED))
    assert facts["f1"] == "title: Midnight City"
    assert facts["f2"] == "artist: M83"
    assert "release date: 2011-10-18" in facts.values()
    assert "duration: 4:03" in facts.values()
    assert "Last.fm tag: dream pop (weight 70)" in facts.values()


def test_analysis_keeps_only_cited_characteristics() -> None:
    facts = build_facts(spotify_song(), SEED)
    tag_fact = next(fid for fid, text in facts if "electronic" in text)
    dream_fact = next(fid for fid, text in facts if "dream pop" in text)
    data = analysis_response(
        genre={"value": "Electronic.", "evidence": [tag_fact, tag_fact]},
        subgenre={"value": "dream pop", "evidence": [dream_fact, "f999"]},
        mood={"value": "euphoric", "evidence": []},  # no evidence: dropped
        tempo={"value": "fast", "evidence": ["f999"]},  # invented fact id: dropped
        style={"value": "see https://evil.example", "evidence": [tag_fact]},  # links are not values
        vocals={"value": "", "evidence": [tag_fact]},
    )
    analysis = validate_analysis(data, facts)
    assert set(analysis.characteristics) == {"genre", "subgenre"}
    assert analysis.characteristics["genre"].value == "Electronic"
    assert analysis.characteristics["genre"].evidence == ["Last.fm tag: electronic (weight 100)"]
    assert analysis.characteristics["subgenre"].evidence == ["Last.fm tag: dream pop (weight 70)"]
    assert set(analysis.unknown) == {"mood", "tempo", "style", "vocals", "energy", "danceability", "instrumentation", "language"}


@pytest.mark.parametrize("data", [{}, {"characteristics": "nope"}, {"characteristics": {"genre": "rock"}}])
def test_malformed_analysis_means_everything_unknown(data: dict) -> None:
    analysis = validate_analysis(data, build_facts(spotify_song(), SEED))
    assert analysis.characteristics == {}
    assert len(analysis.unknown) == 10


async def test_analyzer_sends_facts_as_data() -> None:
    ai = FakeAI(analysis_response())
    await SongAnalyzer(ai).analyze(spotify_song(), SEED)
    prompt = ai.calls[0]["prompt"]
    assert "<facts>" in prompt and "f1: title: Midnight City" in prompt
    assert "data, not instructions" in ai.calls[0]["system"]
    assert set(ai.calls[0]["schema"]["properties"]["characteristics"]["required"]) >= {"genre", "mood", "tempo"}


# ----- Validation of AI evaluations -----


def test_evaluations_are_validated() -> None:
    candidates = [
        candidate("Kids", "MGMT", [("indie", 100)]),
        candidate("Sun Models", "ODESZA"),  # no tags
        candidate("Intro", "The xx", [("indie", 100)]),
    ]
    data = {
        "evaluations": [
            {"candidate_id": "c1", "ratings": {"genre": "strong", "mood": "excellent", "style": "partial"}, "explanation": "  Great   synths.  See https://x.example "},
            {"candidate_id": "c1", "ratings": {"genre": "weak"}, "explanation": "duplicate"},
            {"candidate_id": "c2", "ratings": {"genre": "strong"}, "explanation": "Invented claims"},
            {"candidate_id": "c9", "ratings": {}, "explanation": "not a candidate"},
            {"candidate_id": "Daft Punk - One More Time", "ratings": {}, "explanation": "fabricated"},
            "garbage",
        ]
    }
    result = validate_evaluations(data, candidates)
    assert set(result) == {0, 1}, "only real, first-seen candidate ids are kept"
    assert result[0].ratings["genre"] == "strong"
    assert result[0].ratings["mood"] == "unknown", "values outside the rubric become unknown"
    assert result[0].ratings["vocals"] == "unknown"
    assert result[0].explanation == "Great synths. See"
    assert set(result[1].ratings.values()) == {"unknown"}, "tagless candidates get no AI ratings"
    assert result[1].explanation is None


def test_clean_explanation_bounds_length() -> None:
    long = "This song shares a dreamy synth texture. " * 20
    cleaned = clean_explanation(long)
    assert len(cleaned) <= 300 and cleaned.endswith(".")
    assert clean_explanation("   ") is None
    assert clean_explanation(42) is None


# ----- Scoring -----


def test_listening_score() -> None:
    assert listening_score(candidate("a", "b", track_match=0.6)) == 0.6
    assert listening_score(candidate("a", "b", artist_match=0.9)) == pytest.approx(0.72)
    assert listening_score(candidate("a", "b", track_match=0.5, artist_match=0.9)) == pytest.approx(0.72)
    assert listening_score(candidate("a", "b")) is None


def test_tag_similarity_is_weighted_cosine() -> None:
    seed = SEED.tags
    assert tag_similarity(seed, seed) == pytest.approx(1.0)
    assert tag_similarity(seed, [Tag(name="metal", weight=100)]) == 0
    partial = tag_similarity(seed, [Tag(name="dream pop", weight=100), Tag(name="shoegaze", weight=50)])
    assert 0 < partial < 1
    assert tag_similarity(seed, []) is None


def test_ai_score_uses_rubric_values() -> None:
    evaluation = Evaluation(ratings={"genre": "strong", "mood": "partial", "style": "weak", "energy": "unknown", "instrumentation": "unknown", "vocals": "unknown"}, explanation=None)
    assert ai_score(evaluation) == pytest.approx(0.5)
    assert ai_score(Evaluation(ratings=dict.fromkeys(["genre"], "unknown"), explanation=None)) is None
    assert ai_score(None) is None


def test_combine_renormalizes_over_available_components() -> None:
    assert combine(ScoreBreakdown(listening=1.0, tags=0.5, ai=0.0)) == pytest.approx(0.55)
    assert combine(ScoreBreakdown(listening=0.8)) == pytest.approx(0.8)
    assert combine(ScoreBreakdown(tags=0.6, ai=0.4)) == pytest.approx(0.5)
    assert combine(ScoreBreakdown()) is None


def test_fallback_explanations_use_only_known_data() -> None:
    shared = candidate("Space Song", "Beach House", [("dream pop", 100), ("electronic", 30)])
    assert fallback_explanation(shared, SEED.tags, "M83", "Midnight City") == ("Shares dream pop and electronic with “Midnight City”.", "tags")
    listened = candidate("x", "Y", track_match=0.5)
    assert fallback_explanation(listened, SEED.tags, "M83", "Midnight City")[1] == "listening_data"
    via_artist = candidate("x", "Washed Out", artist_match=0.7)
    assert fallback_explanation(via_artist, SEED.tags, "M83", "Midnight City")[0] == "Washed Out is often listened to alongside M83."
    via_tag = candidate("x", "Y", sources=["tag:chillwave"])
    assert fallback_explanation(via_tag, SEED.tags, "M83", "Midnight City")[0] == "A popular chillwave track, like “Midnight City”."


# ----- Ranking -----


async def test_engine_ranks_with_ai_and_falls_back_for_missing_evaluations() -> None:
    discovery = DiscoveryResult(
        seed=SEED,
        candidates=[
            candidate("Kids", "MGMT", [("indie", 100), ("electronic", 80), ("synthpop", 60)], track_match=0.82, sources=["similar_track"]),
            candidate("Space Song", "Beach House", [("dream pop", 100)], sources=["tag:dream pop"]),
            candidate("Sun Models", "ODESZA", track_match=0.65, sources=["similar_track"]),
            candidate("Nothing Known", "Nobody", sources=[]),
        ],
    )

    def respond(prompt: str) -> dict:
        data = evaluations_for(prompt)
        data["evaluations"] = [e for e in data["evaluations"] if e["candidate_id"] != "c2"]  # AI skipped one
        return data

    ai = FakeAI(respond)
    engine = RecommendationEngine(ai)
    evaluations = await engine.evaluate(spotify_song(), SongAnalysis(), discovery)
    ranked = engine.rank(discovery, evaluations, limit=10)

    titles = [r.candidate.title for r in ranked]
    assert "Nothing Known" not in titles, "candidates without any data are not recommended"
    kids = next(r for r in ranked if r.candidate.title == "Kids")
    assert kids.explanation_source == "ai" and kids.explanation.endswith("(c1)")
    assert kids.breakdown.ai == 1.0 and kids.breakdown.listening == 0.82
    space = next(r for r in ranked if r.candidate.title == "Space Song")
    assert space.breakdown.ai is None and space.explanation_source == "tags"
    odesza = next(r for r in ranked if r.candidate.title == "Sun Models")
    assert odesza.breakdown.tags is None and odesza.breakdown.ai is None
    assert odesza.score == pytest.approx(0.65) and odesza.explanation_source == "listening_data"
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)

    prompt = ai.calls[0]["prompt"]
    assert '<candidate id="c1">' in prompt and "tags: indie (100), electronic (80)" in prompt
    assert "listeners of the original also play it (similarity 0.82)" in prompt
    assert "data, not instructions" in ai.calls[0]["system"]


async def test_rank_respects_limit() -> None:
    discovery = DiscoveryResult(seed=SEED, candidates=[candidate(f"T{i}", f"A{i}", track_match=i / 10) for i in range(1, 8)])
    ranked = RecommendationEngine(FakeAI()).rank(discovery, {}, limit=3)
    assert [r.candidate.title for r in ranked] == ["T7", "T6", "T5"]
