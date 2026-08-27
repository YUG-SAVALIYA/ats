"""
dhan/endpoints.py — Central Repository of Official Dhan HQ API v2 Endpoints
==========================================================================
Single source of truth for all Dhan REST API URLs, WebSocket endpoints,
CDN data feeds, and dynamic endpoint builders.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════════
# BASE DOMAINS
# ═══════════════════════════════════════════════════════════════════════════════
DHAN_API_BASE_URL = "https://api.dhan.co/v2"
DHAN_AUTH_BASE_URL = "https://auth.dhan.co"
DHAN_CDN_BASE_URL = "https://images.dhan.co"
DHAN_WS_BASE_URL = "wss://api-feed.dhan.co"

# ═══════════════════════════════════════════════════════════════════════════════
# AUTHENTICATION & SESSION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════
AUTH_GENERATE_TOKEN_URL = f"{DHAN_AUTH_BASE_URL}/app/generateAccessToken"
AUTH_RENEW_TOKEN_URL = f"{DHAN_API_BASE_URL}/RenewToken"

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA & CHARTS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════
MARKET_HISTORICAL_CHARTS_URL = f"{DHAN_API_BASE_URL}/charts/historical"
MARKET_FEED_OHLC_URL = f"{DHAN_API_BASE_URL}/marketfeed/ohlc"
MARKET_FEED_LTP_URL = f"{DHAN_API_BASE_URL}/marketfeed/ltp"
MARKET_FEED_QUOTE_URL = f"{DHAN_API_BASE_URL}/marketfeed/quote"

# CDN / Official Scrip Master Data Feed
SCRIP_MASTER_CSV_URL = f"{DHAN_CDN_BASE_URL}/api-data/api-scrip-master.csv"

# ═══════════════════════════════════════════════════════════════════════════════
# ORDERS & TRADES EXECUTION ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════
ORDERS_URL = f"{DHAN_API_BASE_URL}/orders"
TRADES_URL = f"{DHAN_API_BASE_URL}/trades"


def get_order_by_id_url(dhan_order_id: str) -> str:
    """Returns endpoint URL for GET or DELETE on specific order ID."""
    return f"{ORDERS_URL}/{dhan_order_id}"


def get_trade_by_id_url(dhan_trade_id: str) -> str:
    """Returns endpoint URL for GET specific trade ID."""
    return f"{TRADES_URL}/{dhan_trade_id}"


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO, FUNDS & ACCOUNT ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════
PORTFOLIO_FUND_LIMIT_URL = f"{DHAN_API_BASE_URL}/fundlimit"
PORTFOLIO_HOLDINGS_URL = f"{DHAN_API_BASE_URL}/holdings"
PORTFOLIO_POSITIONS_URL = f"{DHAN_API_BASE_URL}/positions"


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKET FEED ENDPOINTS & BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════
def get_websocket_feed_url(access_token: str, client_id: str, auth_type: int = 2) -> str:
    """Builds full authenticated Dhan Marketfeed v2 WebSocket URL."""
    return f"{DHAN_WS_BASE_URL}?version=2&token={access_token}&clientId={client_id}&authType={auth_type}"
