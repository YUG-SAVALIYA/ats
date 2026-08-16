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
from app.services.settings import get_strategy_settings


def _r2(v: float) -> float:
    return round(v, 2)


def compute_initial_levels(entry: float) -> dict:
    """Compute all price levels from actual fill price using dynamic settings."""
    settings = get_strategy_settings()
    return {
        "stop_price":         _r2(entry * (1 + settings["initial_sl_pct"] / 100.0)),
        "target1_price":      _r2(entry * (1 + settings["target1_pct"] / 100.0)),
        "target2_price":      _r2(entry * (1 + settings["target2_pct"] / 100.0)),
        "sl_stage_1_trigger": _r2(entry * (1 + settings["sl_stage1_trigger"] / 100.0)),
        "sl_stage_2_trigger": _r2(entry * (1 + settings["sl_stage2_trigger"] / 100.0)),
        "sl_stage_3_trigger": _r2(entry * (1 + settings["sl_stage3_trigger"] / 100.0)),
    }


def sl_price_for_stage(entry: float, stage: int) -> float:
    """Return the SL price corresponding to a given stage."""
    settings = get_strategy_settings()
    mapping = {
        0: _r2(entry * (1 + settings["initial_sl_pct"] / 100.0)),
        1: _r2(entry * (1 + settings["sl_stage1_trail"] / 100.0)),
        2: _r2(entry * (1 + settings["sl_stage2_trail"] / 100.0)),
        3: _r2(entry * (1 + settings["sl_stage3_trail"] / 100.0)),
    }
    return mapping.get(stage, mapping[0])


def stage_1_trigger(entry: float) -> float:
    settings = get_strategy_settings()
    return _r2(entry * (1 + settings["sl_stage1_trigger"] / 100.0))


def stage_2_trigger(entry: float) -> float:
    settings = get_strategy_settings()
    return _r2(entry * (1 + settings["sl_stage2_trigger"] / 100.0))


def stage_3_trigger(entry: float) -> float:
    settings = get_strategy_settings()
    return _r2(entry * (1 + settings["sl_stage3_trigger"] / 100.0))


def final_target(entry: float) -> float:
    settings = get_strategy_settings()
    return _r2(entry * (1 + settings["target2_pct"] / 100.0))


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
