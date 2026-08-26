"""
app.broker.dhan_client
======================
Account-Scoped Dhan API Execution Context, Rate Limiters, and Data Client.
"""

import logging
import requests
import threading
import time
from typing import Optional, Any, Dict, List
from app.config import load_config, Config
from app.broker.dhan_auth import DhanAuthManager

logger = logging.getLogger("ats.dhan_client")

_data_client_instance: Optional["AccountExecutionContext"] = None
_lock = threading.Lock()


class ConfigAuthManager:
    """Fallback auth manager for the global Data Account configured via environment/config."""
    def __init__(self, client_id: str, pin: str, totp_secret: str, initial_token: str):
        self.client_id = client_id
        self.pin = pin
        self.totp_secret = totp_secret
        self._cached_token = initial_token
    
    def get_valid_token(self):
        return self._cached_token
        
    def refresh_token(self):
        logger.warning("[AUTH] Data Account token refresh not fully supported without DB.")
        return False


class TokenBucketRateLimiter:
    """Token-bucket rate limiter ensuring compliance with Dhan API rate limits."""
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate
        self.last_refill = time.time()
        self.lock = threading.Lock()
    
    def consume(self, tokens: int = 1) -> float:
        with self.lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0
            
            deficit = tokens - self.tokens
            return deficit / self.refill_rate

_global_rate_limiters = {}
_global_rl_lock = threading.Lock()


def get_rate_limiter(client_id: str) -> TokenBucketRateLimiter:
    with _global_rl_lock:
        if client_id not in _global_rate_limiters:
            _global_rate_limiters[client_id] = TokenBucketRateLimiter(8, 8.0)  # 8/sec per account
        return _global_rate_limiters[client_id]


