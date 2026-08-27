import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from database.database import init_database

if __name__ == "__main__":
    print("[INIT_DB] Initializing database schema...")
    init_database()
    print("[INIT_DB] Database schema initialized successfully.")
