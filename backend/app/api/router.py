"""
app.api.router
==============
Master API router aggregating all domain sub-routers under `/api`.
"""

from fastapi import APIRouter

# Re-exports for backward compatibility with existing tests and scripts
from app.broker.dhan_client import get_account_context, get_dhan_data_client
from app.trading.execution import get_order_executor, place_market_sell
from app.trading.trade_engine import get_trade_engine
from app.trading.strategy import get_strategy_engine, get_signals_from_db

from app.api.auth import router as auth_router
from app.api.trades import router as trades_router
from app.api.portfolio import router as portfolio_router
from app.api.engine import router as engine_router
from app.api.signals import router as signals_router
from app.api.stocks import router as stocks_router
from app.api.candles import router as candles_router
from app.api.settings import router as settings_router
from app.api.db_views import router as db_views_router
from app.api.observability import router as observability_router
from app.api.dhan_connection import router as dhan_connection_router

router = APIRouter(prefix="/api")

# Mount domain sub-routers
router.include_router(auth_router)
router.include_router(dhan_connection_router)
router.include_router(trades_router)
router.include_router(portfolio_router)
router.include_router(engine_router)
router.include_router(signals_router)
router.include_router(stocks_router)
router.include_router(candles_router)
router.include_router(settings_router)
router.include_router(db_views_router)
router.include_router(observability_router)

__all__ = [
    "router",
    "get_account_context",
    "get_dhan_data_client",
    "get_order_executor",
    "place_market_sell",
    "get_trade_engine",
    "get_strategy_engine",
    "get_signals_from_db",
]
