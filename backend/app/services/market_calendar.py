from datetime import date
from typing import Set

from app.database import SessionLocal
from app.models import MarketHoliday

_holiday_cache: Set[date] = set()
_cache_populated = False


def _populate_cache():
    global _holiday_cache, _cache_populated
    db = SessionLocal()
    try:
        holidays = db.query(MarketHoliday.date).all()
        _holiday_cache = {h[0] for h in holidays}
        _cache_populated = True
    except Exception:
        pass
    finally:
        db.close()


def is_trading_day(check_date: date = None) -> bool:
    """
    Returns True if the given date is a valid trading day in India.
    """
    if check_date is None:
        check_date = date.today()
        
    if check_date.weekday() >= 5:
        return False
        
    if not _cache_populated:
        _populate_cache()
        
    if check_date in _holiday_cache:
        return False
        
    return True
