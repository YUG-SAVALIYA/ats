"""
app.trading
===========
Core trading domain logic:
- levels: Mathematical computation of dynamic stop-loss stages, price levels, and targets.
- state_machine: Trade lifecycle transition rules and state validation.
- risk: Pre-trade safety validations, circuit breakers, and kill switch.
- cache: In-memory dual-indexed trade cache manager.
- execution: Two-phase atomic exit-claim order placement and fill processing.
- trade_engine: Real-time asynchronous tick monitoring state machine.
- strategy: Daily Supertrend(21, 1.5) + RSI scanning and 3:25 PM execution rules.
- monthly_rsi: Monthly RSI strategy evaluation and signals.
"""

from app.trading.levels import (
    compute_initial_levels,
    sl_price_for_stage,
    stage_trigger,
    final_target,
    next_sl_stage,
)
from app.trading.state_machine import validate_state_transition, ALLOWED_TRANSITIONS
from app.trading.risk import PreTradeSafetyValidator
from app.trading.cache import TradeCacheManager, get_cache_manager
from app.trading.execution import (
    OrderExecutor,
    get_order_executor,
    place_market_sell,
    confirm_entry_fill,
    confirm_exit_fill,
)
from app.trading.trade_engine import (
    TradeEngine,
    get_trade_engine,
    init_trade_engine,
)
from app.trading.strategy import (
    AutomatedStrategyEngine,
    get_strategy_engine,
    calculate_rsi,
    calculate_supertrend,
    filter_completed_weekly_candles,
    evaluate_stock_signal,
    get_signals_from_db,
)
from app.trading.monthly_rsi import evaluate_monthly_rsi_signal

__all__ = [
    "compute_initial_levels",
    "sl_price_for_stage",
    "stage_trigger",
    "final_target",
    "next_sl_stage",
    "validate_state_transition",
    "ALLOWED_TRANSITIONS",
    "PreTradeSafetyValidator",
    "TradeCacheManager",
    "get_cache_manager",
    "OrderExecutor",
    "get_order_executor",
    "place_market_sell",
    "confirm_entry_fill",
    "confirm_exit_fill",
    "TradeEngine",
    "get_trade_engine",
    "init_trade_engine",
    "AutomatedStrategyEngine",
    "get_strategy_engine",
    "calculate_rsi",
    "calculate_supertrend",
    "filter_completed_weekly_candles",
    "evaluate_stock_signal",
    "get_signals_from_db",
    "evaluate_monthly_rsi_signal",
]
