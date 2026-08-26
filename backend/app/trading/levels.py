"""
app.trading.levels
==================
Pure math helpers for computing trade price levels, stop-loss stages, and targets.
No DB access, no I/O. Fully testable in isolation.
"""

from __future__ import annotations
from app.services.settings import get_strategy_settings


def _r2(v: float) -> float:
    return round(v, 2)


def compute_initial_levels(entry: float) -> dict:
    """Compute all price levels from actual fill price using dynamic settings."""
    settings = get_strategy_settings()
    levels = {
        "stop_price":         _r2(entry * (1 + settings["initial_sl_pct"] / 100.0)),
        "target1_price":      _r2(entry * (1 + settings["target1_pct"] / 100.0)),
    }
    
    stages = settings.get("trade_stages", [])
    for i, stage in enumerate(stages):
        levels[f"sl_stage_{i+1}_trigger"] = _r2(entry * (1 + stage["trigger"] / 100.0))
        
    return levels


def sl_price_for_stage(entry: float, stage: int) -> float:
    """Return the SL price corresponding to a given stage (0 is initial, 1 is index 0 of stages)."""
    settings = get_strategy_settings()
    if stage == 0:
        return _r2(entry * (1 + settings["initial_sl_pct"] / 100.0))
        
    stages = settings.get("trade_stages", [])
    if stage > 0 and stage <= len(stages):
        return _r2(entry * (1 + stages[stage - 1]["trail"] / 100.0))
        
    return _r2(entry * (1 + settings["initial_sl_pct"] / 100.0))


def stage_trigger(entry: float, stage: int) -> float:
    """Return the trigger price for a given stage (1 is index 0)."""
    settings = get_strategy_settings()
    stages = settings.get("trade_stages", [])
    if stage > 0 and stage <= len(stages):
        return _r2(entry * (1 + stages[stage - 1]["trigger"] / 100.0))
    return entry


def final_target(entry: float) -> float:
    settings = get_strategy_settings()
    return _r2(entry * (1 + settings["target1_pct"] / 100.0))


def next_sl_stage(current_stage: int, ltp: float, entry: float) -> int:
    """Return the new SL stage given LTP and entry price. Stages only move UP."""
    settings = get_strategy_settings()
    stages = settings.get("trade_stages", [])
    max_stage = len(stages)
    
    if current_stage >= max_stage:
        return max_stage
        
    for i in range(max_stage, current_stage, -1):
        trigger_price = stage_trigger(entry, i)
        if ltp >= trigger_price:
            return i
            
    return current_stage
