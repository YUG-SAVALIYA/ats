"""
scripts/sync_today_marketfeed_candles.py — Fast Live/Post-Market Today Candle Sync
==================================================================================
Uses Dhan's Market Feed OHLC API (batches of 200–500 securities) to instantly fetch
and insert today's (2026-08-26) Open, High, Low, Close, and Volume into `daily_candles`.
"""

import sys
import time
import uuid
from datetime import date, datetime
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from database.database import SessionLocal
from database.models import Company, DailyCandle
from dhan.client import get_dhan_data_client
from market.weekly import aggregate_weekly_candles

def main():
    today = date.today()
    print("=" * 70)
    print(f"  ATS FAST MARKETFEED CANDLE SYNC — {today}")
    print(f"  Ingesting Today's Live/Post-Market OHLC into `daily_candles` Table")
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
            .all()
        )
        total_companies = len(companies)
        print(f"Found {total_companies} active companies.\n")

        # Map security_id (int) -> company
        sec_map = {}
        for c in companies:
            try:
                sec_map[int(c.dhan_security_id)] = c
            except ValueError:
                pass

        client = get_dhan_data_client()
        sec_ids = list(sec_map.keys())

        # Process in batches of 300
        batch_size = 300
        inserted_count = 0
        updated_count = 0

        # Pre-fetch existing daily candles for today
        existing_today = {
            r.company_id: r
            for r in db.query(DailyCandle).filter(DailyCandle.date == today).all()
        }

        print(f"Querying Dhan Market Feed API in batches of {batch_size}...")
        start_time = time.time()

        for i in range(0, len(sec_ids), batch_size):
            chunk = sec_ids[i:i + batch_size]
            try:
                feed = client.get_marketfeed_ohlc(chunk)
            except Exception as exc:
                print(f"Batch {i//batch_size + 1} error: {exc}")
                feed = {}

            for sec_id in chunk:
                comp = sec_map.get(sec_id)
                if not comp:
                    continue

                item = feed.get(str(sec_id)) or feed.get(sec_id)
                if not item or not isinstance(item, dict):
                    continue

                ohlc = item.get("ohlc", {}) if isinstance(item.get("ohlc"), dict) else {}
                open_p = float(ohlc.get("open") or item.get("open") or 0.0)
                high_p = float(ohlc.get("high") or item.get("high") or 0.0)
                low_p = float(ohlc.get("low") or item.get("low") or 0.0)
                close_p = float(item.get("last_price") or ohlc.get("close") or item.get("close") or 0.0)
                vol = int(item.get("volume") or ohlc.get("volume") or 0)

                if open_p <= 0 and close_p <= 0:
                    continue

                if comp.id in existing_today:
                    row = existing_today[comp.id]
                    row.open = open_p
                    row.high = high_p
                    row.low = low_p
                    row.close = close_p
                    row.volume = vol
                    updated_count += 1
                else:
                    new_row = DailyCandle(
                        id=str(uuid.uuid4()),
                        company_id=comp.id,
                        date=today,
                        open=open_p,
                        high=high_p,
                        low=low_p,
                        close=close_p,
                        volume=vol,
                        created_at=datetime.utcnow()
                    )
                    db.add(new_row)
                    existing_today[comp.id] = new_row
                    inserted_count += 1

            db.commit()
            print(f"  Batch {i//batch_size + 1:>2} / {len(sec_ids)//batch_size + 1:>2} processed ({min(i + batch_size, len(sec_ids))}/{len(sec_ids)} stocks).")
            time.sleep(0.2)

        elapsed = time.time() - start_time
        print("\n" + "=" * 70)
        print("  TODAY'S CANDLE INGESTION COMPLETED")
        print(f"  Daily Candles Inserted for {today}: {inserted_count}")
        print(f"  Daily Candles Updated for {today}:  {updated_count}")
        print(f"  Total Today Candles in DB:         {inserted_count + updated_count}")
        print(f"  Execution Time:                    {elapsed:.2f} seconds")
        print("=" * 70)

    finally:
        db.close()

if __name__ == "__main__":
    main()
