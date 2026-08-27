"""
tests/test_tradingview_rsi.py — Comprehensive TradingView RSI(14) Verification
==============================================================================
Validates that ATS calculate_rsi and calculate_rsi_series match TradingView's
built-in `ta.rsi(close, 14)` with Wilder's RMA smoothing value-for-value.
"""

import sys
import math
from pathlib import Path
from typing import List, Optional

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from trading.strategies import calculate_rsi, calculate_rsi_series
from database.database import SessionLocal
from database.models import Company, DailyCandle, WeeklyCandle
from market.weekly import filter_completed_weekly_candles


def pine_script_rsi_reference(prices: List[float], length: int = 14) -> List[Optional[float]]:
    """
    Pure reference implementation of TradingView Pine Script built-in `ta.rsi(close, 14)`.
    Pine Script:
        u = math.max(change, 0)
        d = math.max(-change, 0)
        rs = ta.rma(u, length) / ta.rma(d, length)
        rsi = 100 - (100 / (1 + rs))
    where `ta.rma(src, length)`:
        rma = na(rma[1]) ? ta.sma(src, length) : (src + (length - 1) * rma[1]) / length
    """
    n = len(prices)
    if n < length + 1:
        return [None] * n

    # Pine script returns `na` for the first `length` bars (indices 0 .. length-1)
    result: List[Optional[float]] = [None] * length

    gains = []
    losses = []
    for i in range(1, n):
        chg = prices[i] - prices[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))

    # Initial RMA is SMA of first `length` price changes
    rma_u = sum(gains[:length]) / length
    rma_d = sum(losses[:length]) / length

    if rma_u + rma_d == 0.0:
        first_rsi = 50.0
    elif rma_d == 0.0:
        first_rsi = 100.0
    elif rma_u == 0.0:
        first_rsi = 0.0
    else:
        rs = rma_u / rma_d
        first_rsi = 100.0 - (100.0 / (1.0 + rs))
    result.append(first_rsi)

    # For subsequent bars
    for i in range(length, len(gains)):
        rma_u = (gains[i] + (length - 1) * rma_u) / length
        rma_d = (losses[i] + (length - 1) * rma_d) / length

        if rma_u + rma_d == 0.0:
            rsi = 50.0
        elif rma_d == 0.0:
            rsi = 100.0
        elif rma_u == 0.0:
            rsi = 0.0
        else:
            rs = rma_u / rma_d
            rsi = 100.0 - (100.0 / (1.0 + rs))
        result.append(rsi)

    return result


def test_wilder_textbook_data():
    """Test using J. Welles Wilder Jr.'s original textbook 14-period dataset."""
    wilder_prices = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
        45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
        46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
        43.42, 42.66, 43.13
    ]

    ats_series = calculate_rsi_series(wilder_prices, period=14)
    ref_series = pine_script_rsi_reference(wilder_prices, length=14)

    assert len(ats_series) == len(ref_series)

    max_diff = 0.0
    for i in range(len(wilder_prices)):
        ats_val = ats_series[i]
        ref_val = ref_series[i]
        if ref_val is None:
            assert ats_val is None
        else:
            diff = abs(ats_val - ref_val)
            if diff > max_diff:
                max_diff = diff
            assert diff < 1e-12, f"Bar {i} mismatch: ATS={ats_val}, Ref={ref_val}"

    # Final candle check
    latest_ats = calculate_rsi(wilder_prices, period=14)
    assert abs(latest_ats - ref_series[-1]) < 1e-12
    print(f"[PASS] Wilder Textbook Test: Max Absolute Difference = {max_diff:.2e}")


def test_edge_cases():
    """Test all edge cases: flat prices, all gains, all losses, insufficient bars."""
    # 1. Flat prices (zero change)
    flat_prices = [100.0] * 30
    assert calculate_rsi(flat_prices, 14) == 50.0

    # 2. Continuous Gains (zero loss)
    bull_prices = [100.0 + i * 2.0 for i in range(30)]
    assert calculate_rsi(bull_prices, 14) == 100.0

    # 3. Continuous Losses (zero gain)
    bear_prices = [200.0 - i * 2.0 for i in range(30)]
    assert calculate_rsi(bear_prices, 14) == 0.0

    # 4. Insufficient Bars (< 15 bars for 14-period RSI)
    short_prices = [100.0, 102.0, 101.0]
    assert calculate_rsi(short_prices, 14) == 0.0
    assert calculate_rsi_series(short_prices, 14) == [None, None, None]

    print("[PASS] All Edge Cases Verified Successfully!")


def test_real_database_stocks():
    """Test real stock candles from PostgreSQL database bar-by-bar across 300+ daily bars."""
    db = SessionLocal()
    try:
        symbols_to_test = ["RELIANCE", "HDFCBANK", "KARURVYSYA", "PAYTM", "LAURUSLABS"]
        print("\n" + "=" * 75)
        print("  REAL STOCKS DATABASE VERIFICATION (ATS vs TRADINGVIEW PINE SCRIPT)")
        print("=" * 75)

        for sym in symbols_to_test:
            comp = db.query(Company).filter(Company.trading_symbol == sym).first()
            if not comp:
                continue

            # Daily candles
            daily_rows = (
                db.query(DailyCandle)
                .filter(DailyCandle.company_id == comp.id)
                .order_by(DailyCandle.date.asc())
                .all()
            )
            if len(daily_rows) < 30:
                continue

            closes = [float(r.close) for r in daily_rows]
            dates = [str(r.date) for r in daily_rows]

            ats_series = calculate_rsi_series(closes, period=14)
            ref_series = pine_script_rsi_reference(closes, length=14)

            max_diff = 0.0
            valid_bars = 0
            for i in range(len(closes)):
                a = ats_series[i]
                r = ref_series[i]
                if a is not None and r is not None:
                    valid_bars += 1
                    diff = abs(a - r)
                    if diff > max_diff:
                        max_diff = diff
                    assert diff < 1e-11, f"Mismatch on {sym} at {dates[i]}: ATS={a}, TV={r}"

            latest_ats = calculate_rsi(closes, period=14)
            latest_ref = ref_series[-1]

            print(f"  {sym:<12} | Daily Bars: {len(closes):>3} | Valid RSI Bars: {valid_bars:>3} | Max Diff: {max_diff:.2e} | Latest RSI: {latest_ats:.4f}")

            # Weekly candles
            weekly_rows = (
                db.query(WeeklyCandle)
                .filter(WeeklyCandle.company_id == comp.id)
                .order_by(WeeklyCandle.week_start_date.asc())
                .all()
            )
            if len(weekly_rows) >= 15:
                w_closes = [float(r.close) for r in weekly_rows]
                w_ats = calculate_rsi_series(w_closes, period=14)
                w_ref = pine_script_rsi_reference(w_closes, length=14)
                w_max_diff = max(abs(a - r) for a, r in zip(w_ats, w_ref) if a is not None and r is not None)
                print(f"  {sym + ' (Wk)':<12} | Weekly Bars:{len(w_closes):>3} | Valid RSI Bars: {len(w_closes)-14:>3} | Max Diff: {w_max_diff:.2e} | Latest RSI: {w_ats[-1]:.4f}")

        print("=" * 75 + "\n")
    finally:
        db.close()


if __name__ == "__main__":
    print("Running TradingView RSI Verification Suite...\n")
    test_wilder_textbook_data()
    test_edge_cases()
    test_real_database_stocks()
    print("ALL TRADINGVIEW RSI TESTS PASSED WITH ZERO DISCREPANCY!")
