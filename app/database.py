"""SQLAlchemy engine, session factory and declarative base."""

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str) -> Engine:
    """Create an engine; SQLite needs cross-thread access because FastAPI uses a threadpool."""
    if not database_url.startswith("sqlite"):
        return create_engine(database_url)
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if database_url in ("sqlite://", "sqlite:///:memory:"):
        # Share one connection so every session sees the same in-memory database.
        kwargs["poolclass"] = StaticPool
    return create_engine(database_url, **kwargs)


engine = build_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create missing tables, and add nullable columns introduced since a
    table was created (a minimal migration for the local SQLite database)."""
    import app.models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)


def add_missing_columns(target: Engine) -> list[str]:
    """Adds nullable model columns that an existing table lacks. Returns the
    `table.column` names added. Non-nullable additions need a real migration."""
    inspector = inspect(target)
    added: list[str] = []
    with target.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"The database table '{table.name}' is missing the required column '{column.name}'. "
                        "Delete the local database file to recreate it."
                    )
                column_type = column.type.compile(dialect=target.dialect)
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))
                added.append(f"{table.name}.{column.name}")
    return added


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
