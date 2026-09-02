"""
scripts/sync_and_scan_today.py
==============================
1. Fast multi-threaded sync of daily OHLCV candles up to today (2026-08-31).
2. Executes full Supertrend and Monthly RSI signal scans.
3. Formats and prints all signals discovered for today.
"""

import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import logging
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text
from database.database import SessionLocal
from database.models import Company
from market.candles import sync_candles_for_company
from trading.signals import scan_signals_from_db, get_signals_from_db

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_and_scan")


def fast_sync_candles_today(max_workers: int = 8):
    today = date.today()
    logger.info(f"==> Step 1: Checking and syncing latest daily candles up to {today}...")

    db = SessionLocal()
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != "",
            Company.market_cap >= 8000
        ).all()
        comp_list = [(c.id, c.dhan_security_id, c.trading_symbol) for c in companies]
    finally:
        db.close()

    logger.info(f"Targeting {len(comp_list)} companies with Market Cap >= 8,000 Cr.")

    synced = 0
    updated_total = 0

    def _sync_single(c_info):
        cid, sec_id, symbol = c_info
        try:
            res = sync_candles_for_company(
                company_id=cid,
                security_id=sec_id,
                exchange_segment="NSE_EQ"
            )
            return symbol, res.get("daily_inserted", 0) + res.get("daily_updated", 0)
        except Exception as e:
            return symbol, 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_sync_single, c): c for c in comp_list}
        for future in as_completed(futures):
            sym, count = future.result()
            if count > 0:
                synced += 1
                updated_total += count

    logger.info(f"Sync complete! {synced} companies updated with {updated_total} candles.")


def run_scan_and_report():
    logger.info("==> Step 2: Running signal scanner across all active stocks...")
    new_signals = scan_signals_from_db()
    logger.info(f"Signal scan complete! Newly generated signals: {len(new_signals)}")

    today = date.today()
    today_str = str(today)

    # Fetch all PENDING or latest signals from DB
    all_signals = get_signals_from_db(limit=50)

    print("\n" + "=" * 110)
    print(f"  ATS LIVE SIGNAL SCAN REPORT FOR TODAY ({today_str})")
    print("=" * 110)

    today_signals = [s for s in all_signals if s.get("signal_date") == today_str or s.get("status") == "PENDING"]

    if not today_signals:
        print("\n  [i] No signals detected for today meeting all strict strategy criteria.")
        print("      (Supertrend GREEN Flip + RSI Filters + Market Cap >= 8,000 Cr)")
    else:
        print(f"\n  Found {len(today_signals)} Actionable / Pending Signal(s):\n")
        header = f"{'Symbol':<14} | {'Strategy':<12} | {'Date':<10} | {'Close':<9} | {'High':<9} | {'Daily RSI':<9} | {'Wkly RSI':<9} | {'Status':<10}"
        print(header)
        print("-" * 110)
        for sig in today_signals:
            sym = sig.get("trading_symbol", "N/A")
            strat = sig.get("strategy_type", "SUPERTREND")
            s_date = sig.get("signal_date", "N/A")
            close = f"{sig.get('signal_close', 0.0):.2f}" if sig.get('signal_close') else "N/A"
            high = f"{sig.get('signal_high', 0.0):.2f}" if sig.get('signal_high') else "N/A"
            d_rsi = f"{sig.get('daily_rsi', 0.0):.1f}" if sig.get('daily_rsi') else "N/A"
            w_rsi = f"{sig.get('weekly_rsi', 0.0):.1f}" if sig.get('weekly_rsi') else "N/A"
            status = sig.get("status", "PENDING")
            print(f"{sym:<14} | {strat:<12} | {s_date:<10} | {close:<9} | {high:<9} | {d_rsi:<9} | {w_rsi:<9} | {status:<10}")

    print("\n" + "=" * 110)


if __name__ == "__main__":
    fast_sync_candles_today(max_workers=6)
    run_scan_and_report()
