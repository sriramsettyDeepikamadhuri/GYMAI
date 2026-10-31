import os
import shutil
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parent.parent
# CHANGE: local databases live in ~/.gymai (override with GYMAI_DATA_DIR), NOT inside the project folder.
# Unzipping a new version, deleting the project or starting the server from another folder can no longer
# wipe accounts, history and streaks.
DATA_DIR = Path(os.getenv("GYMAI_DATA_DIR", str(Path.home() / ".gymai")))


def _normalize_postgres_url(url: str) -> str:
    """Normalize PostgreSQL URLs to SQLAlchemy's psycopg2 driver while preserving Neon SSL settings."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _resolve_url(url: str) -> str:
    """Relative SQLite paths (sqlite:///./gymai.db) are stored in DATA_DIR. An existing backend/gymai.db is
    copied there once. Absolute SQLite paths and PostgreSQL URLs are used exactly as given."""
    if settings.is_production:
        return _normalize_postgres_url(url)

    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////") and url != "sqlite://":
        raw = url[len(prefix):]
        if raw and raw != ":memory:" and not Path(raw).is_absolute():
            legacy = (BACKEND_DIR / raw).resolve()
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                target = DATA_DIR / Path(raw).name
                if not target.exists() and legacy.exists():
                    shutil.copy2(legacy, target)  # one-time carry-over of an existing database
                return prefix + str(target)
            except OSError:
                return prefix + str(legacy)  # data folder not writable (some hosts): keep it beside the code
    return _normalize_postgres_url(url)


DATABASE_URL = _resolve_url(settings.database_url)

# SQLite needs this connect_arg for multi-threaded FastAPI use; Postgres uses a small pool with SSL mode enabled.
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif "sslmode=" not in DATABASE_URL:
    connect_args = {"sslmode": "require"}
else:
    connect_args = {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=20,
    pool_size=5,
    max_overflow=10,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
