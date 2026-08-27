"""
database/database.py — ATS Database Connection & Session Management
===================================================================
Provides PostgreSQL SQLAlchemy engine, session factory, Base declarative class,
and database dependency generator for FastAPI routes.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from config import load_config

# Load DATABASE_URL centrally from Config system (Pure PostgreSQL)
cfg = load_config()
DATABASE_URL = cfg.database_url.strip()

if not DATABASE_URL:
    raise ValueError(
        "[DATABASE] DATABASE_URL is missing in Config! "
        "Please set a valid PostgreSQL connection string in .env."
    )

# Normalize legacy postgres:// to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# PostgreSQL engine configuration with connection pooling & auto-ping
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=engine,
)

Base = declarative_base()


def get_db():
    """FastAPI dependency for yielding a transactional database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database():
    """
    Creates all database tables defined in models.py if they do not exist.
    Uses 'CREATE TABLE IF NOT EXISTS' safely without altering existing data.
    """
    import database.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
