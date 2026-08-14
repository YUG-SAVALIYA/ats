from app.config import load_config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Load DATABASE_URL centrally from Config system (Pure PostgreSQL)
cfg = load_config()
DATABASE_URL = cfg.database_url.strip()

if not DATABASE_URL:
    raise ValueError("[DATABASE] DATABASE_URL is missing in .env or Config! Please set a valid PostgreSQL connection string.")

# Normalize legacy postgres:// to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# PostgreSQL engine configuration with connection pooling & auto-ping
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
