"""
market/candles.py — Candle Synchronization & Data Retrieval Service
===================================================================
Synchronizes OHLCV daily candles from Dhan, self-heals missing candle gaps,
triggers weekly aggregations, and provides database candle queries.
"""

from __future__ import annotations

import time
import logging
import uuid
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional

from sqlalchemy import func
from database.database import SessionLocal
from database.models import Company, DailyCandle, WeeklyCandle
from dhan.market import fetch_historical_ohlcv
from market.weekly import aggregate_weekly_candles
from market.monthly import get_monthly_candles_from_db

from market.calendar import is_trading_day

logger = logging.getLogger("ats.market.candles")

SYMBOL_FETCH_DELAY_SEC = 0.5  # Rate limit: 0.5s delay per symbol


def get_latest_candle_date_from_db(company_id: str) -> Optional[date]:
    """Returns the latest daily candle date stored in DB for a company."""
    db = SessionLocal()
    try:
        latest = (
            db.query(func.max(DailyCandle.date))
            .filter(DailyCandle.company_id == company_id)
            .scalar()
        )
        return latest
    except Exception:
        return None
    finally:
        db.close()


def _upsert_daily_candles(db, company_id: str, candles: List[Dict[str, Any]]) -> Dict[str, int]:
    """Upsert daily candles into DB. Skips weekends and market holidays."""
    existing = {
        r.date: r
        for r in db.query(DailyCandle).filter(DailyCandle.company_id == company_id).all()
    }

    inserted = 0
    updated = 0
    for c in candles:
        c_date = c["date"]
        if isinstance(c_date, str):
            c_date = datetime.strptime(c_date[:10], "%Y-%m-%d").date()

        # Strictly ignore Saturdays (5), Sundays (6), and Indian market holidays
        if c_date.weekday() in (5, 6) or not is_trading_day(c_date):
            continue

        if c_date in existing:
            row = existing[c_date]
            row.open   = c["open"]
            row.high   = c["high"]
            row.low    = c["low"]
            row.close  = c["close"]
            row.volume = c["volume"]
            updated += 1
        else:
            db.add(DailyCandle(
                id=str(uuid.uuid4()),
                company_id=company_id,
                date=c_date,
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
                created_at=datetime.utcnow()
            ))
            inserted += 1

    db.commit()
    return {"inserted": inserted, "updated": updated}


def sync_candles_for_company(
    company_id: str,
    security_id: str,
    exchange_segment: str = "NSE_EQ",
    force_full: bool = False
) -> Dict[str, Any]:
    """
    Detects missing candles for a company and selectively fetches missing dates from Dhan API.
    """
    today = date.today()
    latest_db_date = get_latest_candle_date_from_db(company_id)

    if not force_full and latest_db_date:
        # 5-Day Gap Check: Always fetch at least the last 5 days to self-heal missing candles.
        lookback_date = today - timedelta(days=5)
        if latest_db_date < lookback_date:
            from_date = latest_db_date.strftime("%Y-%m-%d")
        else:
            from_date = lookback_date.strftime("%Y-%m-%d")
            
        to_date = today.strftime("%Y-%m-%d")
        logger.info(f"[CANDLE SYNC] 5-Day Gap Check for sec_id={security_id}. Fetching {from_date} to {to_date}...")
    else:
        from_date = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        logger.info(f"[CANDLE SYNC] Initializing full candle sync for sec_id={security_id} ({from_date} to {to_date})...")

    candles = fetch_historical_ohlcv(security_id, exchange_segment, from_date, to_date)
    if not candles:
        logger.warning(f"[CANDLE SYNC] No new data returned from Dhan for security_id={security_id}")
        return {"daily_inserted": 0, "daily_updated": 0, "weekly_inserted": 0, "skipped": False}

    db = SessionLocal()
    daily_inserted = 0
    daily_updated = 0
    weekly_inserted = 0

    try:
        counts = _upsert_daily_candles(db, company_id, candles)
        daily_inserted = counts.get("inserted", 0)
        daily_updated = counts.get("updated", 0)
        logger.info(f"[CANDLE SYNC] {security_id}: {daily_inserted} new daily candles stored, {daily_updated} updated in DB")

        agg_result = aggregate_weekly_candles(company_id=company_id)
        weekly_inserted = agg_result.get("processed_rows", 0)
        logger.info(f"[CANDLE SYNC] {security_id}: {weekly_inserted} weekly candles updated natively")

    except Exception as exc:
        db.rollback()
        logger.error(f"[CANDLE SYNC] DB error for company_id={company_id}: {exc}")
    finally:
        db.close()

    return {
        "daily_inserted": daily_inserted,
        "daily_updated": daily_updated,
        "weekly_inserted": weekly_inserted,
        "skipped": False
    }


