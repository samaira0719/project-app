"""SQLAlchemy engine, session factory and FastAPI dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()


def _normalize_url(url: str) -> str:
    """Route plain postgres:// URLs through the psycopg (v3) driver."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _normalize_url(settings.database_url)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Neon suspends idle databases; re-check connections
    pool_recycle=300,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the first release. `create_all` creates missing *tables*
# but never alters an existing one, so a database created before a column
# existed would keep failing on every query that selects it. Each entry is an
# additive, nullable column - safe to apply to a live database, and a no-op
# once it is there.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "tasks": {"quadrant_override": "VARCHAR(20)"},
    "task_decisions": {"assessment": "JSON"},
    "users": {
        "privacy_version": "VARCHAR(32)",
        "privacy_accepted_at": "TIMESTAMP",
        "consent_personalization": "BOOLEAN DEFAULT 0",
        "consent_ai": "BOOLEAN DEFAULT 0",
        "age_confirmed": "BOOLEAN DEFAULT 0",
    },
}

# Portable spellings for the few types above that differ between the two
# databases this runs on (SQLite locally, Postgres on Neon).
_DIALECT_TYPES: dict[str, dict[str, str]] = {
    "postgresql": {
        "TIMESTAMP": "TIMESTAMP WITH TIME ZONE",
        "JSON": "JSONB",
        "BOOLEAN DEFAULT 0": "BOOLEAN DEFAULT FALSE",
    },
}


def _column_type(column_type: str) -> str:
    return _DIALECT_TYPES.get(engine.dialect.name, {}).get(column_type, column_type)


def _apply_additive_migrations() -> None:
    """Add any missing column from ADDED_COLUMNS. Idempotent."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table, columns in ADDED_COLUMNS.items():
        if table not in existing_tables:
            continue  # create_all just made it, with every column present
        present = {c["name"] for c in inspector.get_columns(table)}
        missing = {n: t for n, t in columns.items() if n not in present}
        if not missing:
            continue
        with engine.begin() as connection:
            for name, column_type in missing.items():
                # Identifiers come from the literal dict above, never user input.
                connection.execute(
                    text(
                        f"ALTER TABLE {table} ADD COLUMN {name} "
                        f"{_column_type(column_type)}"
                    )
                )


def init_db() -> None:
    """Create all tables, then top up any column added since they were made."""
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_additive_migrations()
