import sys
from pathlib import Path
import pytest

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from trading.strategies import calculate_rsi, calculate_supertrend
from trading.signals import calculate_reference_price
from trading.risk import compute_initial_levels, sl_price_for_stage, next_sl_stage


def test_calculate_rsi():
    prices = [100.0, 102.0, 104.0, 103.0, 105.0, 107.0, 106.0, 108.0, 110.0, 109.0, 112.0, 115.0, 114.0, 116.0, 118.0]
    rsi = calculate_rsi(prices, period=14)
    assert 0.0 <= rsi <= 100.0
    assert rsi > 50.0


def test_calculate_supertrend():
    candles = [
        {"high": 100 + i * 2, "low": 98 + i * 2, "close": 99 + i * 2, "open": 98 + i * 2}
        for i in range(25)
    ]
    st = calculate_supertrend(candles, period=21, multiplier=1.5)
    assert len(st) == 25
    assert st[-1] == 1


def test_reference_price_calculation():
    ref, is_gap = calculate_reference_price(signal_high=100.0, today_open=99.0)
    assert ref == 100.0
    assert is_gap is False

    ref_gap, is_gap2 = calculate_reference_price(signal_high=100.0, today_open=105.0)
    assert ref_gap == 105.0
    assert is_gap2 is True


def test_trailing_stop_loss_progression():
    entry = 100.0
    initial_levels = compute_initial_levels(entry)
    assert initial_levels["stop_price"] == 95.0

    # Stage 0
    assert sl_price_for_stage(entry, 0) == 95.0

    # Stage 1: +2.5% trigger -> SL moves to +0.5% (100.5)
    stage = next_sl_stage(0, ltp=102.6, entry=entry)
    assert stage == 1
    assert sl_price_for_stage(entry, 1) == 100.5

    # Stage 2: +7% trigger -> SL moves to +4% (104.0)
    stage2 = next_sl_stage(stage, ltp=107.1, entry=entry)
    assert stage2 == 2
    assert sl_price_for_stage(entry, 2) == 104.0

    # Stage 3: +12% trigger -> SL moves to +5% (105.0)
    stage3 = next_sl_stage(stage2, ltp=112.5, entry=entry)
    assert stage3 == 3
    assert sl_price_for_stage(entry, 3) == 105.0
