"""
market/calendar.py — Indian Financial Market Calendar & Holiday Management
==========================================================================
Maintains holiday schedule and determines trading day validity for NSE/BSE.
"""

from __future__ import annotations

import uuid
import logging
from datetime import date, datetime
from typing import Set, Optional
import holidays

from database.database import SessionLocal
from database.models import MarketHoliday

logger = logging.getLogger("ats.market.calendar")

_holiday_cache: Set[date] = set()
_cache_populated = False


def _populate_cache():
    global _holiday_cache, _cache_populated
    db = SessionLocal()
    try:
        holidays_db = db.query(MarketHoliday.date).all()
        _holiday_cache = {h[0] for h in holidays_db}
        _cache_populated = True
    except Exception as exc:
        logger.warning(f"[CALENDAR] Error reading holiday cache from DB: {exc}")
    finally:
        db.close()


def is_trading_day(check_date: Optional[date] = None) -> bool:
    """Returns True if the given date is a valid trading day in India (Mon-Fri, non-holiday)."""
    if check_date is None:
        check_date = date.today()

    # Saturday (5) or Sunday (6)
    if check_date.weekday() >= 5:
        return False

    if not _cache_populated:
        _populate_cache()

    if check_date in _holiday_cache:
        return False

    return True


def fetch_and_store_holidays(year: int) -> int:
    """Fetches Indian Financial Market holidays and upserts them into `market_holidays`."""
    global _cache_populated
    logger.info(f"[CALENDAR] Fetching financial holidays for India for the year {year}...")
    try:
        in_holidays = holidays.financial_holidays("IN", years=year)
    except Exception as e:
        logger.error(f"[CALENDAR] Failed to fetch holidays for year {year}: {e}")
        return 0

    if not in_holidays:
        logger.warning(f"[CALENDAR] No holidays found for year {year}.")
        return 0

    db = SessionLocal()
    inserted_count = 0

    try:
        for hol_date, hol_name in in_holidays.items():
            existing = db.query(MarketHoliday).filter(MarketHoliday.date == hol_date).first()
            if existing:
                if existing.description != hol_name:
                    existing.description = hol_name
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
        _cache_populated = False  # Invalidate cache so it re-populates
        logger.info(f"[CALENDAR] Stored/updated {len(in_holidays)} holidays in DB. New inserts: {inserted_count}")
        return len(in_holidays)
    except Exception as e:
        db.rollback()
        logger.error(f"[CALENDAR] Error storing holidays in DB: {e}")
        return 0
    finally:
        db.close()
