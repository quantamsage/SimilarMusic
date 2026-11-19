import os

# Set before the app is imported: use an in-memory database and blank out API
# credentials (env vars take priority over .env) so tests never call real services.
os.environ["DATABASE_URL"] = "sqlite://"
for credential in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "YOUTUBE_API_KEY", "LASTFM_API_KEY"):
    os.environ[credential] = ""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session() -> Iterator[Session]:
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
