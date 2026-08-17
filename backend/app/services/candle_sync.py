import time
import logging
import uuid
import requests
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional

from sqlalchemy import func
from app.database import SessionLocal
from app.models import Company, DailyCandle, WeeklyCandle
from app.services.dhan_client import get_dhan_data_client
from app.services.weekly_aggregation import aggregate_weekly_candles

logger = logging.getLogger("ats.candle_sync")

DHAN_HISTORY_URL = "https://api.dhan.co/v2/charts/historical"
SYMBOL_FETCH_DELAY_SEC = 0.3  # Rate limit: 0.3s delay per symbol


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


def _fetch_ohlcv_from_dhan(
    security_id: str,
    exchange_segment: str,
    from_date: str,
    to_date: str,
    client
) -> Optional[List[Dict]]:
    """Fetch OHLCV candles from Dhan Historical Charts API using Premium Data Account."""
    token = client.auth_manager.get_valid_token()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token or ""
    }
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment,
        "instrument": "EQUITY",
        "expiryCode": 0,
        "fromDate": from_date,
        "toDate": to_date
    }
    try:
        resp = requests.post(DHAN_HISTORY_URL, json=payload, headers=headers, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"[CANDLE SYNC] Dhan API returned HTTP {resp.status_code} for sec_id={security_id}")
            return None

        data = resp.json()
        if not isinstance(data, dict):
            return None

        timestamps = data.get("timestamp", [])
        opens      = data.get("open", [])
        highs      = data.get("high", [])
        lows       = data.get("low", [])
        closes     = data.get("close", [])
        volumes    = data.get("volume", [])

        if not timestamps or not closes:
            return None

        candles = []
        for i in range(len(timestamps)):
            try:
                ts = timestamps[i]
                if isinstance(ts, (int, float)):
                    # Convert UTC epoch timestamp to IST date (+5:30)
                    candle_date = (datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)).date()
                else:
                    candle_date = datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()

                # Filter out Saturday (5) and Sunday (6) daily candles for NSE/BSE Equity
                if candle_date.weekday() >= 5:
                    continue
                candles.append({
                    "date":   candle_date,
                    "open":   float(opens[i]   if i < len(opens)   else 0),
                    "high":   float(highs[i]   if i < len(highs)   else 0),
                    "low":    float(lows[i]    if i < len(lows)    else 0),
                    "close":  float(closes[i]  if i < len(closes)  else 0),
                    "volume": int(volumes[i]   if i < len(volumes) else 0),
                })
            except Exception:
                continue

        return candles

    except Exception as exc:
        logger.warning(f"[CANDLE SYNC] Fetch error for sec_id={security_id}: {exc}")
        return None


def _upsert_daily_candles(db, company_id: str, candles: List[Dict]) -> int:
    """Upsert daily candles into DB."""
    existing = {
        r.date: r
        for r in db.query(DailyCandle).filter(DailyCandle.company_id == company_id).all()
    }

    count = 0
    for c in candles:
        if c["date"] in existing:
            row = existing[c["date"]]
            row.open   = c["open"]
            row.high   = c["high"]
            row.low    = c["low"]
            row.close  = c["close"]
            row.volume = c["volume"]
        else:
            db.add(DailyCandle(
                id=str(uuid.uuid4()),
                company_id=company_id,
                date=c["date"],
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
                created_at=datetime.utcnow()
            ))
            count += 1

    db.commit()
    return count


def sync_candles_for_company(
    company_id: str,
    security_id: str,
    exchange_segment: str = "NSE_EQ",
    force_full: bool = False
) -> Dict[str, Any]:
    """
    Detects missing candles for a company and selectively fetches missing dates from Dhan API.
    """
    client = get_dhan_data_client()
    today = date.today()

    latest_db_date = get_latest_candle_date_from_db(company_id)

    if not force_full and latest_db_date:
        if latest_db_date >= today:
            logger.info(f"[CANDLE SYNC] Company {security_id} already up-to-date ({latest_db_date}). Skipping API call.")
            return {"daily_inserted": 0, "weekly_inserted": 0, "skipped": True}

        from_date = latest_db_date.strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        logger.info(f"[CANDLE SYNC] Missing candles check for sec_id={security_id} (DB max: {latest_db_date}). Fetching {from_date} to {to_date}...")
    else:
        from_date = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")
        logger.info(f"[CANDLE SYNC] Initializing full candle sync for sec_id={security_id} ({from_date} to {to_date})...")

    candles = _fetch_ohlcv_from_dhan(security_id, exchange_segment, from_date, to_date, client)
    if not candles:
        logger.warning(f"[CANDLE SYNC] No new data returned from Dhan for security_id={security_id}")
        return {"daily_inserted": 0, "weekly_inserted": 0, "skipped": False}

    db = SessionLocal()
    daily_inserted = 0
    weekly_inserted = 0

    try:
        daily_inserted = _upsert_daily_candles(db, company_id, candles)
        logger.info(f"[CANDLE SYNC] {security_id}: {daily_inserted} new daily candles stored in DB")

        agg_result = aggregate_weekly_candles(company_id=company_id)
        weekly_inserted = agg_result.get("processed_rows", 0)
        logger.info(f"[CANDLE SYNC] {security_id}: {weekly_inserted} weekly candles updated natively")

    except Exception as exc:
        db.rollback()
        logger.error(f"[CANDLE SYNC] DB error for company_id={company_id}: {exc}")
    finally:
        db.close()

    return {"daily_inserted": daily_inserted, "weekly_inserted": weekly_inserted, "skipped": False}


def sync_actionable_companies(delay_sec: float = SYMBOL_FETCH_DELAY_SEC) -> Dict[str, Any]:
    """
    Checks missing candles and fetches updates ONLY for companies that have an active signal
    or an open/pending trade. This is intended for the 3:20 PM fast sync.
    """
    logger.info(f"[CANDLE SYNC] Starting fast actionable candle sync...")
    from app.models import Signal, Trade
    db = SessionLocal()
    try:
        # Get companies with active signals
        signal_comp_ids = [r[0] for r in db.query(Signal.company_id).filter(Signal.status == "ACTIVE").all()]
        
        # Get companies with open or pending trades
        trade_comp_ids = [r[0] for r in db.query(Trade.company_id).filter(Trade.status.in_(["OPEN", "ENTRY_PENDING", "EXIT_PENDING"])).all()]
        
        actionable_ids = set(signal_comp_ids + trade_comp_ids)
        
        if not actionable_ids:
            logger.info("[CANDLE SYNC] No actionable companies found for fast sync.")
            return {"total_active_companies": 0, "companies_synced": 0}

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
    Checks missing candles and fetches updates for all active companies with a strict 0.3s rate limit per symbol.
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

            # Strict 0.3s rate limit delay per symbol
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


def get_daily_candles_from_db(company_id: str, limit: int = 60) -> List[Dict]:
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


def get_weekly_candles_from_db(company_id: str, limit: int = 30) -> List[Dict]:
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


def get_monthly_candles_from_db(company_id: str, limit: int = 30) -> List[Dict]:
    """
    Read the most recent daily candles from DB and aggregate them into monthly candles.
    We need enough daily candles to form `limit` monthly candles. A limit of 30 months is ~ 600 days.
    """
    db = SessionLocal()
    try:
        # Fetch up to 800 daily candles to ensure we cover up to limit months
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
        
        monthly_map = {}
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

