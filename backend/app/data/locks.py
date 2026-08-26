"""
app.data.locks
==============
PostgreSQL Advisory Lock helpers for multi-worker and multi-process concurrency control.
Ensures scheduled background jobs (3:20, 3:25, 3:40, 4:00, 22:00) execute on only ONE worker.
"""

import logging
import threading
from typing import Optional, Any
from contextlib import contextmanager
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.data.database import SessionLocal

logger = logging.getLogger("ats.locks")

LOCK_JOB_320_FAST_SYNC    = 8001
LOCK_JOB_325_EXECUTION    = 8002
LOCK_JOB_340_POST_SYNC    = 8003
LOCK_JOB_400_SIGNAL_SCAN  = 8004
LOCK_JOB_2200_FULL_SYNC   = 8005
LOCK_RECONCILE_CYCLE      = 8006

_in_memory_locks = set()
_lock_thread_guard = threading.Lock()


def try_advisory_lock(arg1, arg2 = None, db: Optional[Session] = None) -> bool:
    """
    Attempts to acquire an advisory lock without blocking.
    Supports try_advisory_lock(db, lock_id) and try_advisory_lock(lock_id, db=db).
    """
    if isinstance(arg1, (int, float)):
        lock_id = int(arg1)
        session = db or arg2 or SessionLocal()
    else:
        session = arg1 or db or SessionLocal()
        lock_id = int(arg2)

    try:
        if session.bind and hasattr(session.bind, "dialect") and session.bind.dialect.name == "postgresql":
            res = session.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id}
            ).scalar()
            return bool(res)
        else:
            with _lock_thread_guard:
                if lock_id in _in_memory_locks:
                    return False
                _in_memory_locks.add(lock_id)
                return True
    except Exception as exc:
        logger.warning(f"[LOCK] Failed to query advisory lock {lock_id}: {exc}")
        return True


def release_advisory_lock(arg1, arg2 = None, db: Optional[Session] = None) -> bool:
    """
    Releases an advisory lock.
    Supports release_advisory_lock(db, lock_id) and release_advisory_lock(lock_id, db=db).
    """
    if isinstance(arg1, (int, float)):
        lock_id = int(arg1)
        session = db or arg2 or SessionLocal()
    else:
        session = arg1 or db or SessionLocal()
        lock_id = int(arg2)

    try:
        if session.bind and hasattr(session.bind, "dialect") and session.bind.dialect.name == "postgresql":
            res = session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id}
            ).scalar()
            return bool(res)
        else:
            with _lock_thread_guard:
                _in_memory_locks.discard(lock_id)
                return True
    except Exception as exc:
        logger.warning(f"[LOCK] Failed to release advisory lock {lock_id}: {exc}")
        return False


@contextmanager
def advisory_lock_guard(lock_id: int, job_name: str = "ScheduledJob"):
    """
    Context manager that acquires an advisory lock for the duration of the block.
    If the lock is already held by another process, yields acquired=False.
    """
    db = SessionLocal()
    acquired = False
    try:
        acquired = try_advisory_lock(db, lock_id)
        if not acquired:
            logger.info(f"[LOCK] Job '{job_name}' (Lock {lock_id}) skipped — already running on another worker.")
        yield acquired
    finally:
        if acquired:
            release_advisory_lock(db, lock_id)
        db.close()
