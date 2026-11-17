import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import build_engine, engine, get_db
from app.models import Recommendation, RecommendationRequest, RequestStatus, Song


def make_song(**overrides: object) -> Song:
    values: dict = {
        "title": "Song",
        "artist": "Artist",
        "platform": "spotify",
        "external_id": "abc123",
        "url": "https://open.spotify.com/track/abc123",
    }
    values.update(overrides)
    return Song(**values)


def test_app_startup_creates_tables(client: object) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"songs", "recommendation_requests", "recommendations"} <= tables


def test_build_engine_uses_static_pool_for_in_memory_sqlite() -> None:
    assert isinstance(build_engine("sqlite://").pool, StaticPool)
    assert not isinstance(build_engine("sqlite:///./file.db").pool, StaticPool)


def test_song_round_trip(db_session: Session) -> None:
    song = make_song(attributes={"tags": ["indie rock"]})
    db_session.add(song)
    db_session.commit()

    stored = db_session.get(Song, song.id)
    assert stored is not None
    assert stored.title == "Song"
    assert stored.attributes == {"tags": ["indie rock"]}
    assert stored.album is None
    assert stored.created_at is not None


def test_song_platform_and_external_id_are_unique(db_session: Session) -> None:
    db_session.add(make_song())
    db_session.commit()
    db_session.add(make_song(title="Duplicate"))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_same_external_id_allowed_on_different_platforms(db_session: Session) -> None:
    db_session.add_all([make_song(), make_song(platform="youtube")])
    db_session.commit()

    assert db_session.query(Song).count() == 2


def test_request_defaults_to_pending_and_orders_recommendations(db_session: Session) -> None:
    original = make_song()
    second = make_song(external_id="second", title="Second")
    first = make_song(external_id="first", title="First")
    request = RecommendationRequest(original_song=original)
    request.recommendations = [
        Recommendation(song=second, similarity_score=0.7, explanation="Similar mood.", rank=2),
        Recommendation(song=first, similarity_score=0.9, explanation="Same genre.", rank=1),
    ]
    db_session.add(request)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(RecommendationRequest, request.id)
    assert stored is not None
    assert stored.status is RequestStatus.PENDING
    assert stored.original_song.title == "Song"
    assert [r.song.title for r in stored.recommendations] == ["First", "Second"]


def test_rank_is_unique_within_a_request(db_session: Session) -> None:
    request = RecommendationRequest(original_song=make_song())
    request.recommendations = [
        Recommendation(song=make_song(external_id="a"), similarity_score=0.5, explanation="x", rank=1),
        Recommendation(song=make_song(external_id="b"), similarity_score=0.4, explanation="y", rank=1),
    ]
    db_session.add(request)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_get_db_closes_session() -> None:
    generator = get_db()
    session = next(generator)
    assert isinstance(session, Session)

    with pytest.raises(StopIteration):
        next(generator)
    assert not session.in_transaction()
