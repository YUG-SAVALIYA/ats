"""
scripts/persist_today_candles.py
================================
Fetches today's (2026-08-31) market close OHLC snapshot from Dhan and saves it into daily_candles.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import uuid
import time
from datetime import date, datetime
from sqlalchemy import text
from database.database import SessionLocal
from database.models import Company, DailyCandle
from dhan.market import get_live_ohlc
from market.weekly import aggregate_weekly_candles

def persist_candles():
    today = date.today()
    print(f"Persisting today's ({today}) daily candles into PostgreSQL...")

    db = SessionLocal()
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != ""
        ).all()

        comp_dict = {str(c.dhan_security_id): c for c in companies}
        sec_ids = [int(sid) for sid in comp_dict.keys() if sid.isdigit()]

        print(f"Fetching live OHLC for {len(sec_ids)} securities...")
        snapshot = {}
        for i in range(0, len(sec_ids), 500):
            chunk = sec_ids[i:i+500]
            try:
                res = get_live_ohlc(chunk)
                if isinstance(res, dict):
                    snapshot.update(res)
            except Exception as e:
                print(f"Error fetching chunk: {e}")
            time.sleep(1.0)

        saved = 0
        for sid_str, data in snapshot.items():
            comp = comp_dict.get(sid_str)
            if not comp:
                continue

            ohlc = data.get("ohlc", {})
            close_p = float(ohlc.get("close", 0) or 0)
            if close_p <= 0:
                continue

            open_p = float(ohlc.get("open", close_p) or close_p)
            high_p = float(ohlc.get("high", close_p) or close_p)
            low_p = float(ohlc.get("low", close_p) or close_p)

            # Check existing
            existing = db.query(DailyCandle).filter(
                DailyCandle.company_id == comp.id,
                DailyCandle.date == today
            ).first()

            if existing:
                existing.open = open_p
                existing.high = high_p
                existing.low = low_p
                existing.close = close_p
            else:
                db.add(DailyCandle(
                    id=str(uuid.uuid4()),
                    company_id=comp.id,
                    date=today,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=0,
                    created_at=datetime.utcnow()
                ))
            saved += 1

        db.commit()
        print(f"Successfully saved {saved} daily candles for {today} into PostgreSQL.")

        # Re-aggregate weekly candles for the 3 signal stocks
        for sym in ["LUMAXTECH", "ASKAUTOLTD", "AUROPHARMA"]:
            c = db.query(Company).filter(Company.trading_symbol == sym).first()
            if c:
                aggregate_weekly_candles(c.id)
                print(f"Updated weekly candles for {sym}.")

    finally:
        db.close()

if __name__ == "__main__":
    persist_candles()
