"""
fast_sync_workers.py - High-Performance 5-Worker Candle Fetcher & Signal Scanner
================================================================================
Fetches missing candles concurrently using 5 worker threads, runs batch weekly
aggregation, and scans signals for yesterday (2026-09-03).
"""

import sys
import time
import logging
from pathlib import Path
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(r"c:\Users\Yug\Desktop\ATS\backend")))

from database.database import SessionLocal
from database.models import Company, DailyCandle, Signal
from market.candles import sync_candles_for_company
from market.weekly import aggregate_weekly_candles
from trading.signals import scan_signals_from_db

# Configure clean logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("fast_sync")

TARGET_DATE = date(2026, 9, 3)
MAX_WORKERS = 5


def get_missing_companies():
    """Returns active companies missing candles on TARGET_DATE."""
    db = SessionLocal()
    try:
        active_comps = (
            db.query(Company)
            .filter(
                Company.is_active == True,
                Company.dhan_security_id != None,
                Company.dhan_security_id != ""
            )
            .all()
        )
        
        existing_cids = {
            r[0]
            for r in db.query(DailyCandle.company_id)
            .filter(DailyCandle.date == TARGET_DATE)
            .all()
        }
        
        missing = [c for c in active_comps if c.id not in existing_cids]
        return missing, len(active_comps), len(existing_cids)
    finally:
        db.close()


def process_company(comp_data):
    """Worker task to sync candles for a single company."""
    comp_id, sec_id, exch, symbol = comp_data
    try:
        res = sync_candles_for_company(
            company_id=comp_id,
            security_id=sec_id,
            exchange_segment=exch,
            symbol=symbol,
            aggregate_weekly=False  # Skip per-symbol aggregation; run batch at the end!
        )
        return symbol, True, res.get("daily_inserted", 0) + res.get("daily_updated", 0), None
    except Exception as exc:
        return symbol, False, 0, str(exc)


def main():
    print("=" * 70, flush=True)
    print(f"FAST CANDLE SYNC & SIGNAL SCANNER (Workers: {MAX_WORKERS})", flush=True)
    print(f"Target Date: {TARGET_DATE}", flush=True)
    print("=" * 70, flush=True)

    missing_comps, total_active, total_existing = get_missing_companies()
    print(f"Total Active Companies   : {total_active}", flush=True)
    print(f"Already Synced in DB     : {total_existing}", flush=True)
    print(f"Missing to Fetch         : {len(missing_comps)}", flush=True)
    print("=" * 70, flush=True)

    if not missing_comps:
        print("All active companies already have candles for target date!", flush=True)
    else:
        print(f"Starting concurrent fetch across {MAX_WORKERS} workers...", flush=True)
        t0 = time.time()
        
        tasks = [
            (c.id, c.dhan_security_id, "NSE_EQ", c.trading_symbol)
            for c in missing_comps
        ]
        
        success_count = 0
        fail_count = 0
        inserted_total = 0
        total_tasks = len(tasks)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_sym = {
                executor.submit(process_company, task): task[3]
                for task in tasks
            }

            for idx, future in enumerate(as_completed(future_to_sym), 1):
                sym, ok, count, err = future.result()
                if ok:
                    success_count += 1
                    inserted_total += count
                else:
                    fail_count += 1
                
                # Report progress every 25 symbols or on completion
                if idx % 25 == 0 or idx == total_tasks:
                    pct = (idx / total_tasks) * 100
                    elapsed = time.time() - t0
                    rate = idx / elapsed if elapsed > 0 else 0
                    print(
                        f"[{idx:>4}/{total_tasks}] ({pct:5.1f}%) | "
                        f"Success: {success_count} | Skipped/Failed: {fail_count} | "
                        f"Rate: {rate:.1f} sym/s | Elapsed: {elapsed:.0f}s",
                        flush=True
                    )

        total_elapsed = time.time() - t0
        print("=" * 70, flush=True)
        print(f"FETCH COMPLETE in {total_elapsed:.1f}s ({total_elapsed/60:.1f} mins)!", flush=True)
        print(f"Successfully processed: {success_count}, Skipped/Failed: {fail_count}", flush=True)
        print("=" * 70, flush=True)

    # 2. Batch weekly aggregation once
    print("\nRunning batch weekly candle aggregation...", flush=True)
    t_agg = time.time()
    try:
        agg_res = aggregate_weekly_candles(company_id=None)
        print(f"Weekly aggregation done in {time.time() - t_agg:.2f}s: {agg_res}", flush=True)
    except Exception as e:
        print(f"Weekly aggregation notice: {e}", flush=True)

    # 3. Final candle count check
    db = SessionLocal()
    final_count = db.query(DailyCandle).filter(DailyCandle.date == TARGET_DATE).count()
    print(f"\nFinal Candles for {TARGET_DATE} in DB: {final_count} / {total_active} ({(final_count/total_active)*100:.1f}%)", flush=True)

    # 4. Scan signals
    print("\n" + "=" * 70, flush=True)
    print(f"SCANNING SIGNALS FOR {TARGET_DATE}...", flush=True)
    print("=" * 70, flush=True)

    db.query(Signal).filter(Signal.date == TARGET_DATE).delete()
    db.commit()

    new_sigs = scan_signals_from_db(target_date=TARGET_DATE)
    print(f"\nSignal scan complete! Generated {len(new_sigs)} signals.", flush=True)

    sigs = (
        db.query(Signal, Company)
        .join(Company, Signal.company_id == Company.id)
        .filter(Signal.date == TARGET_DATE)
        .all()
    )
    print(f"Total Signals in DB for {TARGET_DATE}: {len(sigs)}\n", flush=True)
    for s, comp in sigs:
        raw = s.raw_signal_data or {}
        high = f"{raw.get('signal_high', 0):.2f}"
        low = f"{raw.get('signal_low', 0):.2f}"
        close = f"{raw.get('signal_close', 0):.2f}"
        d_rsi = f"{raw.get('daily_rsi', 0):.1f}"
        w_rsi = f"{raw.get('weekly_rsi', 0):.1f}"
        print(f"{comp.trading_symbol:<15} | {s.strategy_type:<12} | {s.status:<8} | High: {high:<8} | Low: {low:<8} | Close: {close:<8} | RSI(D/W): {d_rsi}/{w_rsi}")
    print("-" * 95)
    db.close()


if __name__ == "__main__":
    main()
