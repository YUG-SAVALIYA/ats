"""
app.api.settings
================
Strategy configuration endpoints (ADMIN only).
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any

from app.data.database import SessionLocal
from app.data.models import StrategySettings, MonthlyRsiSettings
from app.services.settings import get_strategy_settings, settings_manager
from app.trading.trade_engine import get_trade_engine
from app.api.auth_app import require_admin, CurrentUser
import asyncio

router = APIRouter(tags=["Settings"])


class StrategySettingsUpdate(BaseModel):
    daily_rsi_period: int
    daily_rsi_lower: float
    daily_rsi_upper: float
    weekly_rsi_period: int
    weekly_rsi_lower: float
    weekly_rsi_upper: float
    supertrend_period: int
    supertrend_multiplier: float
    candle_range_min: float
    candle_range_max: float
    market_cap_min_cr: float
    entry_high_breakout_pct: float
    min_score: float
    initial_sl_pct: float
    target1_pct: float
    trade_stages: List[Dict[str, float]]
    capital_allocation_pct: float


class MonthlyRsiSettingsUpdate(BaseModel):
    rsi_period: int
    min_rsi: float
    max_rsi: float
    swing_window: int
    swing_buffer_pct: float
    min_roc6_pct: float
    min_close_above_sma12_pct: float
    max_entry_gap_pct: float
    
    rsi_exit_below: float
    rsi_exit_trail_points: float
    min_stop_distance_pct: float
    max_stop_distance_pct: float
    supertrend_period: int
    supertrend_multiplier: float
    supertrend_exit_enabled: bool
    
    target_pct: float
    partial_exit_qty_pct: float
    partial_exit_profit_pct: float
    partial_stop_profit_pct: float
    capital_allocation_pct: float


@router.get("/settings/strategy")
def get_strategy_settings_api(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Read strategy settings."""
    try:
        return get_strategy_settings()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get settings: {exc}")


@router.put("/settings/strategy")
def update_strategy_settings_api(settings: StrategySettingsUpdate, _: CurrentUser = Depends(require_admin)):
    """ADMIN only. Update strategy settings."""
    try:
        db = SessionLocal()
        db_settings = db.query(StrategySettings).first()
        if not db_settings:
            db_settings = StrategySettings()
            db.add(db_settings)

        for key, value in settings.dict().items():
            setattr(db_settings, key, value)

        db.commit()
        db.refresh(db_settings)
        db.close()

        settings_manager._cached_supertrend = None

        engine = get_trade_engine()
        if engine:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(engine.recalculate_active_trades())
            except RuntimeError:
                asyncio.run(engine.recalculate_active_trades())

        return {"status": "success", "message": "Strategy settings updated"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update settings: {exc}")


@router.get("/settings/monthly_rsi")
def get_monthly_rsi_settings_api(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Read monthly RSI strategy settings."""
    try:
        db = SessionLocal()
        settings = db.query(MonthlyRsiSettings).first()
        if not settings:
            settings = MonthlyRsiSettings()
            db.add(settings)
            db.commit()
            db.refresh(settings)
        db.close()

        return {c.name: getattr(settings, c.name) for c in settings.__table__.columns if c.name not in ('id', 'updated_at')}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get settings: {exc}")


@router.put("/settings/monthly_rsi")
def update_monthly_rsi_settings_api(settings: MonthlyRsiSettingsUpdate, _: CurrentUser = Depends(require_admin)):
    """ADMIN only. Update monthly RSI strategy settings."""
    try:
        db = SessionLocal()
        db_settings = db.query(MonthlyRsiSettings).first()
        if not db_settings:
            db_settings = MonthlyRsiSettings()
            db.add(db_settings)

        for key, value in settings.dict().items():
            setattr(db_settings, key, value)

        db.commit()
        db.refresh(db_settings)
        db.close()

        settings_manager._cached_monthly = None

        engine = get_trade_engine()
        if engine:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(engine.recalculate_active_trades())
            except RuntimeError:
                asyncio.run(engine.recalculate_active_trades())

        return {"status": "success", "message": "Monthly RSI settings updated"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update settings: {exc}")
