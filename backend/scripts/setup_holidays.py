import os
import sys
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database import engine
from models import MarketHoliday
from utils.holiday_manager import fetch_and_store_holidays

def setup():
    print("Creating market_holidays table...")
    MarketHoliday.__table__.create(engine, checkfirst=True)
    print("Table created successfully.")

    current_year = datetime.now().year
    print(f"Fetching and storing holidays for {current_year}...")
    
    count = fetch_and_store_holidays(current_year)
    print(f"Success! {count} holidays populated for {current_year}.")

if __name__ == "__main__":
    setup()
