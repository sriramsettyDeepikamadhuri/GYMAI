import asyncio
import urllib.parse
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
import secrets

from app.config import settings, validate_runtime_settings, validate_database_url_for_runtime
from app.database import Base, engine

from app import models
from app.seed_exercises import seed_exercises

from app.routers import (
    auth,
    dashboard,
    workouts,
    history,
    progress,
    profile,
    exercises,
    notifications,
    nutrition,
    admin,
)


validate_runtime_settings()
validate_database_url_for_runtime()

Base.metadata.create_all(
    bind=engine,
)


def _upgrade_workout_sessions_schema() -> None:
    """Small backwards-compatible migration for existing local databases.

    The project uses create_all for its lightweight development setup, which
    does not add columns to an already-created table. These additions keep
    existing GymAI databases usable after upgrading the workout tracker.
    """
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("workout_sessions")}
    additions = {
        "completed_exercises": "JSON",
        "total_sets": "INTEGER NOT NULL DEFAULT 0",
        "completed_sets": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(
                    f"ALTER TABLE workout_sessions ADD COLUMN {name} {definition}"
                ))


_upgrade_workout_sessions_schema()


def _upgrade_users_schema() -> None:
    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    if "gender" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN gender VARCHAR(30)"))
    if "is_admin" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE"))


_upgrade_users_schema()


def _upgrade_notification_schema() -> None:
    inspector = inspect(engine)
    for table in ("user_preferences", "notifications"):
        try: columns = {c["name"] for c in inspector.get_columns(table)}
        except Exception: continue
        if "area" not in columns:
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN area VARCHAR(120) NOT NULL DEFAULT 'Other'"))


_upgrade_notification_schema()

from app.database import SessionLocal
with SessionLocal() as _seed_db:
    seed_exercises(_seed_db)


from app.services.reminders import reminder_loop
from app.database import DATABASE_URL


def _safe_database_label(database_url: str) -> str:
    if not database_url:
        return "database URL not configured"
    if database_url.startswith("sqlite"):
        return "SQLite database"
    try:
        parsed = urllib.parse.urlsplit(database_url)
    except ValueError:
        return "PostgreSQL database (credentials hidden)"
    scheme = parsed.scheme or "postgresql"
    hostname = parsed.hostname or "unknown-host"
    db_name = parsed.path.lstrip("/") or "database"
    return f"{scheme} database at {hostname}/{db_name} (credentials hidden)"


print(f"[GymAI] build 2026-10-18-b | Using database: {_safe_database_label(DATABASE_URL)}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup summary: proves which database is in use and that accounts / history survived the restart.
    try:
        with SessionLocal() as db:
            print(f"[GymAI] users: {db.query(models.User).count()}, saved workouts: {db.query(models.WorkoutSession).count()}")
    except Exception as exc:  # never block startup
        print(f"[GymAI] could not read database summary: {exc}")
    task = asyncio.create_task(reminder_loop()) if settings.reminder_enabled and not settings.is_production else None
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(
    title="GymAI API",
    version="0.1.0",
    lifespan=lifespan,
)

@app.post("/api/internal/reminders/run")
def trigger_reminders(x_scheduler_key: str | None = Header(default=None, alias="X-Scheduler-Key")):
    if not settings.scheduler_api_key:
        raise HTTPException(status_code=503, detail="Scheduler is not configured.")
    if not x_scheduler_key or not secrets.compare_digest(x_scheduler_key, settings.scheduler_api_key):
        raise HTTPException(status_code=403, detail="Invalid scheduler key.")
    from app.services.reminders import send_incomplete_reminders

    sent = send_incomplete_reminders()
    return {"status": "ok", "sent": sent}


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_regex,  # CHANGE: fixes 'Could not create your account' on port 5174
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(workouts.router)
app.include_router(history.router)
app.include_router(progress.router)
app.include_router(profile.router)
app.include_router(exercises.router)
app.include_router(notifications.router)
app.include_router(nutrition.router)
app.include_router(admin.router)

app.mount("/media", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="media")  # CHANGE: absolute path, works from any folder


@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "GymAI API",
    }