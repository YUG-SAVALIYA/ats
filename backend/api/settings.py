"""
api/settings.py — Strategy & Risk Parameters Configuration Endpoints
====================================================================
Provides REST endpoints for fetching and dynamically modifying strategy parameters:
- GET  /api/settings/strategy
- PUT  /api/settings/strategy
- GET  /api/settings/monthly_rsi
- PUT  /api/settings/monthly_rsi
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.auth import get_current_user
from database.database import get_db, SessionLocal
from database.models import StrategySettings, MonthlyRsiSettings
from trading.trade_manager import get_trade_engine

logger = logging.getLogger("ats.api.settings")

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class StrategySettingsUpdate(BaseModel):
    daily_rsi_period: Optional[int] = None
    daily_rsi_lower: Optional[float] = None
    daily_rsi_upper: Optional[float] = None
    weekly_rsi_period: Optional[int] = None
    weekly_rsi_lower: Optional[float] = None
    weekly_rsi_upper: Optional[float] = None
    supertrend_period: Optional[int] = None
    supertrend_multiplier: Optional[float] = None
    candle_range_min: Optional[float] = None
    candle_range_max: Optional[float] = None
    market_cap_min_cr: Optional[float] = None
    entry_high_breakout_pct: Optional[float] = None
    initial_sl_pct: Optional[float] = None
    target1_pct: Optional[float] = None
    trade_stages: Optional[List[Dict[str, Any]]] = None
    capital_allocation_pct: Optional[float] = None


class MonthlyRsiSettingsUpdate(BaseModel):
    rsi_period: Optional[int] = None
    min_rsi: Optional[float] = None
    max_rsi: Optional[float] = None
    swing_window: Optional[int] = None
    swing_buffer_pct: Optional[float] = None
    min_roc6_pct: Optional[float] = None
    min_close_above_sma12_pct: Optional[float] = None
    max_entry_gap_pct: Optional[float] = None
    rsi_exit_below: Optional[float] = None
    rsi_exit_trail_points: Optional[float] = None
    min_stop_distance_pct: Optional[float] = None
    max_stop_distance_pct: Optional[float] = None
    supertrend_period: Optional[int] = None
    supertrend_multiplier: Optional[float] = None
    supertrend_exit_enabled: Optional[bool] = None
    target_pct: Optional[float] = None
    partial_exit_qty_pct: Optional[float] = None
    partial_exit_profit_pct: Optional[float] = None
    partial_stop_profit_pct: Optional[float] = None
    capital_allocation_pct: Optional[float] = None


@router.get("/strategy")
def get_strategy_settings_api(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Fetch current Supertrend Strategy parameters."""
    settings = db.query(StrategySettings).first()
    if not settings:
        settings = StrategySettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)

    return {
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
        "updated_at": str(settings.updated_at) if settings.updated_at else None
    }


@router.put("/strategy")
async def update_strategy_settings_api(
    req: StrategySettingsUpdate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Update Supertrend strategy settings and retroactively apply levels to active trades."""
    try:
        settings = db.query(StrategySettings).first()
        if not settings:
            settings = StrategySettings()
            db.add(settings)

        update_data = req.dict(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(settings, key, value)

        db.commit()
        db.refresh(settings)
        logger.info(f"[SETTINGS] Strategy settings updated: {update_data}")

        # Retroactively update levels on active trades
        trade_engine = get_trade_engine()
        if trade_engine:
            await trade_engine.recalculate_active_trades()

        return {
            "status": "success",
            "message": "Strategy settings updated and applied retroactively",
            "settings": {
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
                "updated_at": str(settings.updated_at) if settings.updated_at else None
            }
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"[SETTINGS] Failed to update strategy settings: {exc}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to update strategy settings: {str(exc)}")


@router.get("/monthly_rsi")
def get_monthly_rsi_settings_api(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Fetch current Monthly RSI strategy parameters."""
    try:
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
            "updated_at": str(settings.updated_at) if settings.updated_at else None
        }
    except Exception as exc:
        logger.error(f"[SETTINGS] Failed to fetch monthly RSI settings: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to fetch monthly RSI settings: {str(exc)}")


@router.put("/monthly_rsi")
async def update_monthly_rsi_settings_api(
    req: MonthlyRsiSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Update Monthly RSI strategy settings and retroactively apply levels to active trades."""
    try:
        settings = db.query(MonthlyRsiSettings).first()
        if not settings:
            settings = MonthlyRsiSettings()
            db.add(settings)

        update_data = req.dict(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(settings, key, value)

        db.commit()
        db.refresh(settings)
        logger.info(f"[SETTINGS] Monthly RSI settings updated: {update_data}")

        trade_engine = get_trade_engine()
        if trade_engine:
            await trade_engine.recalculate_active_trades()

        return {
            "status": "success",
            "message": "Monthly RSI settings updated and applied retroactively",
            "settings": {
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
                "updated_at": str(settings.updated_at) if settings.updated_at else None
            }
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"[SETTINGS] Failed to update monthly RSI settings: {exc}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Failed to update monthly RSI settings: {str(exc)}")