class AccountExecutionContext:
    """Account-Scoped execution context. Completely replaces global trade client."""

    def __init__(self, dhan_account_id: str = None, auth_manager = None, account_label: str = "Trade"):
        self.account_label = account_label
        self.dhan_account_id = dhan_account_id
        
        if auth_manager:
            self.auth_manager = auth_manager
        else:
            self.auth_manager = DhanAuthManager(dhan_account_id)
            
        self.client_id = self.auth_manager.client_id
        self.order = None
        self.funds = None
        self.portfolio = None
        self._init_sdk()

    @property
    def config(self):
        class _ClientConfig:
            def __init__(self, client_id: str, pin: str, totp_secret: str):
                self.client_id = client_id
                self.pin = pin
                self.totp_secret = totp_secret
        return _ClientConfig(
            client_id=self.client_id or "",
            pin=getattr(self.auth_manager, "pin", "") or "",
            totp_secret=getattr(self.auth_manager, "totp_secret", "") or ""
        )

    def _init_sdk(self):
        token = self.auth_manager.get_valid_token()
        if not token or not self.client_id:
            logger.warning(f"[CLIENT][{self.account_label}] Dhan credentials missing or incomplete.")
            return

        try:
            from dhanhq import DhanContext, Order, Funds, Portfolio
            context = DhanContext(client_id=self.client_id, access_token=token)
            self.order = Order(context)
            self.funds = Funds(context)
            self.portfolio = Portfolio(context)
            logger.info(f"[CLIENT][{self.account_label}] DhanHQ SDK initialized for Client ID ***{self.client_id[-4:]}")
        except Exception as e:
            logger.error(f"[CLIENT][{self.account_label}] DhanHQ SDK initialization error: {e}")

    def execute_v2_get(self, endpoint_url: str) -> Any:
        """Executes GET request against Dhan V2 API with auto 401/403 token refresh & 0-item empty response handling."""
        logger.info(f"[CLIENT API CALL][{self.account_label}] GET {endpoint_url}")
        token = self.auth_manager.get_valid_token()
        headers = {
            "Accept": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or ""
        }
        
        try:
            resp = requests.get(endpoint_url, headers=headers, timeout=12)
        except Exception as exc:
            logger.error(f"[CLIENT][{self.account_label}] Request exception for {endpoint_url}: {exc}")
            return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

        if resp.status_code in (401, 403) or is_auth_error(resp.json() if _is_json(resp) else None):
            logger.warning(f"[CLIENT][{self.account_label}] Auth error from {endpoint_url}. Triggering token refresh...")
            if self.auth_manager.refresh_token():
                new_token = self.auth_manager.get_valid_token()
                self._init_sdk()
                headers["access-token"] = new_token or ""
                headers["client-id"] = self.client_id or ""
                try:
                    resp = requests.get(endpoint_url, headers=headers, timeout=12)
                except Exception as exc:
                    return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

        if _is_json(resp):
            json_body = resp.json()
            if is_empty_portfolio_response(json_body):
                return []
            if resp.status_code == 200:
                if isinstance(json_body, dict) and is_error_response(json_body):
                    json_body["http_code"] = 400
                return json_body

        if resp.status_code == 200:
            return []
        else:
            return {
                "status": "failure",
                "remarks": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "http_code": resp.status_code
            }

    def execute_v2_post(self, endpoint_url: str, payload: dict) -> Any:
        """Executes POST request against Dhan V2 API with auto 401/403 token refresh & 429 backoff."""
        import random
        logger.info(f"[CLIENT API CALL][{self.account_label}] POST {endpoint_url}")
        token = self.auth_manager.get_valid_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or ""
        }
        
        limiter = get_rate_limiter(self.client_id or "default")
        
        for attempt in range(4):
            wait_time = limiter.consume()
            if wait_time > 0:
                time.sleep(wait_time)
                
            try:
                resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=12)
            except Exception as exc:
                logger.error(f"[CLIENT][{self.account_label}] POST exception for {endpoint_url}: {exc}")
                return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

            if resp.status_code == 429:
                backoff = (2 ** attempt) + random.uniform(0.1, 0.5)
                logger.warning(f"[CLIENT][{self.account_label}] 429 Too Many Requests on POST. Backoff {backoff:.2f}s")
                time.sleep(backoff)
                continue

            if resp.status_code in (401, 403) or is_auth_error(resp.json() if _is_json(resp) else None):
                logger.warning(f"[CLIENT][{self.account_label}] Auth error from {endpoint_url}. Triggering token refresh...")
                if self.auth_manager.refresh_token():
                    new_token = self.auth_manager.get_valid_token()
                    self._init_sdk()
                    headers["access-token"] = new_token or ""
                    headers["client-id"] = self.client_id or ""
                    try:
                        resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=12)
                    except Exception as exc:
                        return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

            if _is_json(resp):
                json_body = resp.json()
                if resp.status_code in (200, 201, 202):
                    if isinstance(json_body, dict) and is_error_response(json_body):
                        json_body["http_code"] = 400
                    return json_body

            if resp.status_code in (200, 201, 202):
                return {}
            else:
                return {
                    "status": "failure",
                    "remarks": f"HTTP {resp.status_code}: {resp.text[:200]}",
                    "http_code": resp.status_code
                }
                
        return {"status": "failure", "remarks": "Max retries exceeded for 429", "http_code": 429}

    def execute_v2_delete(self, endpoint_url: str) -> Any:
        """Executes DELETE request against Dhan V2 API."""
        logger.info(f"[CLIENT API CALL][{self.account_label}] DELETE {endpoint_url}")
        token = self.auth_manager.get_valid_token()
        headers = {
            "Accept": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or "",
        }

        try:
            resp = requests.delete(endpoint_url, headers=headers, timeout=12)
        except Exception as exc:
            logger.error(f"[CLIENT][{self.account_label}] DELETE exception for {endpoint_url}: {exc}")
            return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

        if resp.status_code in (401, 403) or is_auth_error(resp.json() if _is_json(resp) else None):
            logger.warning(f"[CLIENT][{self.account_label}] Auth error on DELETE {endpoint_url}. Refreshing token...")
            if self.auth_manager.refresh_token():
                new_token = self.auth_manager.get_valid_token()
                self._init_sdk()
                headers["access-token"] = new_token or ""
                try:
                    resp = requests.delete(endpoint_url, headers=headers, timeout=12)
                except Exception as exc:
                    return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

        if _is_json(resp):
            json_body = resp.json()
            if resp.status_code in (200, 201, 202):
                return json_body

        if resp.status_code in (200, 201, 202):
            return {"status": "accepted"}
        else:
            return {
                "status": "failure",
                "remarks": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "http_code": resp.status_code,
            }

    def get_marketfeed_ohlc(self, nse_eq_sec_ids: List[int]) -> Dict[str, Any]:
        """Fetch live Open, High, Low, Close, and last_price for a list of NSE_EQ security IDs."""
        if not nse_eq_sec_ids:
            return {}

        logger.info(f"[CLIENT API CALL][{self.account_label}] POST https://api.dhan.co/v2/marketfeed/ohlc (IDs: {nse_eq_sec_ids})")
        token = self.auth_manager.get_valid_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or ""
        }
        payload = {
            "NSE_EQ": [int(sid) for sid in nse_eq_sec_ids],
            "NSE_FNO": [],
            "BSE_EQ": []
        }
        url = "https://api.dhan.co/v2/marketfeed/ohlc"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code in (401, 403):
                logger.warning(f"[CLIENT][{self.account_label}] Marketfeed OHLC 401/403 auth error. Refreshing token...")
                if self.auth_manager.refresh_token():
                    headers["access-token"] = self.auth_manager.get_valid_token() or ""
                    resp = requests.post(url, json=payload, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json() if _is_json(resp) else {}
                if isinstance(data, dict) and "data" in data:
                    return data.get("data", {}).get("NSE_EQ", {})
                return {}
            logger.warning(f"[CLIENT][{self.account_label}] Marketfeed OHLC API HTTP {resp.status_code}: {resp.text[:150]}")
            return {}
        except requests.exceptions.Timeout:
            logger.error(f"[CLIENT][{self.account_label}] Marketfeed OHLC request timed out for IDs: {nse_eq_sec_ids[:5]}...")
            return {}
        except requests.exceptions.RequestException as req_err:
            logger.error(f"[CLIENT][{self.account_label}] Marketfeed OHLC network request error: {req_err}")
            return {}
        except Exception as exc:
            logger.error(f"[CLIENT][{self.account_label}] Unexpected error in get_marketfeed_ohlc: {exc}", exc_info=True)
            return {}

    def get_marketfeed_ltp(self, nse_eq_sec_ids: List[int]) -> Dict[str, Any]:
        """Fetch live last_price for a list of NSE_EQ security IDs."""
        if not nse_eq_sec_ids:
            return {}

        logger.info(f"[CLIENT API CALL][{self.account_label}] POST https://api.dhan.co/v2/marketfeed/ltp (IDs: {nse_eq_sec_ids})")
        token = self.auth_manager.get_valid_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or ""
        }
        payload = {
            "NSE_EQ": [int(sid) for sid in nse_eq_sec_ids],
            "NSE_FNO": [],
            "BSE_EQ": []
        }
        url = "https://api.dhan.co/v2/marketfeed/ltp"
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            if resp.status_code in (401, 403):
                logger.warning(f"[CLIENT][{self.account_label}] Marketfeed LTP 401/403 auth error. Refreshing token...")
                if self.auth_manager.refresh_token():
                    headers["access-token"] = self.auth_manager.get_valid_token() or ""
                    resp = requests.post(url, json=payload, headers=headers, timeout=10)

            if resp.status_code == 200:
                data = resp.json() if _is_json(resp) else {}
                if isinstance(data, dict) and "data" in data:
                    return data.get("data", {}).get("NSE_EQ", {})
                return {}
            logger.warning(f"[CLIENT][{self.account_label}] Marketfeed LTP API HTTP {resp.status_code}: {resp.text[:150]}")
            return {}
        except requests.exceptions.Timeout:
            logger.error(f"[CLIENT][{self.account_label}] Marketfeed LTP request timed out for IDs: {nse_eq_sec_ids[:5]}...")
            return {}
        except requests.exceptions.RequestException as req_err:
            logger.error(f"[CLIENT][{self.account_label}] Marketfeed LTP network request error: {req_err}")
            return {}
        except Exception as exc:
            logger.error(f"[CLIENT][{self.account_label}] Unexpected error in get_marketfeed_ltp: {exc}", exc_info=True)
            return {}


