"""
app.broker.dhan_gateway
=======================
Standardized, account-scoped broker gateway isolating Dhan API communication,
rate limiting (8 req/s per account), retries, auto-token renewal, and error handling.
"""

from typing import Optional, Dict, Any, List
import logging
import time
import requests
from app.broker.dhan_auth import DhanAuthManager
from app.broker.dhan_client import (
    get_rate_limiter,
    is_error_response,
    is_empty_portfolio_response,
    is_auth_error,
    _is_json
)

logger = logging.getLogger("ats.dhan_gateway")


class BrokerError(Exception):
    """Base exception for all broker communication errors."""
    pass

class BrokerAuthError(BrokerError):
    """Raised when broker authentication or token renewal fails."""
    pass

class BrokerTimeoutError(BrokerError):
    """Raised when broker HTTP request times out."""
    pass

class BrokerRejectError(BrokerError):
    """Raised when broker RMS or order engine rejects an order."""
    pass

class BrokerNetworkError(BrokerError):
    """Raised on socket or transport errors."""
    pass


class DhanBrokerGateway:
    """
    Account-scoped broker gateway for Dhan API v2.
    Ensures no strategy or API code makes direct raw HTTP calls to Dhan.
    """

    BASE_URL = "https://api.dhan.co/v2"

    def __init__(self, dhan_account_id: str, auth_manager: Optional[DhanAuthManager] = None):
        self.dhan_account_id = dhan_account_id
        if auth_manager:
            self.auth_manager = auth_manager
        else:
            self.auth_manager = DhanAuthManager(dhan_account_id=dhan_account_id)
        self.client_id = self.auth_manager.client_id
        self.rate_limiter = get_rate_limiter(self.client_id or "default")

    def _get_headers(self) -> Dict[str, str]:
        token = self.auth_manager.get_valid_token()
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or "",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: int = 12,
        max_retries: int = 3,
    ) -> Any:
        url = endpoint if endpoint.startswith("http") else f"{self.BASE_URL}{endpoint}"
        
        # Enforce rate limit (8 req/sec per account)
        wait_time = self.rate_limiter.consume(1)
        if wait_time > 0:
            time.sleep(wait_time)

        headers = self._get_headers()
        attempt = 0

        while attempt < max_retries:
            attempt += 1
            try:
                if method.upper() == "GET":
                    resp = requests.get(url, headers=headers, timeout=timeout)
                elif method.upper() == "POST":
                    resp = requests.post(url, json=payload or {}, headers=headers, timeout=timeout)
                elif method.upper() == "DELETE":
                    resp = requests.delete(url, headers=headers, timeout=timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

            except requests.exceptions.Timeout as exc:
                logger.warning(f"[BROKER_GATEWAY][***{self.client_id[-4:] if self.client_id else ''}] Timeout on {method} {url}: {exc}")
                if method.upper() == "POST" and "orders" in url:
                    raise BrokerTimeoutError(f"Order submission timed out: {exc}") from exc
                if attempt >= max_retries:
                    raise BrokerTimeoutError(f"Request timed out after {max_retries} attempts: {exc}") from exc
                time.sleep(0.5 * attempt)
                continue

            except requests.exceptions.RequestException as exc:
                logger.error(f"[BROKER_GATEWAY] Network error on {method} {url}: {exc}")
                if attempt >= max_retries:
                    raise BrokerNetworkError(f"Network error: {exc}") from exc
                time.sleep(0.5 * attempt)
                continue

            # Handle 401/403 Auth Refresh
            if resp.status_code in (401, 403) or is_auth_error(resp.json() if _is_json(resp) else None):
                logger.warning(f"[BROKER_GATEWAY] Auth error on {url}. Refreshing token...")
                if self.auth_manager.refresh_token():
                    headers = self._get_headers()
                    continue
                else:
                    raise BrokerAuthError("Failed to refresh broker access token.")

            # Handle 429 Rate Limiting
            if resp.status_code == 429:
                backoff = 1.0 * attempt
                logger.warning(f"[BROKER_GATEWAY] HTTP 429 received. Backing off for {backoff}s...")
                time.sleep(backoff)
                continue

            # Success or standard JSON response
            if _is_json(resp):
                data = resp.json()
                if is_empty_portfolio_response(data):
                    return []
                return data

            if resp.status_code == 200:
                return {}

            return {"status": "failure", "http_code": resp.status_code, "remarks": resp.text}

        raise BrokerError(f"Request failed after {max_retries} attempts to {url}")

    # ── Standardized Broker Operations ─────────────────────────────────────────

    def place_market_order(
        self,
        security_id: str,
        transaction_type: str,
        quantity: int,
        product_type: str = "CNC",
        correlation_id: Optional[str] = None,
        tag: Optional[str] = None,
        exchange_segment: str = "NSE_EQ",
    ) -> Dict[str, Any]:
        """Places a MARKET BUY or MARKET SELL order on Dhan."""
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": correlation_id or "",
            "transactionType": transaction_type.upper(),
            "exchangeSegment": exchange_segment,
            "productType": product_type.upper(),
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(quantity),
            "price": 0,
            "triggerPrice": 0,
            "afterMarketOrder": False,
        }
        if tag:
            payload["tag"] = tag[:25]

        res = self._request("POST", "/orders", payload=payload, timeout=10)
        return res if isinstance(res, dict) else {"status": "success", "data": res}

    def cancel_order(self, dhan_order_id: str) -> Dict[str, Any]:
        """Cancels a pending order on Dhan."""
        res = self._request("DELETE", f"/orders/{dhan_order_id}", timeout=10)
        return res if isinstance(res, dict) else {"status": "success", "data": res}

    def get_order_status(self, dhan_order_id: str) -> Dict[str, Any]:
        """Checks status of a single order."""
        res = self._request("GET", f"/orders/{dhan_order_id}", timeout=10)
        return res if isinstance(res, dict) else {"data": res}

    def get_order_book(self) -> List[Dict[str, Any]]:
        """Fetches the daily order book from Dhan."""
        res = self._request("GET", "/orders", timeout=12)
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            return res["data"]
        return []

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetches open net positions from Dhan."""
        res = self._request("GET", "/positions", timeout=12)
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            return res["data"]
        return []

    def get_holdings(self) -> List[Dict[str, Any]]:
        """Fetches demat and MTF holdings from Dhan."""
        res = self._request("GET", "/holdings", timeout=12)
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            return res["data"]
        return []

    def get_funds(self) -> Dict[str, Any]:
        """Fetches fund limits from Dhan."""
        res = self._request("GET", "/fundlimit", timeout=10)
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], dict):
            return res["data"]
        return res if isinstance(res, dict) else {}

    def get_trades(self) -> List[Dict[str, Any]]:
        """Fetches executed trades for the day from Dhan."""
        res = self._request("GET", "/trades", timeout=10)
        if isinstance(res, list):
            return res
        if isinstance(res, dict) and "data" in res and isinstance(res["data"], list):
            return res["data"]
        return []


def get_broker_gateway(dhan_account_id: str) -> DhanBrokerGateway:
    """Factory helper to obtain an account-scoped broker gateway instance."""
    return DhanBrokerGateway(dhan_account_id=dhan_account_id)
