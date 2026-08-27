"""
scripts/scan_yesterday_signals.py — Fast Signal Scanner & Strategy Indicator Breakdown
======================================================================================
Scans all qualifying universe stocks for yesterday's market data (2026-08-25)
and outputs indicator metrics, Supertrend flips, and strategy signals.

Usage:
  backend\\venv\\Scripts\\python.exe backend\\scripts\\scan_yesterday_signals.py
"""

import os
import sys
import time
from datetime import datetime, date, timedelta
from typing import List, Dict, Any
from collections import defaultdict

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# Ensure backend directory is in path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import text
from database.database import SessionLocal
from database.models import Signal
from trading.strategies import evaluate_stock_signal, evaluate_monthly_rsi_signal, calculate_supertrend, calculate_rsi
from trading.signals import save_signal_to_db


def print_header(title: str):
    print("\n" + "=" * 90)
    print(f"  {title}")
    print("=" * 90)


def aggregate_weekly(daily_candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    weekly_map = {}
    for r in daily_candles:
        d = r["date"]
        mon = d - timedelta(days=d.weekday())
        if mon not in weekly_map:
            weekly_map[mon] = {
                "date": mon,
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"]
            }
        else:
            weekly_map[mon]["high"] = max(weekly_map[mon]["high"], r["high"])
            weekly_map[mon]["low"] = min(weekly_map[mon]["low"], r["low"])
            weekly_map[mon]["close"] = r["close"]
            weekly_map[mon]["volume"] += r["volume"]
    weekly_list = list(weekly_map.values())
    weekly_list.sort(key=lambda c: c["date"])
    return weekly_list


def aggregate_monthly(daily_candles: List[Dict[str, Any]], limit: int = 30) -> List[Dict[str, Any]]:
    monthly_map = {}
    for r in daily_candles:
        d = r["date"]
        month_key = d.strftime("%Y-%m") if hasattr(d, "strftime") else str(d)[:7]
        if month_key not in monthly_map:
            monthly_map[month_key] = {
                "date": d,
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"]
            }
        else:
            monthly_map[month_key]["high"] = max(monthly_map[month_key]["high"], r["high"])
            monthly_map[month_key]["low"] = min(monthly_map[month_key]["low"], r["low"])
            monthly_map[month_key]["close"] = r["close"]
            monthly_map[month_key]["volume"] += r["volume"]
    monthly_list = list(monthly_map.values())
    monthly_list.sort(key=lambda c: c["date"])
    return monthly_list[-limit:]


def run_scan():
    start_time = time.time()
    print_header("FAST SIGNAL SCANNER -- TARGET DATE: YESTERDAY'S CANDLE")

    db = SessionLocal()
    try:
        print("  [1/3] Loading stock universe & daily candles from PostgreSQL...")
        
        # 1. Fetch active companies with market cap >= 8,000 Cr
        comp_rows = db.execute(text(
            "SELECT id, trading_symbol, dhan_security_id, market_cap "
            "FROM companies WHERE is_active = true AND dhan_security_id != '' AND market_cap >= 8000"
        )).fetchall()

        comp_map = {
            r[0]: {
                "id": r[0],
                "symbol": r[1],
                "sec_id": r[2],
                "mcap": float(r[3] or 0)
            }
            for r in comp_rows
        }
        print(f"        -> Found {len(comp_map)} eligible companies (Mcap >= 8,000 Cr).")

        # 2. Fetch daily candles in single fast SQL join (220 days for 14-period Weekly RSI)
        cutoff_daily = date.today() - timedelta(days=220)
        daily_rows = db.execute(
            text(
                "SELECT d.company_id, d.date, d.open, d.high, d.low, d.close, d.volume "
                "FROM daily_candles d "
                "JOIN companies c ON d.company_id = c.id "
                "WHERE c.is_active = true AND c.market_cap >= 8000 AND d.date >= :cutoff "
                "ORDER BY d.company_id, d.date ASC"
            ),
            {"cutoff": cutoff_daily}
        ).fetchall()

        daily_by_comp = defaultdict(list)
        for r in daily_rows:
            daily_by_comp[r[0]].append({
                "date": r[1],
                "open": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "close": float(r[5]),
                "volume": int(r[6] or 0)
            })
        print(f"        -> Loaded {len(daily_rows):,} daily candles in memory.")

    finally:
        db.close()

    # 3. Strategy Evaluation & Supertrend Flip Analysis
    print("\n  [2/3] Analyzing Supertrend & Multi-Timeframe Indicators for Yesterday's Candle...")
    
    st_flips = []
    qualified_signals = []

    for cid, comp in comp_map.items():
        daily = daily_by_comp.get(cid, [])
        if len(daily) < 22:
            continue

        symbol = comp["symbol"]
        sec_id = comp["sec_id"]
        mcap = comp["mcap"]

        # Supertrend direction on daily
        st_dirs = calculate_supertrend(daily, period=21, multiplier=1.5)
        if len(st_dirs) >= 2:
            latest_st = st_dirs[-1]
            prev_st = st_dirs[-2]

            # Flipped from RED (-1) to GREEN (+1)
            if prev_st == -1 and latest_st == 1:
                last_c = daily[-1]
                d_rsi = calculate_rsi([x["close"] for x in daily], period=14)
                c_range = round(((last_c["high"] - last_c["low"]) / last_c["open"]) * 100.0, 2)
                
                # Calculate Weekly RSI strictly on completed weekly candles (excluding current forming week)
                from market.weekly import filter_completed_weekly_candles
                weekly = aggregate_weekly(daily)
                completed_weekly = filter_completed_weekly_candles(weekly, current_date=last_c["date"])
                
                w_rsi = calculate_rsi([w["close"] for w in completed_weekly], period=14) if len(completed_weekly) >= 15 else 0.0

                # Check if all conditions match
                # Daily RSI: 50-90, Weekly RSI: 65-85, Range: 3-12%
                rsi_ok = 50.0 <= d_rsi <= 90.0
                w_rsi_ok = 65.0 <= w_rsi <= 85.0
                range_ok = 3.0 <= c_range <= 12.0

                st_flips.append({
                    "symbol": symbol,
                    "date": str(last_c["date"]),
                    "close": last_c["close"],
                    "range": c_range,
                    "d_rsi": d_rsi,
                    "w_rsi": w_rsi,
                    "rsi_ok": rsi_ok,
                    "w_rsi_ok": w_rsi_ok,
                    "range_ok": range_ok,
                    "fully_qualified": (rsi_ok and w_rsi_ok and range_ok)
                })

                # Check standard engine evaluation with completed weekly
                st_sig = evaluate_stock_signal(
                    symbol=symbol,
                    security_id=sec_id,
                    exchange_segment="NSE_EQ",
                    daily_candles=daily,
                    weekly_candles=completed_weekly,
                    market_cap_cr=mcap,
                    current_date=last_c["date"]
                )
                if st_sig:
                    st_sig["company_id"] = cid
                    st_sig["strategy_type"] = "SUPERTREND"
                    qualified_signals.append(st_sig)

    elapsed = time.time() - start_time

    # Display Indicator Table for All Supertrend Flips
    print_header(f"SUPERTREND FLIPS FOUND ON YESTERDAY'S CANDLE ({len(st_flips)} STOCKS) [Scan Time: {elapsed:.2f}s]")
    print(f"\n  {'SYMBOL':<14} | {'DATE':<10} | {'CLOSE (Rs)':<10} | {'RANGE %':<8} | {'D-RSI (50-90)':<14} | {'W-RSI (65-85)':<14} | {'STATUS'}")
    print("  " + "-" * 88)

    for f in st_flips:
        status_parts = []
        if not f["range_ok"]:
            status_parts.append(f"Range {f['range']}% (Req 3-12%)")
        if not f["rsi_ok"]:
            status_parts.append(f"D-RSI {f['d_rsi']:.1f} (Req 50-90)")
        if not f["w_rsi_ok"]:
            status_parts.append(f"W-RSI {f['w_rsi']:.1f} (Req 65-85)")

        status_str = "QUALIFIED" if f["fully_qualified"] else f"Filter: {', '.join(status_parts)}"
        print(f"  {f['symbol']:<14} | {f['date']:<10} | {f['close']:<10.2f} | {f['range']:<7.2f}% | {f['d_rsi']:<14.1f} | {f['w_rsi']:<14.1f} | {status_str}")

    print("  " + "-" * 88)

    if qualified_signals:
        print(f"\n  [OK] Saved {len(qualified_signals)} fully qualified signals into PostgreSQL database.")
        for s in qualified_signals:
            save_signal_to_db(s["company_id"], s, strategy_type="SUPERTREND")

    print("\n" + "=" * 90 + "\n")


if __name__ == "__main__":
    run_scan()
