"""
app.broker
==========
Standardized Dhan API v2 broker integration boundary:
- dhan_auth: Encrypted credential storage and auto token refresh via TOTP.
- dhan_client: AccountExecutionContext and rate limiters.
- dhan_gateway: Single authoritative DhanBrokerGateway interface.
- dhan_portfolio: Live fund limits, holdings, and net positions synchronization.
- dhan_websocket: Persistent binary market feed manager.
"""

from app.broker.dhan_auth import DhanAuthManager
from app.broker.dhan_client import (
    AccountExecutionContext,
    get_account_context,
    get_dhan_client,
    get_dhan_data_client,
    TokenBucketRateLimiter,
    get_rate_limiter,
    is_error_response,
    is_empty_portfolio_response,
    is_auth_error,
)
from app.broker.dhan_gateway import (
    DhanBrokerGateway,
    get_broker_gateway,
    BrokerError,
    BrokerAuthError,
    BrokerTimeoutError,
    BrokerRejectError,
    BrokerNetworkError,
)
from app.broker.dhan_portfolio import PortfolioService
from app.broker.dhan_websocket import (
    MarketFeedManager,
    get_market_feed_manager,
    init_market_feed_manager,
)

__all__ = [
    "DhanAuthManager",
    "AccountExecutionContext",
    "get_account_context",
    "get_dhan_client",
    "get_dhan_data_client",
    "TokenBucketRateLimiter",
    "get_rate_limiter",
    "is_error_response",
    "is_empty_portfolio_response",
    "is_auth_error",
    "DhanBrokerGateway",
    "get_broker_gateway",
    "BrokerError",
    "BrokerAuthError",
    "BrokerTimeoutError",
    "BrokerRejectError",
    "BrokerNetworkError",
    "PortfolioService",
    "MarketFeedManager",
    "get_market_feed_manager",
    "init_market_feed_manager",
]
