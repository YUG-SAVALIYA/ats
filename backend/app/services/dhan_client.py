import logging
import requests
import threading
from typing import Optional, Any, Dict, List
from app.config import load_config, Config
from app.services.auth import DhanAuthManager

logger = logging.getLogger("ats.client")

_trade_client_instance: Optional["DhanClient"] = None
_data_client_instance: Optional["DhanClient"] = None
_lock = threading.Lock()


class DhanClient:
    """Singleton Facade for official Dhan HQ API v2 interactions."""

    def __init__(self, client_id: str, pin: str, totp_secret: str, access_token: str = "", account_label: str = "Trade"):
        self.account_label = account_label
        self.client_id = client_id
        self.auth_manager = DhanAuthManager(
            client_id=client_id,
            pin=pin,
            totp_secret=totp_secret,
            initial_token=access_token
        )
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
        """Executes POST request against Dhan V2 API with auto 401/403 token refresh."""
        logger.info(f"[CLIENT API CALL][{self.account_label}] POST {endpoint_url}")
        token = self.auth_manager.get_valid_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": token or "",
            "client-id": self.client_id or ""
        }
        
        try:
            resp = requests.post(endpoint_url, json=payload, headers=headers, timeout=12)
        except Exception as exc:
            logger.error(f"[CLIENT][{self.account_label}] POST exception for {endpoint_url}: {exc}")
            return {"status": "failure", "remarks": f"Network Error: {exc}", "http_code": 503}

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
            if is_empty_portfolio_response(json_body):
                return []
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


def get_dhan_client() -> DhanClient:
    """Returns singleton DhanClient for Trade / Order Execution Account (1106585038)."""
    global _trade_client_instance
    if _trade_client_instance is not None:
        return _trade_client_instance
    with _lock:
        if _trade_client_instance is not None:
            return _trade_client_instance
        cfg = load_config()
        _trade_client_instance = DhanClient(
            client_id=cfg.client_id,
            pin=cfg.pin,
            totp_secret=cfg.totp_secret,
            access_token=cfg.access_token,
            account_label="TradeAccount"
        )
        return _trade_client_instance


def get_dhan_data_client() -> DhanClient:
    """Returns singleton DhanClient for Premium Market Data Account (1111482994)."""
    global _data_client_instance
    if _data_client_instance is not None:
        return _data_client_instance
    with _lock:
        if _data_client_instance is not None:
            return _data_client_instance
        cfg = load_config()
        _data_client_instance = DhanClient(
            client_id=cfg.data_client_id,
            pin=cfg.data_pin,
            totp_secret=cfg.data_totp_secret,
            access_token=cfg.data_access_token,
            account_label="DataAccount"
        )
        return _data_client_instance
