"""
scripts/fast_persist_signals_and_candles.py
===========================================
1. Fast snapshot fetch for the 621 eligible universe companies.
2. Directly persists 2026-08-31 daily candles in PostgreSQL.
3. Updates weekly candle aggregation.
4. Verifies all 3 signals are active with status PENDING in DB.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import uuid
import time
from datetime import date, datetime
from database.database import SessionLocal
from database.models import Company, DailyCandle, Signal
from dhan.market import get_live_ohlc
from market.weekly import aggregate_weekly_candles

def main():
    today = date.today()
    today_str = str(today)
    print(f"[*] Persisting today's ({today_str}) candles & signals for the eligible universe...")

    db = SessionLocal()
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != "",
            Company.market_cap >= 8000
        ).all()

        comp_dict = {str(c.dhan_security_id): c for c in companies}
        sec_ids = [int(sid) for sid in comp_dict.keys() if sid.isdigit()]
        print(f"[*] Target: {len(sec_ids)} securities.")

        snapshot = {}
        for i in range(0, len(sec_ids), 500):
            chunk = sec_ids[i:i+500]
            try:
                res = get_live_ohlc(chunk)
                if isinstance(res, dict):
                    snapshot.update(res)
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(1.0)

        # Batch insert/update daily candles
        saved = 0
        for sid_str, snap in snapshot.items():
            comp = comp_dict.get(sid_str)
            if not comp:
                continue

            ohlc = snap.get("ohlc", {})
            close_p = float(ohlc.get("close", 0) or 0)
            if close_p <= 0:
                continue

            open_p = float(ohlc.get("open", close_p) or close_p)
            high_p = float(ohlc.get("high", close_p) or close_p)
            low_p = float(ohlc.get("low", close_p) or close_p)

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
        print(f"[*] Successfully saved {saved} daily candles for {today_str} into PostgreSQL.")

        # Update weekly candles for signal companies
        signal_syms = ["LUMAXTECH", "ASKAUTOLTD", "AUROPHARMA"]
        for sym in signal_syms:
            c = db.query(Company).filter(Company.trading_symbol == sym).first()
            if c:
                aggregate_weekly_candles(c.id)

        # Verify signals in DB
        db_signals = (
            db.query(Signal, Company)
            .join(Company, Signal.company_id == Company.id)
            .filter(Signal.date == today, Signal.status == "PENDING")
            .all()
        )

        print("\n" + "=" * 110)
        print(f"  ACTIVE PENDING SIGNALS IN DATABASE FOR TODAY ({today_str}):")
        print("=" * 110)
        for sig, comp in db_signals:
            raw = sig.raw_signal_data or {}
            print(f"  Symbol: {comp.trading_symbol:<12} | Strategy: {sig.strategy_type:<12} | Close: {raw.get('signal_close')} | Ref High: {raw.get('signal_high')} | Status: {sig.status}")
        print("=" * 110)

    finally:
        db.close()

if __name__ == "__main__":
    main()
