"""
sync_missing_candles.py — Sync Missing Daily & Weekly Candles from Dhan HQ
===========================================================================
1. Identifies missing daily candles for all active companies.
2. Fetches missing OHLCV candles from Dhan HQ Data API with a 0.5s rate-limit delay.
3. Saves daily candles to `daily_candles` table.
4. Aggregates weekly candles into `weekly_candles` table with `status = 'completed'` / `'incompleted'`.
"""

import sys
import os
import time
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

# Ensure backend directory is in path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config import load_config
from database.database import SessionLocal, engine
from database.models import Company, DailyCandle, WeeklyCandle
from market.calendar import is_trading_day
from market.weekly import aggregate_weekly_candles
from dhan.market import fetch_historical_ohlcv
from sqlalchemy import func, text

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("ats.sync_missing_candles")

RATE_LIMIT_DELAY_SEC = 0.5  # Exactly 0.5s delay between requests


def ensure_weekly_candle_status_column():
    """Ensures `status` column exists in `weekly_candles` table."""
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE weekly_candles ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'completed';"))
        conn.commit()


def get_company_latest_candle_date(db, company_id: str) -> Optional[date]:
    """Returns the latest daily candle date in DB for a specific company."""
    return db.query(func.max(DailyCandle.date)).filter(DailyCandle.company_id == company_id).scalar()


def upsert_daily_candles(db, company_id: str, candles: List[Dict[str, Any]]) -> Dict[str, int]:
    """Upsert daily candles into DB. Skips weekends and market holidays."""
    if not candles:
        return {"inserted": 0, "updated": 0}

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
            row.open = float(c["open"])
            row.high = float(c["high"])
            row.low = float(c["low"])
            row.close = float(c["close"])
            row.volume = int(c["volume"])
            updated += 1
        else:
            new_row = DailyCandle(
                company_id=company_id,
                date=c_date,
                open=float(c["open"]),
                high=float(c["high"]),
                low=float(c["low"]),
                close=float(c["close"]),
                volume=int(c["volume"]),
            )
            db.add(new_row)
            existing[c_date] = new_row
            inserted += 1

    db.commit()
    return {"inserted": inserted, "updated": updated}


def sync_company_candles(
    db,
    company: Company,
    today: date,
    force_full: bool = False
) -> Dict[str, Any]:
    """Fetches missing daily candles from Dhan and updates weekly candles with status."""
    sec_id = str(company.dhan_security_id or "").strip()
    if not sec_id:
        return {"status": "skipped", "reason": "No security ID"}

    latest_date = get_company_latest_candle_date(db, company.id)

    # Determine date range to fetch
    if not force_full and latest_date:
        # Check if already up to date
        if latest_date >= (today - timedelta(days=1)):
            # Already up to date (up to yesterday or today)
            from_date = (today - timedelta(days=3)).strftime("%Y-%m-%d")
        else:
            # Overlap by 2 days for gap healing
            from_date = (latest_date - timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        # Full 1-year historical fetch
        from_date = (today - timedelta(days=365)).strftime("%Y-%m-%d")

    to_date = today.strftime("%Y-%m-%d")

    # Fetch from Dhan HQ Data API
    candles = fetch_historical_ohlcv(
        security_id=sec_id,
        exchange_segment=company.segment or "NSE_EQ",
        from_date=from_date,
        to_date=to_date
    )

    if not candles:
        return {"status": "no_data", "daily_inserted": 0, "daily_updated": 0, "weekly_processed": 0}

    # Upsert daily candles
    counts = upsert_daily_candles(db, company.id, candles)

    # Aggregate weekly candles with status ('completed' or 'incompleted')
    agg_res = aggregate_weekly_candles(company_id=company.id)
    weekly_processed = agg_res.get("processed_rows", 0)

    return {
        "status": "success",
        "daily_inserted": counts["inserted"],
        "daily_updated": counts["updated"],
        "weekly_processed": weekly_processed,
        "from_date": from_date,
        "to_date": to_date
    }


def main():
    parser = argparse.ArgumentParser(description="Sync missing daily & weekly candles from Dhan HQ API.")
    parser.add_argument("--symbol", type=str, help="Sync a single stock symbol (e.g. --symbol IDEA)")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies to sync (0 = all)")
    parser.add_argument("--force-full", action="store_true", help="Force full 1-year sync for all companies")
    args = parser.parse_args()

    logger.info("═══════════════════════════════════════════════════════════════")
    logger.info("  ATS CANDLE SYNCHRONIZATION & HEALING PIPELINE")
    logger.info(f"  Rate Limit Delay : {RATE_LIMIT_DELAY_SEC}s per company")
    logger.info(f"  Weekly Status    : Enabled ('completed' / 'incompleted')")
    logger.info("═══════════════════════════════════════════════════════════════")

    # Ensure weekly_candles table has status column
    ensure_weekly_candle_status_column()

    db = SessionLocal()
    query = db.query(Company).filter(Company.is_active == True)

    if args.symbol:
        query = query.filter(Company.trading_symbol.ilike(args.symbol.strip()))

    companies = query.order_by(Company.trading_symbol.asc()).all()

    if args.limit > 0:
        companies = companies[:args.limit]

    total_companies = len(companies)
    logger.info(f"Targeting {total_companies} companies for candle synchronization.\n")

    today = date.today()
    total_daily_inserted = 0
    total_daily_updated = 0
    total_weekly_processed = 0
    start_time = time.time()

    for idx, comp in enumerate(companies, 1):
        try:
            res = sync_company_candles(db, comp, today, force_full=args.force_full)
            
            if res["status"] == "success":
                d_ins = res["daily_inserted"]
                d_upd = res["daily_updated"]
                w_cnt = res["weekly_processed"]
                total_daily_inserted += d_ins
                total_daily_updated += d_upd
                total_weekly_processed += w_cnt

                logger.info(
                    f"[{idx:>4}/{total_companies}] {comp.trading_symbol:<12} (SecID: {comp.dhan_security_id}): "
                    f"+{d_ins} new daily, {d_upd} updated | {w_cnt} weekly candles synced"
                )
            elif res["status"] == "no_data":
                logger.info(f"[{idx:>4}/{total_companies}] {comp.trading_symbol:<12}: Up to date / No new candles")
            else:
                logger.warning(f"[{idx:>4}/{total_companies}] {comp.trading_symbol:<12}: {res.get('reason')}")

        except Exception as exc:
            logger.error(f"[{idx:>4}/{total_companies}] {comp.trading_symbol:<12}: Sync error: {exc}")

        # Enforce exactly 0.5s rate limit delay between API requests
        time.sleep(RATE_LIMIT_DELAY_SEC)

    elapsed = time.time() - start_time
    db.close()

    logger.info("\n═══════════════════════════════════════════════════════════════")
    logger.info("  CANDLE SYNC SUMMARY")
    logger.info(f"  Total Companies Processed : {total_companies}")
    logger.info(f"  New Daily Candles Inserted: {total_daily_inserted:,}")
    logger.info(f"  Daily Candles Updated     : {total_daily_updated:,}")
    logger.info(f"  Weekly Candles Synced     : {total_weekly_processed:,}")
    logger.info(f"  Elapsed Time              : {elapsed:.2f} seconds ({elapsed/60:.2f} mins)")
    logger.info("═══════════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
