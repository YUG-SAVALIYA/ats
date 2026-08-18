
import logging
from app.database import SessionLocal
from app.models import StrategySettings, MonthlyRsiSettings
from functools import lru_cache
import time

logger = logging.getLogger('ats.settings')

class SettingsManager:
    def __init__(self):
        self._cached_supertrend = None
        self._cached_monthly = None
        self._last_fetched_supertrend = 0
        self._last_fetched_monthly = 0
        self._cache_ttl = 30 # seconds

    def get_settings(self, strategy_type: str = 'SUPERTREND'):
        now = time.time()
        
        if strategy_type == 'MONTHLY_RSI':
            if self._cached_monthly and (now - self._last_fetched_monthly < self._cache_ttl):
                return self._cached_monthly
            
            db = SessionLocal()
            try:
                settings = db.query(MonthlyRsiSettings).first()
                if not settings:
                    settings = MonthlyRsiSettings()
                    db.add(settings)
                    db.commit()
                    db.refresh(settings)
                
                self._cached_monthly = {
                    'rsi_period': settings.rsi_period,
                    'min_rsi': settings.min_rsi,
                    'max_rsi': settings.max_rsi,
                    'swing_window': settings.swing_window,
                    'swing_buffer_pct': settings.swing_buffer_pct,
                    'min_roc6_pct': settings.min_roc6_pct,
                    'min_close_above_sma12_pct': settings.min_close_above_sma12_pct,
                    'max_entry_gap_pct': settings.max_entry_gap_pct,
                    'rsi_exit_below': settings.rsi_exit_below,
                    'rsi_exit_trail_points': settings.rsi_exit_trail_points,
                    'min_stop_distance_pct': settings.min_stop_distance_pct,
                    'max_stop_distance_pct': settings.max_stop_distance_pct,
                    'supertrend_period': settings.supertrend_period,
                    'supertrend_multiplier': settings.supertrend_multiplier,
                    'supertrend_exit_enabled': settings.supertrend_exit_enabled,
                    'target_pct': settings.target_pct,
                    'partial_exit_qty_pct': settings.partial_exit_qty_pct,
                    'partial_exit_profit_pct': settings.partial_exit_profit_pct,
                    'partial_stop_profit_pct': settings.partial_stop_profit_pct,
                    'capital_allocation_pct': settings.capital_allocation_pct,
                }
                self._last_fetched_monthly = now
                return self._cached_monthly
            except Exception as e:
                logger.error(f'Error fetching monthly rsi settings: {e}')
                return {
                    'capital_allocation_pct': 20.0,
                    'max_entry_gap_pct': 5.0,
                    'rsi_period': 14,
                    'min_rsi': 55.0,
                    'max_rsi': 70.0,
                    'swing_window': 10,
                    'swing_buffer_pct': 0.5,
                    'min_roc6_pct': 25.0,
                    'min_close_above_sma12_pct': 10.0,
                    'rsi_exit_below': 55.0,
                    'rsi_exit_trail_points': 50.0,
                    'min_stop_distance_pct': 10.0,
                    'max_stop_distance_pct': 25.0,
                    'supertrend_period': 10,
                    'supertrend_multiplier': 3.0,
                    'supertrend_exit_enabled': True,
                    'target_pct': 100.0,
                    'partial_exit_qty_pct': 0.0,
                    'partial_exit_profit_pct': 10.0,
                    'partial_stop_profit_pct': 0.0,
                }
            finally:
                db.close()
        else:
            if self._cached_supertrend and (now - self._last_fetched_supertrend < self._cache_ttl):
                return self._cached_supertrend

            db = SessionLocal()
            try:
                settings = db.query(StrategySettings).first()
                if not settings:
                    settings = StrategySettings()
                    db.add(settings)
                    db.commit()
                    db.refresh(settings)
                
                self._cached_supertrend = {
                    'daily_rsi_period': settings.daily_rsi_period,
                    'daily_rsi_lower': settings.daily_rsi_lower,
                    'daily_rsi_upper': settings.daily_rsi_upper,
                    'weekly_rsi_period': settings.weekly_rsi_period,
                    'weekly_rsi_lower': settings.weekly_rsi_lower,
                    'weekly_rsi_upper': settings.weekly_rsi_upper,
                    'supertrend_period': settings.supertrend_period,
                    'supertrend_multiplier': settings.supertrend_multiplier,
                    'candle_range_min': settings.candle_range_min,
                    'candle_range_max': settings.candle_range_max,
                    'market_cap_min_cr': settings.market_cap_min_cr,
                    'entry_high_breakout_pct': settings.entry_high_breakout_pct,
                    
                    'initial_sl_pct': settings.initial_sl_pct,
                    'target1_pct': settings.target1_pct,
                    
                    'trade_stages': settings.trade_stages,
                    'capital_allocation_pct': settings.capital_allocation_pct,
                }
                self._last_fetched_supertrend = now
                return self._cached_supertrend
            except Exception as e:
                logger.error(f'Error fetching strategy settings: {e}')
                return {
                    'capital_allocation_pct': 20.0,
                    'daily_rsi_period': 14,
                    'daily_rsi_lower': 50.0,
                    'daily_rsi_upper': 90.0,
                    'weekly_rsi_period': 14,
                    'weekly_rsi_lower': 65.0,
                    'weekly_rsi_upper': 85.0,
                    'supertrend_period': 21,
                    'supertrend_multiplier': 1.5,
                    'candle_range_min': 3.0,
                    'candle_range_max': 12.0,
                    'market_cap_min_cr': 8000.0,
                    'entry_high_breakout_pct': 3.0,
                    'initial_sl_pct': -5.0,
                    'target1_pct': 17.0,
                    'trade_stages': [
                        {'trigger': 5.0, 'trail': 2.0, 'qty': 0.0},
                        {'trigger': 8.0, 'trail': 4.0, 'qty': 0.0},
                        {'trigger': 12.0, 'trail': 5.0, 'qty': 50.0}
                    ],
                }
            finally:
                db.close()

settings_manager = SettingsManager()

def get_strategy_settings(strategy_type: str = 'SUPERTREND'):
    return settings_manager.get_settings(strategy_type=strategy_type)

