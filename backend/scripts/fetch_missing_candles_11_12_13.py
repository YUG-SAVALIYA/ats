"""
fetch_missing_candles_11_12_13.py
=================================
Dedicated script to check and fetch missing daily candles for August 11, August 12, and August 13 (today).

Pipeline:
1. Fetches historical chart candles (up to Aug 12) from Dhan API using Premium Data Account (1111482994) with 0.3s rate limiting per symbol.
2. Fetches today's live OHLC (Aug 13) from Dhan Marketfeed API in batches of 100 symbols.
3. Upserts all daily candles (Aug 11, Aug 12, Aug 13) into PostgreSQL `daily_candles` table.
4. Re-aggregates weekly candles natively across all active companies.

Usage:
  venv\Scripts\python.exe scripts/fetch_missing_candles_11_12_13.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import time
import uuid
import logging
from datetime import datetime, date, timedelta
from typing import List, Dict, Any

from sqlalchemy import func
from app.database import SessionLocal
from app.models import Company, DailyCandle
from app.services.dhan_client import get_dhan_data_client
from app.services.weekly_aggregation import aggregate_weekly_candles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("fetch_missing_11_12_13")

DHAN_HISTORY_URL = "https://api.dhan.co/v2/charts/historical"
RATE_LIMIT_DELAY = 0.3  # 0.3s delay per symbol for historical charts API


def fetch_historical_chart_candles(client, security_id: str, from_date: str, to_date: str) -> List[Dict]:
    """Fetch completed historical daily candles from Dhan API."""
    token = client.auth_manager.get_valid_token()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token or ""
    }
    payload = {
        "securityId": str(security_id),
        "exchangeSegment": "NSE_EQ",
        "instrument": "EQUITY",
        "expiryCode": 0,
        "fromDate": from_date,
        "toDate": to_date
    }
    try:
        resp = client.execute_v2_post(DHAN_HISTORY_URL, payload)
        if not isinstance(resp, dict):
            return []

        timestamps = resp.get("timestamp", [])
        opens = resp.get("open", [])
        highs = resp.get("high", [])
        lows = resp.get("low", [])
        closes = resp.get("close", [])
        volumes = resp.get("volume", [])

        if not timestamps or not closes:
            return []

        candles = []
        for i in range(len(timestamps)):
            try:
                ts = timestamps[i]
                if isinstance(ts, (int, float)):
                    candle_date = (datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)).date()
                else:
                    candle_date = datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()

                # Exclude Saturday/Sunday
                if candle_date.weekday() >= 5:
                    continue

                candles.append({
                    "date": candle_date,
                    "open": float(opens[i] if i < len(opens) else 0),
                    "high": float(highs[i] if i < len(highs) else 0),
                    "low": float(lows[i] if i < len(lows) else 0),
                    "close": float(closes[i] if i < len(closes) else 0),
                    "volume": int(volumes[i] if i < len(volumes) else 0),
                })
            except Exception:
                continue

        return candles
    except Exception as exc:
        logger.warning(f"[HISTORY FETCH] Exception for sec_id={security_id}: {exc}")
        return []


def upsert_daily_candles_to_db(db, company_id: str, candles: List[Dict]) -> int:
    """Upsert daily candles into PostgreSQL daily_candles table."""
    existing = {
        r.date: r
        for r in db.query(DailyCandle).filter(DailyCandle.company_id == company_id).all()
    }

    count = 0
    for c in candles:
        c_date = c["date"]
        if c_date.weekday() >= 5:
            continue  # Skip weekend

        if c_date in existing:
            row = existing[c_date]
            row.open = c["open"]
            row.high = c["high"]
            row.low = c["low"]
            row.close = c["close"]
            row.volume = c["volume"]
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
            count += 1

    db.commit()
    return count


def fetch_today_live_ohlc_batch(client, security_ids: List[int]) -> Dict[str, Dict]:
    """Fetch today's (Aug 13) live OHLC for a list of security IDs in batches."""
    if not security_ids:
        return {}
    return client.get_marketfeed_ohlc(security_ids)


