import sys
import os
import logging

# Ensure the backend directory is in the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import engine
from app.models import Base

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ats.init_db")

def init_database():
    """
    Creates all database tables defined in models.py.
    SQLAlchemy's create_all() automatically uses 'CREATE TABLE IF NOT EXISTS',
    meaning it will safely skip tables that already exist and WILL NOT delete or affect your existing data.
    """
    logger.info("Initializing database schema...")
    
    try:
        # This scans models.py and creates missing tables safely
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables successfully created or already exist.")
        logger.info("Your existing data is perfectly safe and untouched!")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")

if __name__ == "__main__":
    init_database()
