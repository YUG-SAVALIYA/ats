import sys
import os
import logging

# Ensure the backend directory is in the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text, inspect
from app.data.database import engine
from app.data.models import Base

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ats.init_db")

def init_database():
    """
    Creates all database tables defined in models.py and automatically adds
    any missing columns to existing tables without data loss.
    """
    logger.info("Initializing database schema...")
    
    try:
        # 1. Create missing tables
        Base.metadata.create_all(bind=engine)
        
        # 2. Add any missing columns to existing tables
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())
        
        with engine.connect() as conn:
            for table_name, table in Base.metadata.tables.items():
                if table_name not in existing_tables:
                    continue
                db_cols = {c['name'] for c in insp.get_columns(table_name)}
                for col in table.columns:
                    if col.name not in db_cols:
                        col_type = col.type.compile(dialect=engine.dialect)
                        default_clause = ""
                        if col.default is not None and col.default.is_scalar:
                            default_clause = f" DEFAULT {repr(col.default.arg)}"
                        elif col.nullable:
                            default_clause = " DEFAULT NULL"
                        sql = f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{col.name}" {col_type}{default_clause}'
                        logger.info(f"Adding missing column: {table_name}.{col.name}")
                        conn.execute(text(sql))
                        conn.commit()

        logger.info("✅ Database tables and columns successfully verified & updated.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database: {e}")

if __name__ == "__main__":
    init_database()

