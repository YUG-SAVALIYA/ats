import os
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.data.database
from app.data.models import Base

TEST_DB_FILE = os.path.join(os.path.dirname(__file__), "test_ats_tmp.db")
TEST_DB_URL = f"sqlite:///{TEST_DB_FILE}"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)

@event.listens_for(test_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

TestSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    bind=test_engine,
)

# Configure global SessionLocal to bind to test_engine
app.data.database.SessionLocal.configure(bind=test_engine)
app.data.database.engine = test_engine

@pytest.fixture(scope="session", autouse=True)
def patch_database_engine():
    app.data.database.SessionLocal.configure(bind=test_engine)
    app.data.database.engine = test_engine
    Base.metadata.create_all(bind=test_engine)
    yield
    test_engine.dispose()
    Base.metadata.drop_all(bind=test_engine)
    for ext in ["", "-shm", "-wal"]:
        f_path = f"{TEST_DB_FILE}{ext}"
        if os.path.exists(f_path):
            try:
                os.remove(f_path)
            except Exception:
                pass

@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
