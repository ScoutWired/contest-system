"""Database engine and session management."""
import os
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings, ON_VERCEL

def normalize_url(url: str) -> str:
    # Providers (e.g. Neon via Vercel) may hand out postgres://; SQLAlchemy wants postgresql://
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    return url

def resolve_url() -> str:
    """Resolve the DB URL from the environment (testable without reimport)."""
    url = normalize_url(os.environ.get("DATABASE_URL", settings.DATABASE_URL))
    if (ON_VERCEL or os.environ.get("VERCEL")) and url.startswith("sqlite"):
        raise RuntimeError("SQLite cannot persist on Vercel (read-only filesystem). Set DATABASE_URL to Postgres (e.g. Neon).")
    return url

_db_url = resolve_url()

if _db_url.startswith("sqlite"):
    engine = create_engine(_db_url, connect_args={"check_same_thread": False}, future=True)
else:
    # NullPool: serverless functions must not hold pooled connections across invocations
    engine = create_engine(_db_url, poolclass=NullPool, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