def sync_actionable_companies(delay_sec: float = SYMBOL_FETCH_DELAY_SEC) -> Dict[str, Any]:
    """
    Checks missing candles and fetches updates ONLY for companies with active signals or open/pending trades.
    Intended for 3:20 PM fast pre-market-execution sync.
    """
    logger.info("[CANDLE SYNC] Starting fast actionable candle sync...")
    from database.models import Signal, Trade
    db = SessionLocal()
    try:
        signal_comp_ids = [r[0] for r in db.query(Signal.company_id).filter(Signal.status == "PENDING").all()]
        trade_comp_ids = [r[0] for r in db.query(Trade.company_id).filter(Trade.ats_state.in_(["OPEN", "ENTRY_PENDING", "PARTIAL_EXIT", "EXIT_REQUESTED"])).all()]
        actionable_ids = set(signal_comp_ids + trade_comp_ids)
        
        if not actionable_ids:
            logger.info("[CANDLE SYNC] No actionable companies found for fast sync.")
            return {"total_actionable_companies": 0, "companies_synced": 0}

        companies = (
            db.query(Company)
            .filter(
                Company.id.in_(actionable_ids),
                Company.is_active == True,
                Company.dhan_security_id != None,
                Company.dhan_security_id != ""
            )
            .all()
        )
    finally:
        db.close()

    total_daily = 0
    total_weekly = 0
    synced = 0
    skipped = 0

    for company in companies:
        try:
            result = sync_candles_for_company(
                company_id=company.id,
                security_id=company.dhan_security_id,
                exchange_segment="NSE_EQ"
            )
            if result.get("skipped"):
                skipped += 1
            else:
                total_daily += result.get("daily_inserted", 0)
                total_weekly += result.get("weekly_inserted", 0)
                synced += 1

            if delay_sec > 0:
                time.sleep(delay_sec)

        except Exception as exc:
            logger.warning(f"[CANDLE SYNC] Skipping {company.trading_symbol}: {exc}")

    summary = {
        "total_actionable_companies": len(companies),
        "companies_synced": synced,
        "companies_skipped_up_to_date": skipped,
        "daily_candles_inserted": total_daily,
        "weekly_candles_inserted": total_weekly,
        "rate_limit_delay_sec": delay_sec
    }
    logger.info(f"[CANDLE SYNC] Fast actionable sync complete: {summary}")
    return summary


def sync_all_active_companies(limit: int = 4000, delay_sec: float = SYMBOL_FETCH_DELAY_SEC) -> Dict[str, Any]:
    """
    Checks missing candles and fetches updates for all active companies with a 0.3s rate limit per symbol.
    """
    logger.info(f"[CANDLE SYNC] Starting batch candle sync for up to {limit} active companies (Rate Limit: {delay_sec}s/symbol)...")
    db = SessionLocal()
    try:
        companies = (
            db.query(Company)
            .filter(
                Company.is_active == True,
                Company.dhan_security_id != None,
                Company.dhan_security_id != ""
            )
            .limit(limit)
            .all()
        )
    finally:
        db.close()

    total_daily = 0
    total_weekly = 0
    synced = 0
    skipped = 0

    for company in companies:
        try:
            result = sync_candles_for_company(
                company_id=company.id,
                security_id=company.dhan_security_id,
                exchange_segment="NSE_EQ"
            )
            if result.get("skipped"):
                skipped += 1
            else:
                total_daily += result.get("daily_inserted", 0)
                total_weekly += result.get("weekly_inserted", 0)
                synced += 1

            if delay_sec > 0:
                time.sleep(delay_sec)

        except Exception as exc:
            logger.warning(f"[CANDLE SYNC] Skipping {company.trading_symbol}: {exc}")

    summary = {
        "total_active_companies": len(companies),
        "companies_synced": synced,
        "companies_skipped_up_to_date": skipped,
        "daily_candles_inserted": total_daily,
        "weekly_candles_inserted": total_weekly,
        "rate_limit_delay_sec": delay_sec
    }
    logger.info(f"[CANDLE SYNC] Batch sync complete: {summary}")
    return summary


def get_daily_candles_from_db(company_id: str, limit: int = 300) -> List[Dict[str, Any]]:
    """Read the most recent N daily candles from DB for a company in ascending date order."""
    db = SessionLocal()
    try:
        rows = (
            db.query(DailyCandle)
            .filter(DailyCandle.company_id == company_id)
            .order_by(DailyCandle.date.desc())
            .limit(limit)
            .all()
        )
        rows = sorted(rows, key=lambda r: r.date)
        return [
            {
                "date":   str(r.date),
                "open":   r.open,
                "high":   r.high,
                "low":    r.low,
                "close":  r.close,
                "volume": r.volume
            }
            for r in rows
        ]
    finally:
        db.close()


def get_weekly_candles_from_db(company_id: str, limit: int = 150) -> List[Dict[str, Any]]:
    """Read the most recent N weekly candles from DB for a company in ascending date order."""
    db = SessionLocal()
    try:
        rows = (
            db.query(WeeklyCandle)
            .filter(WeeklyCandle.company_id == company_id)
            .order_by(WeeklyCandle.week_start_date.desc())
            .limit(limit)
            .all()
        )
        rows = sorted(rows, key=lambda r: r.week_start_date)
        return [
            {
                "date":          str(r.week_start_date),
                "week_end_date": str(r.week_end_date),
                "open":          r.open,
                "high":          r.high,
                "low":           r.low,
                "close":         r.close,
                "volume":        r.volume
            }
            for r in rows
        ]
    finally:
        db.close()
