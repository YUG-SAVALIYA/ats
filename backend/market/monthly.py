"""
market/monthly.py — Monthly Candle Aggregation & Query Helpers
==============================================================
Aggregates daily candles into monthly candles and retrieves monthly bars for strategies.
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any
from database.database import SessionLocal
from database.models import DailyCandle

logger = logging.getLogger("ats.market.monthly")


def get_monthly_candles_from_db(company_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Read the most recent daily candles from DB and aggregate them into monthly candles.
    Fetches up to 800 daily candles to ensure coverage for up to `limit` monthly candles.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(DailyCandle)
            .filter(DailyCandle.company_id == company_id)
            .order_by(DailyCandle.date.desc())
            .limit(800)
            .all()
        )
        if not rows:
            return []

        rows = sorted(rows, key=lambda r: r.date)
        
        monthly_map: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            month_key = r.date.strftime("%Y-%m")
            if month_key not in monthly_map:
                monthly_map[month_key] = {
                    "date": r.date,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume
                }
            else:
                monthly_map[month_key]["high"] = max(monthly_map[month_key]["high"], r.high)
                monthly_map[month_key]["low"] = min(monthly_map[month_key]["low"], r.low)
                monthly_map[month_key]["close"] = r.close
                monthly_map[month_key]["volume"] += r.volume
                
        monthly_list = list(monthly_map.values())
        monthly_list.sort(key=lambda c: c["date"])
        
        return monthly_list[-limit:]
    finally:
        db.close()
