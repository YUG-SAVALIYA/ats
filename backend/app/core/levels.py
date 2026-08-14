"""
app/core/levels.py
===================
Pure math helpers for computing trade price levels.
No DB access, no I/O. Fully testable in isolation.

Trailing SL stages
------------------
Stage 0 (initial):  SL = entry × 0.95   (−5%)
Stage 1 (+5% hit):  SL = entry × 1.02   (+2%)
Stage 2 (+8% hit):  SL = entry × 1.04   (+4%)
Stage 3 (+12% hit): SL = entry × 1.05   (+5%)  + 50% partial exit

Final target (+17%): full exit of remaining qty.
"""

from __future__ import annotations


def _r2(v: float) -> float:
    return round(v, 2)


def compute_initial_levels(entry: float) -> dict:
    """Compute all price levels from actual fill price."""
    return {
        "stop_price":         _r2(entry * 0.95),    # initial SL  −5%
        "target1_price":      _r2(entry * 1.12),    # +12% → partial exit + SL→+5%
        "target2_price":      _r2(entry * 1.17),    # +17% → final exit
        "sl_stage_1_trigger": _r2(entry * 1.05),    # at +5%  → SL→+2%
        "sl_stage_2_trigger": _r2(entry * 1.08),    # at +8%  → SL→+4%
        "sl_stage_3_trigger": _r2(entry * 1.12),    # at +12% → SL→+5% + partial exit
    }


def sl_price_for_stage(entry: float, stage: int) -> float:
    """Return the SL price corresponding to a given stage."""
    mapping = {
        0: _r2(entry * 0.95),
        1: _r2(entry * 1.02),
        2: _r2(entry * 1.04),
        3: _r2(entry * 1.05),
    }
    return mapping.get(stage, _r2(entry * 0.95))


def stage_1_trigger(entry: float) -> float:
    return _r2(entry * 1.05)   # +5%


def stage_2_trigger(entry: float) -> float:
    return _r2(entry * 1.08)   # +8%


def stage_3_trigger(entry: float) -> float:
    return _r2(entry * 1.12)   # +12%


def final_target(entry: float) -> float:
    return _r2(entry * 1.17)   # +17%


def next_sl_stage(current_stage: int, ltp: float, entry: float) -> int:
    """Return the new SL stage given LTP and entry price. Stages only move UP."""
    if current_stage >= 3:
        return 3
    if ltp >= stage_3_trigger(entry):
        return 3
    if current_stage >= 2:
        return 2
    if ltp >= stage_2_trigger(entry):
        return 2
    if current_stage >= 1:
        return 1
    if ltp >= stage_1_trigger(entry):
        return 1
    return 0
