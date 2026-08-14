import logging
import uuid
from datetime import datetime
import holidays

from app.database import SessionLocal
from app.models import MarketHoliday

logger = logging.getLogger("ats.holiday_manager")


def fetch_and_store_holidays(year: int) -> int:
    """Fetches Indian Financial Market holidays and upserts them into `market_holidays`."""
    logger.info(f"Fetching financial holidays for India for the year {year}...")
    try:
        in_holidays = holidays.financial_holidays('IN', years=year)
    except Exception as e:
        logger.error(f"Failed to fetch holidays for year {year}: {e}")
        return 0
        
    if not in_holidays:
        logger.warning(f"No holidays found for year {year}.")
        return 0

    db = SessionLocal()
    inserted_count = 0
    
    try:
        for hol_date, hol_name in in_holidays.items():
            existing = db.query(MarketHoliday).filter(MarketHoliday.date == hol_date).first()
            if existing:
                if existing.description != hol_name:
                    existing.description = hol_name
                    existing.updated_at = datetime.utcnow()
            else:
                new_holiday = MarketHoliday(
                    id=str(uuid.uuid4()),
                    date=hol_date,
                    description=hol_name,
                    created_at=datetime.utcnow()
                )
                db.add(new_holiday)
                inserted_count += 1
                
        db.commit()
        logger.info(f"Successfully stored/updated {len(in_holidays)} holidays in DB. New inserts: {inserted_count}")
        return len(in_holidays)
    except Exception as e:
        db.rollback()
        logger.error(f"Error storing holidays in DB: {e}")
        return 0
    finally:
        db.close()
