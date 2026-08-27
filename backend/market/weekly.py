"""
market/weekly.py — Weekly Candle Aggregation & Filter Utilities
================================================================
Aggregates daily candles into weekly candles using PostgreSQL native windowing
and filters for fully completed weekly candles based on ISO calendar weeks.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy import text

from database.database import SessionLocal
from market.calendar import is_trading_day

logger = logging.getLogger("ats.market.weekly")


def aggregate_weekly_candles(company_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Aggregates daily candles into weekly candles using PostgreSQL native aggregation.
    Optionally filters by company_id.
    """
    db = SessionLocal()
    try:
        company_filter = ""
        params = {}
        if company_id:
            company_filter = "AND dc.company_id = :company_id"
            params["company_id"] = company_id

        sql = f"""
        WITH weekly_data AS (
            SELECT 
                dc.company_id,
                date_trunc('week', dc.date)::date AS week_start_date,
                (date_trunc('week', dc.date) + INTERVAL '4 days')::date AS week_end_date,
                (array_agg(dc.open ORDER BY dc.date ASC))[1] AS open,
                MAX(dc.high) AS high,
                MIN(dc.low) AS low,
                (array_agg(dc.close ORDER BY dc.date DESC))[1] AS close,
                SUM(dc.volume) AS volume,
                COUNT(*) AS trading_days,
                CASE 
                    WHEN date_trunc('week', CURRENT_DATE)::date > date_trunc('week', dc.date)::date THEN 'completed'
                    ELSE 'incompleted'
                END AS status
            FROM daily_candles dc
            WHERE EXTRACT(ISODOW FROM dc.date) BETWEEN 1 AND 5
            {company_filter}
            GROUP BY dc.company_id, date_trunc('week', dc.date)
        )
        INSERT INTO weekly_candles (
            id, company_id, week_start_date, week_end_date, 
            open, high, low, close, volume, trading_days, status, created_at, updated_at
        )
        SELECT 
            gen_random_uuid()::varchar(36), 
            company_id, 
            week_start_date, 
            week_end_date,
            open, 
            high, 
            low, 
            close, 
            volume, 
            trading_days,
            status,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM weekly_data
        ON CONFLICT (company_id, week_start_date) DO UPDATE SET
            week_end_date = EXCLUDED.week_end_date,
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            trading_days = EXCLUDED.trading_days,
            status = EXCLUDED.status,
            updated_at = CURRENT_TIMESTAMP;
        """
        
        result = db.execute(text(sql), params)
        db.commit()
        
        affected_rows = result.rowcount
        logger.info(f"[WEEKLY AGG] Successfully processed {affected_rows} weekly candles.")
        return {"status": "success", "processed_rows": affected_rows}
        
    except Exception as e:
        db.rollback()
        logger.error(f"[WEEKLY AGG] Error in weekly candle aggregation: {e}")
        raise e
    finally:
        db.close()


def filter_completed_weekly_candles(
    weekly_candles: List[Dict[str, Any]], current_date: Optional[date] = None
) -> List[Dict[str, Any]]:
    """
    Returns only completed weekly candles using ISO calendar week comparison.
    A weekly candle is COMPLETED if its ISO week (year, week_num) is strictly BEFORE current_date's ISO week.
    If current_date is the last trading day of its week, that week is also considered COMPLETED.
    """
    if not weekly_candles:
        return []
    if current_date is None:
        current_date = date.today()

    curr_iso = current_date.isocalendar()[:2]  # (year, week_number)
    
    week_is_complete = True
    curr = current_date + timedelta(days=1)
    while curr.weekday() <= 6 and curr.isocalendar()[:2] == curr_iso:
        if is_trading_day(curr):
            week_is_complete = False
            break
        curr += timedelta(days=1)

    completed = []
    for c in weekly_candles:
        w_start = c.get("date") or c.get("week_start_date")
        if isinstance(w_start, str):
            w_start_date = datetime.strptime(w_start[:10], "%Y-%m-%d").date()
        elif isinstance(w_start, datetime):
            w_start_date = w_start.date()
        else:
            w_start_date = w_start

        if w_start_date:
            candle_iso = w_start_date.isocalendar()[:2]
            if candle_iso < curr_iso:
                completed.append(c)
            elif candle_iso == curr_iso and week_is_complete:
                completed.append(c)

    return completed if completed else (weekly_candles[:-1] if len(weekly_candles) > 1 else [])