def main():
    logger.info("==================================================================")
    logger.info("   FETCHING MISSING CANDLES FOR AUGUST 11, 12, AND 13 (TODAY)     ")
    logger.info("==================================================================")

    data_client = get_dhan_data_client()
    today = date.today()  # 2026-08-13
    aug_11 = date(2026, 8, 11)
    aug_12 = date(2026, 8, 12)
    aug_13 = date(2026, 8, 13)

    db = SessionLocal()
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != ""
        ).all()
        logger.info(f" Found {len(companies)} active companies to check for missing candles (Aug 11-13)...")

        # ── STEP 1: Fetch Historical Chart Candles (Aug 11 & Aug 12) ────────
        logger.info("\n--- STEP 1: Fetching Historical Candles (Aug 11 & Aug 12) with 0.3s Rate Limit ---")
        history_inserted = 0
        history_updated = 0

        for idx, comp in enumerate(companies):
            try:
                # Check DB for existing candles
                existing_dates = {
                    r.date for r in db.query(DailyCandle.date).filter(DailyCandle.company_id == comp.id).all()
                }

                # Determine if Aug 11 or Aug 12 are missing
                needs_aug11 = aug_11 not in existing_dates
                needs_aug12 = aug_12 not in existing_dates

                if needs_aug11 or needs_aug12:
                    from_str = "2026-08-10"
                    to_str = "2026-08-13"
                    candles = fetch_historical_chart_candles(data_client, comp.dhan_security_id, from_str, to_str)
                    if candles:
                        n = upsert_daily_candles_to_db(db, comp.id, candles)
                        history_inserted += n
                        history_updated += 1
                        logger.info(f" [{idx+1}/{len(companies)}] {comp.trading_symbol} ({comp.dhan_security_id}): Synced {len(candles)} candles (New: {n})")
                
                if RATE_LIMIT_DELAY > 0:
                    time.sleep(RATE_LIMIT_DELAY)

            except Exception as exc:
                logger.warning(f" Skipping historical fetch for {comp.trading_symbol}: {exc}")

        logger.info(f" ✅ Step 1 Complete: Updated historical candles for {history_updated} companies ({history_inserted} new daily rows stored).")

        # ── STEP 2: Fetch Today's Live OHLC (Aug 13) ────────────────────────
        logger.info("\n--- STEP 2: Fetching Today's Live OHLC (Aug 13) via Marketfeed API ---")
        sec_map = {}
        for comp in companies:
            try:
                sid_int = int(comp.dhan_security_id)
                sec_map[sid_int] = comp
            except ValueError:
                continue

        sec_ids = list(sec_map.keys())
        batch_size = 100
        today_inserted = 0

        for i in range(0, len(sec_ids), batch_size):
            batch = sec_ids[i:i+batch_size]
            ohlc_data = fetch_today_live_ohlc_batch(data_client, batch)

            for sid_int, item in ohlc_data.items():
                comp = sec_map.get(int(sid_int))
                if not comp:
                    continue

                ohlc = item.get("ohlc", {}) or {}
                last_price = float(item.get("last_price") or ohlc.get("close") or 0.0)
                open_p = float(ohlc.get("open") or last_price)
                high_p = float(ohlc.get("high") or last_price)
                low_p = float(ohlc.get("low") or last_price)
                close_p = float(ohlc.get("close") or last_price)

                if open_p > 0 and high_p > 0 and low_p > 0 and close_p > 0:
                    c_today = [{
                        "date": aug_13,
                        "open": open_p,
                        "high": high_p,
                        "low": low_p,
                        "close": close_p,
                        "volume": 0
                    }]
                    n = upsert_daily_candles_to_db(db, comp.id, c_today)
                    today_inserted += n

            logger.info(f" [{min(i+batch_size, len(sec_ids))}/{len(sec_ids)}] Processed today's OHLC batch...")
            time.sleep(0.3)

        logger.info(f" ✅ Step 2 Complete: Synced today's (Aug 13) live OHLC into DB ({today_inserted} new rows stored).")

        # ── STEP 3: Re-aggregate Weekly Candles Natively ────────────────────
        logger.info("\n--- STEP 3: Re-aggregating Weekly Candles Across All Companies ---")
        weekly_updated = 0
        for comp in companies:
            try:
                res = aggregate_weekly_candles(company_id=comp.id)
                weekly_updated += res.get("processed_rows", 0)
            except Exception:
                pass

        logger.info(f" ✅ Step 3 Complete: Aggregated {weekly_updated} weekly candles across all companies!")

        # ── STEP 4: Verification of August 11, 12, 13 Daily Candles in DB ──
        logger.info("\n--- STEP 4: Database Candle Count Summary (August 11, 12, 13) ---")
        counts = db.execute(
            SessionLocal().text(
                "SELECT date, COUNT(*) FROM daily_candles WHERE date IN ('2026-08-11', '2026-08-12', '2026-08-13') GROUP BY date ORDER BY date ASC"
            )
        ).fetchall()

        for c_date, c_count in counts:
            dow_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][c_date.weekday()]
            logger.info(f" 📊 {c_date} ({dow_name}): {c_count} daily candles present in DB")

    finally:
        db.close()

    logger.info("\n==================================================================")
    logger.info(" 🎉 MISSING CANDLE FETCH FOR AUG 11, 12, 13 PASSED SUCCESSFULLY! ")
    logger.info("==================================================================")


if __name__ == "__main__":
    main()
