import logging
from app.database import SessionLocal
from app.models import StrategySettings
from functools import lru_cache
import time

logger = logging.getLogger("ats.settings")

class SettingsManager:
    def __init__(self):
        self._cached_settings = None
        self._last_fetched = 0
        self._cache_ttl = 30 # seconds

    def get_settings(self):
        now = time.time()
        if self._cached_settings and (now - self._last_fetched < self._cache_ttl):
            return self._cached_settings

        db = SessionLocal()
        try:
            settings = db.query(StrategySettings).first()
            if not settings:
                settings = StrategySettings()
                db.add(settings)
                db.commit()
                db.refresh(settings)
            
            # Convert to dict for easier use and caching
            self._cached_settings = {
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
                "target2_pct": settings.target2_pct,
                
                "sl_stage1_trigger": settings.sl_stage1_trigger,
                "sl_stage1_trail": settings.sl_stage1_trail,
                "sl_stage2_trigger": settings.sl_stage2_trigger,
                "sl_stage2_trail": settings.sl_stage2_trail,
                "sl_stage3_trigger": settings.sl_stage3_trigger,
                "sl_stage3_trail": settings.sl_stage3_trail,
            }
            self._last_fetched = now
            return self._cached_settings
        except Exception as e:
            logger.error(f"Error fetching strategy settings: {e}")
            # Return defaults if db fails
            return {
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
                "target1_pct": 12.0,
                "target2_pct": 17.0,
                "sl_stage1_trigger": 5.0,
                "sl_stage1_trail": 2.0,
                "sl_stage2_trigger": 8.0,
                "sl_stage2_trail": 4.0,
                "sl_stage3_trigger": 12.0,
                "sl_stage3_trail": 5.0,
            }
        finally:
            db.close()

settings_manager = SettingsManager()

def get_strategy_settings():
    return settings_manager.get_settings()
