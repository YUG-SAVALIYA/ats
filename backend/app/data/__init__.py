"""
app.data
========
Centralized database models, database access, repositories, candles, calendar, and locks.
"""

from app.data.database import Base, engine, SessionLocal, get_db
from app.data.models import (
    User, DhanAccount, Company, Signal, Trade, AtsOrder, TradeEvent,
    OrderAttempt, Portfolio, Holding, Position, DailyCandle, WeeklyCandle,
    MonthlyCandle, MarketHoliday, ActiveSubscription, AppConfig,
    StrategySettings, MonthlyRsiSettings, AtsTradeState, OrderPurpose,
    SignalStatus, TradeStatus, AccountStatus, UserRole,
)
from app.data.repositories import (
    AccountRepository, get_account_repo,
    TradeRepository, get_trade_repo,
    CompanyRepository, get_company_repo,
)
from app.data.locks import (
    try_advisory_lock, release_advisory_lock, advisory_lock_guard,
    LOCK_JOB_320_FAST_SYNC, LOCK_JOB_325_EXECUTION, LOCK_JOB_340_POST_SYNC,
    LOCK_JOB_400_SIGNAL_SCAN, LOCK_JOB_2200_FULL_SYNC, LOCK_RECONCILE_CYCLE,
)
from app.data.calendar import is_trading_day
from app.data.holidays import fetch_and_store_holidays
from app.data.weekly import aggregate_weekly_candles
from app.data.candles import (
    sync_candles_for_company, sync_actionable_companies, sync_all_active_companies,
    get_daily_candles_from_db, get_weekly_candles_from_db, get_monthly_candles_from_db,
)

__all__ = [
    "Base", "engine", "SessionLocal", "get_db",
    "User", "DhanAccount", "Company", "Signal", "Trade", "AtsOrder", "TradeEvent",
    "OrderAttempt", "Portfolio", "Holding", "Position", "DailyCandle", "WeeklyCandle",
    "MonthlyCandle", "MarketHoliday", "ActiveSubscription", "AppConfig",
    "StrategySettings", "MonthlyRsiSettings", "AtsTradeState", "OrderPurpose",
    "SignalStatus", "TradeStatus", "AccountStatus", "UserRole",
    "AccountRepository", "get_account_repo",
    "TradeRepository", "get_trade_repo",
    "CompanyRepository", "get_company_repo",
    "try_advisory_lock", "release_advisory_lock", "advisory_lock_guard",
    "LOCK_JOB_320_FAST_SYNC", "LOCK_JOB_325_EXECUTION", "LOCK_JOB_340_POST_SYNC",
    "LOCK_JOB_400_SIGNAL_SCAN", "LOCK_JOB_2200_FULL_SYNC", "LOCK_RECONCILE_CYCLE",
    "is_trading_day", "fetch_and_store_holidays", "aggregate_weekly_candles",
    "sync_candles_for_company", "sync_actionable_companies", "sync_all_active_companies",
    "get_daily_candles_from_db", "get_weekly_candles_from_db", "get_monthly_candles_from_db",
]
