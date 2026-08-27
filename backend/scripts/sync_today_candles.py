"""
scripts/sync_today_candles.py — Sync Daily & Weekly Candles for Active Universe
================================================================================
Fetches today's candle (and fills any 5-day gaps) from Dhan with a 0.5s delay per symbol.
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from database.database import SessionLocal
from database.models import Company, DailyCandle
from market.candles import sync_candles_for_company
from market.calendar import is_trading_day

def main():
    today = date.today()
    print("=" * 70)
    print(f"  ATS CANDLE SYNCHRONIZATION — {today}")
    print(f"  Rate Limit: 0.5s per symbol | Exchange Segment: NSE_EQ")
    print("=" * 70)

    db = SessionLocal()
    try:
        companies = (
            db.query(Company)
            .filter(
                Company.is_active == True,
                Company.dhan_security_id != None,
                Company.dhan_security_id != ""
            )
            .order_by(Company.market_cap.desc().nullslast())
            .all()
        )
        total_count = len(companies)
        print(f"Found {total_count} active companies to synchronize.\n")
    finally:
        db.close()

    synced_count = 0
    skipped_count = 0
    total_daily_inserted = 0
    total_weekly_inserted = 0
    errors_count = 0

    start_time = time.time()

    for idx, comp in enumerate(companies, 1):
        sym = comp.trading_symbol
        sec_id = comp.dhan_security_id

        try:
            res = sync_candles_for_company(
                company_id=comp.id,
                security_id=sec_id,
                exchange_segment="NSE_EQ",
                force_full=False
            )

            if res.get("skipped"):
                skipped_count += 1
                status_str = "UP-TO-DATE"
            else:
                d_ins = res.get("daily_inserted", 0) + res.get("daily_updated", 0)
                w_ins = res.get("weekly_inserted", 0)
                total_daily_inserted += d_ins
                total_weekly_inserted += w_ins
                synced_count += 1
                status_str = f"SYNCED (+{d_ins} daily, +{w_ins} weekly)"

            if idx % 10 == 0 or idx == total_count or not res.get("skipped"):
                elapsed = time.time() - start_time
                print(f"[{idx:>4}/{total_count}] {sym:<12} (ID: {sec_id:<6}) -> {status_str} | Elapsed: {elapsed:.1f}s")

            time.sleep(0.5)

        except Exception as exc:
            errors_count += 1
            print(f"[{idx:>4}/{total_count}] {sym:<12} -> ERROR: {exc}")
            time.sleep(0.5)

    total_time = time.time() - start_time
    print("\n" + "=" * 70)
    print("  CANDLE SYNC COMPLETE")
    print(f"  Total Processed: {total_count}")
    print(f"  Companies Synced: {synced_count}")
    print(f"  Up-to-Date / Skipped: {skipped_count}")
    print(f"  Daily Candles Updated: {total_daily_inserted}")
    print(f"  Weekly Candles Generated: {total_weekly_inserted}")
    print(f"  Errors: {errors_count}")
    print(f"  Total Time: {total_time:.1f}s ({total_time/60:.1f} mins)")
    print("=" * 70)

if __name__ == "__main__":
    main()