def is_empty_portfolio_response(data: Any) -> bool:
    if data is None:
        return True
    if isinstance(data, list) and len(data) == 0:
        return True
    if isinstance(data, dict):
        err_code = str(data.get("errorCode", "") or data.get("errorType", "")).upper()
        err_msg = str(data.get("errorMessage", "") or data.get("remarks", "") or data.get("message", "")).lower()
        if "DH-1111" in err_code or "DH_1111" in err_code:
            return True
        if any(msg in err_msg for msg in ("no holdings", "no positions", "no orders", "no trades", "no data")):
            return True
    return False


def is_error_response(res: Any) -> bool:
    if not isinstance(res, dict):
        return False
    if is_empty_portfolio_response(res):
        return False
    status = str(res.get("status", "")).lower()
    return status in ("failure", "failed", "error") or ("remarks" in res and "error" in str(res.get("remarks")).lower())


def is_auth_error(data: Optional[dict]) -> bool:
    if not data or not isinstance(data, dict):
        return False
    combined = f"{data.get('status', '')} {data.get('remarks', '')} {data.get('message', '')} {data.get('errorCode', '')} {data.get('errorMessage', '')} {data.get('errorType', '')}".lower()
    return any(k in combined for k in ("unauthorized", "invalid token", "token expired", "forbidden", "dh-901", "dh-906"))


def _is_json(resp) -> bool:
    try:
        resp.json()
        return True
    except Exception:
        return False


def get_account_context(dhan_account_id: str) -> AccountExecutionContext:
    """Returns a new execution context strictly bound to a specific Dhan Account."""
    return AccountExecutionContext(dhan_account_id=dhan_account_id)


def get_dhan_data_client() -> AccountExecutionContext:
    """Returns singleton DhanClient for Market Data Account."""
    global _data_client_instance
    if _data_client_instance is not None:
        return _data_client_instance
    with _lock:
        if _data_client_instance is not None:
            return _data_client_instance
        cfg = load_config()
        
        auth_mgr = ConfigAuthManager(
            client_id=cfg.data_client_id,
            pin=cfg.data_pin,
            totp_secret=cfg.data_totp_secret,
            initial_token=cfg.data_access_token
        )
        
        _data_client_instance = AccountExecutionContext(
            auth_manager=auth_mgr,
            account_label="DataAccount"
        )
        return _data_client_instance


def get_dhan_client(*args, **kwargs) -> None:
    """Deprecated: Raises explicit RuntimeError."""
    raise RuntimeError(
        "get_dhan_client() is deprecated and forbidden in production ATS. "
        "Use get_account_context(dhan_account_id) for trade execution or get_dhan_data_client() for market data."
    )


