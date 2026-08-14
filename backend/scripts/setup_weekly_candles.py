import os
import sys
from sqlalchemy import text

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database import engine, Base
from models import WeeklyCandle
from services.weekly_aggregation import aggregate_weekly_candles

def setup():
    print("Dropping existing weekly_candles table if it exists...")
    # Because of the schema change, we need to drop the old table and recreate
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS weekly_candles CASCADE;"))
    
    print("Creating new weekly_candles table...")
    WeeklyCandle.__table__.create(engine)
    print("Table created successfully with unique constraints.")

    print("Running initial full backfill of weekly candles...")
    try:
        result = aggregate_weekly_candles()
        print(f"Backfill successful! Processed {result['processed_rows']} weekly candles.")
    except Exception as e:
        print(f"Error during backfill: {e}")

if __name__ == "__main__":
    setup()
