"""
trading/risk.py — Trailing Stop-Loss & Risk Management Calculations
===================================================================
Pure mathematics and level calculation helpers for trade risk management.
Computes multi-stage trailing stop-loss levels and target prices dynamically.
"""

from __future__ import annotations

import logging
from typing import Dict, Any, List
from database.database import SessionLocal
from database.models import StrategySettings, MonthlyRsiSettings

import functools

logger = logging.getLogger("ats.trading.risk")


def _r2(v: float) -> float:
    return round(v, 2)


_SETTINGS_CACHE = {}

def get_strategy_settings(strategy_type: str = "SUPERTREND") -> Dict[str, Any]:
    """Retrieves strategy settings from database with sensible defaults and fast in-memory caching."""
    if strategy_type in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[strategy_type]

    db = SessionLocal()
    try:
        if strategy_type == "MONTHLY_RSI":
            settings = db.query(MonthlyRsiSettings).first()
            if not settings:
                settings = MonthlyRsiSettings()
                db.add(settings)
                db.commit()
                db.refresh(settings)

            return {
                "rsi_period": settings.rsi_period,
                "min_rsi": settings.min_rsi,
                "max_rsi": settings.max_rsi,
                "swing_window": settings.swing_window,
                "swing_buffer_pct": settings.swing_buffer_pct,
                "min_roc6_pct": settings.min_roc6_pct,
                "min_close_above_sma12_pct": settings.min_close_above_sma12_pct,
                "max_entry_gap_pct": settings.max_entry_gap_pct,
                "rsi_exit_below": settings.rsi_exit_below,
                "rsi_exit_trail_points": settings.rsi_exit_trail_points,
                "min_stop_distance_pct": settings.min_stop_distance_pct,
                "max_stop_distance_pct": settings.max_stop_distance_pct,
                "supertrend_period": settings.supertrend_period,
                "supertrend_multiplier": settings.supertrend_multiplier,
                "supertrend_exit_enabled": settings.supertrend_exit_enabled,
                "target_pct": settings.target_pct,
                "partial_exit_qty_pct": settings.partial_exit_qty_pct,
                "partial_exit_profit_pct": settings.partial_exit_profit_pct,
                "partial_stop_profit_pct": settings.partial_stop_profit_pct,
                "capital_allocation_pct": settings.capital_allocation_pct,
            }
        else:
            settings = db.query(StrategySettings).first()
            if not settings:
                settings = StrategySettings()
                db.add(settings)
                db.commit()
                db.refresh(settings)

            res = {
                "daily_rsi_period": settings.daily_rsi_period,
                "daily_rsi_lower": settings.daily_rsi_lower,
                "daily_rsi_upper": settings.daily_rsi_upper,
                "weekly_rsi_period": settings.weekly_rsi_period,
                "weekly_rsi_lower": settings.weekly_rsi_lower,
                "weekly_rsi_upper": settings.weekly_rsi_upper,
                "supertrend_period": settings.supertrend_period,
                "supertrend_multiplier": settings.supertrend_multiplier,
                "candle_range_min": settings.candle_range_min,
                "candle_range_max": settings.candle_range_max,
                "market_cap_min_cr": settings.market_cap_min_cr,
                "entry_high_breakout_pct": settings.entry_high_breakout_pct,
                "initial_sl_pct": settings.initial_sl_pct,
                "target1_pct": settings.target1_pct,
                "trade_stages": settings.trade_stages,
                "capital_allocation_pct": settings.capital_allocation_pct,
            }
            _SETTINGS_CACHE[strategy_type] = res
            return res
    except Exception as exc:
        logger.error(f"[RISK] Error fetching settings: {exc}")
        res = {
            "daily_rsi_period": 14,
            "daily_rsi_lower": 50.0,
            "daily_rsi_upper": 90.0,
            "weekly_rsi_period": 14,
            "weekly_rsi_lower": 65.0,
            "weekly_rsi_upper": 85.0,
            "supertrend_period": 21,
            "supertrend_multiplier": 1.5,
            "candle_range_min": 3.0,
            "candle_range_max": 12.0,
            "market_cap_min_cr": 8000.0,
            "entry_high_breakout_pct": 3.0,
            "initial_sl_pct": -5.0,
            "target1_pct": 17.0,
            "trade_stages": [
                {"trigger": 5.0, "trail": 2.0, "qty": 0.0},
                {"trigger": 8.0, "trail": 4.0, "qty": 0.0},
                {"trigger": 12.0, "trail": 5.0, "qty": 50.0},
            ],
            "capital_allocation_pct": 20.0,
        }
        _SETTINGS_CACHE[strategy_type] = res
        return res
    finally:
        db.close()


def compute_initial_levels(entry: float) -> Dict[str, float]:
    """Compute initial price levels (Stop Loss, Targets, Triggers) from fill price."""
    settings = get_strategy_settings()
    levels = {
        "stop_price":    _r2(entry * (1 + settings["initial_sl_pct"] / 100.0)),
        "target1_price": _r2(entry * (1 + settings["target1_pct"] / 100.0)),
    }
    
    stages = settings.get("trade_stages", [])
    for i, stage in enumerate(stages):
        levels[f"sl_stage_{i+1}_trigger"] = _r2(entry * (1 + stage["trigger"] / 100.0))
        
    return levels


def sl_price_for_stage(entry: float, stage: int) -> float:
    """Return the SL price corresponding to a given stage (0 is initial)."""
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
    """Computes final target price from entry price."""
    settings = get_strategy_settings()
    return _r2(entry * (1 + settings["target1_pct"] / 100.0))


def next_sl_stage(current_stage: int, ltp: float, entry: float) -> int:
    """Return the new SL stage given LTP and entry price. Stages only move UP."""
    settings = get_strategy_settings()
    stages = settings.get("trade_stages", [])
    max_stage = len(stages)
    
    if current_stage >= max_stage:
        return max_stage
        
    # Check from highest possible stage down to current_stage + 1
    for i in range(max_stage, current_stage, -1):
        trigger_price = stage_trigger(entry, i)
        if ltp >= trigger_price:
            return i
            
    return current_stage
