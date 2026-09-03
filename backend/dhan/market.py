"""
dhan/market.py — Dhan Market Data API (Historical Charts & Live Feeds)
======================================================================
Provides wrappers for Dhan Historical Charts API and snapshot Marketfeed OHLC/LTP.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from dhan.client import get_dhan_data_client, is_error_response
from dhan.endpoints import MARKET_HISTORICAL_CHARTS_URL, SCRIP_MASTER_CSV_URL

logger = logging.getLogger("ats.dhan.market")

DHAN_HISTORY_URL = MARKET_HISTORICAL_CHARTS_URL


def fetch_historical_ohlcv(
    security_id: str,
    exchange_segment: str,
    from_date: str,
    to_date: str,
    symbol: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """Fetch OHLCV candles from Dhan Historical Charts API with automatic Scrip Master auto-healing fallback."""
    client = get_dhan_data_client()

    # Ensure from_date is strictly before to_date
    if from_date >= to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            from_date = (to_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        except Exception:
            pass

    payload = {
        "securityId": str(security_id),
        "exchangeSegment": exchange_segment or "NSE_EQ",
        "instrument": "EQUITY",
        "expiryCode": 0,
        "oi": False,
        "fromDate": from_date,
        "toDate": to_date
    }
    sym_str = f" ({symbol})" if symbol else ""
    current_sec_id = str(security_id)

    try:
        data = client.execute_v2_post(DHAN_HISTORY_URL, payload)

        # Market Hours Fallback: Auto-heal if invalid security ID
        if isinstance(data, dict) and is_invalid_security_error(data) and symbol:
            logger.warning(
                f"[DHAN MARKET] Invalid Security ID '{current_sec_id}' detected for {symbol} "
                f"[{exchange_segment}, {from_date}..{to_date}]. Fetching fresh Scrip Master..."
            )
            new_sec_id = auto_heal_security_id(symbol)
            if new_sec_id and str(new_sec_id) != current_sec_id:
                logger.info(f"[DHAN MARKET] Retrying historical fetch for {symbol} with official ID {new_sec_id}...")
                current_sec_id = str(new_sec_id)
                payload["securityId"] = current_sec_id
                data = client.execute_v2_post(DHAN_HISTORY_URL, payload)

        if not isinstance(data, dict):
            logger.warning(
                f"[DHAN MARKET] Unexpected non-dict response for sec_id={current_sec_id}{sym_str} "
                f"[{exchange_segment}, {from_date}..{to_date}]: {data}"
            )
            return None

        # Check for explicit API error response from Dhan
        if is_error_response(data) or data.get("status") in ("failure", "failed", "error"):
            err_msg = data.get("remarks") or data.get("message") or data.get("errorMessage") or data
            logger.warning(
                f"[DHAN MARKET] API error response for sec_id={current_sec_id}{sym_str} "
                f"[{exchange_segment}, {from_date}..{to_date}]: {err_msg}"
            )
            return None

        timestamps = data.get("timestamp", [])
        opens      = data.get("open", [])
        highs      = data.get("high", [])
        lows       = data.get("low", [])
        closes     = data.get("close", [])
        volumes    = data.get("volume", [])

        if not timestamps or not closes:
            logger.info(
                f"[DHAN MARKET] No candle records returned for sec_id={current_sec_id}{sym_str} "
                f"[{exchange_segment}, {from_date}..{to_date}]"
            )
            return None

        total_received = len(timestamps)
        parsed_count = 0
        skipped_parse_errors = 0
        skipped_weekends = 0
        candles = []

        for i in range(total_received):
            ts = timestamps[i] if i < len(timestamps) else None
            try:
                if ts is None:
                    raise ValueError(f"Missing timestamp at index {i}")
                if isinstance(ts, (int, float)):
                    # Convert UTC epoch timestamp to IST date (+5:30)
                    candle_date = (datetime.utcfromtimestamp(ts) + timedelta(hours=5, minutes=30)).date()
                else:
                    candle_date = datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()

                # Filter out Saturday (5) and Sunday (6) daily candles for NSE/BSE Equity
                if candle_date.weekday() >= 5:
                    skipped_weekends += 1
                    continue

                open_val = float(opens[i]) if i < len(opens) and opens[i] is not None else 0.0
                high_val = float(highs[i]) if i < len(highs) and highs[i] is not None else 0.0
                low_val = float(lows[i]) if i < len(lows) and lows[i] is not None else 0.0
                close_val = float(closes[i]) if i < len(closes) and closes[i] is not None else 0.0
                vol_val = int(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0

                candles.append({
                    "date":   candle_date,
                    "open":   open_val,
                    "high":   high_val,
                    "low":    low_val,
                    "close":  close_val,
                    "volume": vol_val,
                })
                parsed_count += 1
            except Exception as parse_err:
                skipped_parse_errors += 1
                logger.warning(
                    f"[DHAN MARKET] Malformed candle at index {i} (ts={ts}) for sec_id={current_sec_id}{sym_str}: {parse_err}"
                )
                continue

        logger.info(
            f"[DHAN MARKET] sec_id={current_sec_id}{sym_str} [{from_date}..{to_date}]: "
            f"received={total_received}, parsed={parsed_count}, skipped_weekend={skipped_weekends}, parse_errors={skipped_parse_errors}"
        )

        return candles

    except Exception as exc:
        logger.exception(
            f"[DHAN MARKET] Fetch historical exception for sec_id={current_sec_id}{sym_str} "
            f"[{exchange_segment}, {from_date}..{to_date}]: {exc}"
        )
        return None


def get_live_ohlc(security_ids: List[int]) -> Dict[str, Any]:
    """Fetch live snapshot OHLC for given NSE_EQ security IDs."""
    client = get_dhan_data_client()
    return client.get_marketfeed_ohlc(security_ids)


def get_live_ltp(security_ids: List[int]) -> Dict[str, Any]:
    """Fetch live snapshot LTP for given NSE_EQ security IDs."""
    client = get_dhan_data_client()
    return client.get_marketfeed_ltp(security_ids)


_scrip_cache: Dict[str, str] = {}
_scrip_cache_time: float = 0.0
_SCRIP_CACHE_TTL_SEC: float = 300.0  # 5 minutes in-memory cache


def is_invalid_security_error(resp: Any) -> bool:
    """Checks if API response indicates an invalid or unknown security ID."""
    if not isinstance(resp, dict):
        return False
    combined = f"{resp.get('status', '')} {resp.get('remarks', '')} {resp.get('message', '')} {resp.get('errorCode', '')} {resp.get('errorMessage', '')} {resp.get('errorType', '')}".lower()
    return any(k in combined for k in ("invalid security", "invalid instrument", "security not found", "dh-905", "invalid_security", "scrip not found"))


def get_official_scrip_id(symbol: str, force_refresh: bool = False) -> Optional[str]:
    """Looks up official security ID for a symbol from Dhan Scrip Master."""
    global _scrip_cache, _scrip_cache_time
    import time
    import csv
    import io
    import requests

    now = time.time()
    clean_sym = symbol.strip().upper()

    # Refresh cache if expired or forced
    if force_refresh or not _scrip_cache or (now - _scrip_cache_time) > _SCRIP_CACHE_TTL_SEC:
        try:
            url = SCRIP_MASTER_CSV_URL
            logger.info(f"[SCRIP LOOKUP] Downloading latest Dhan Scrip Master CSV from {url}...")
            resp = requests.get(url, timeout=25)
            if resp.status_code == 200:
                new_cache = {}
                reader = csv.DictReader(io.StringIO(resp.text))
                for row in reader:
                    seg = row.get("SEM_SEGMENT", "").strip().upper()
                    exch = row.get("SEM_EXM_EXCH_ID", "").strip().upper()
                    sym = (row.get("SEM_TRADING_SYMBOL", "") or row.get("SEM_CUSTOM_SYMBOL", "")).strip().upper()
                    sec_id = row.get("SEM_SMST_SECURITY_ID", "").strip()
                    if seg == "E" and sym and sec_id:
                        if exch == "NSE":
                            new_cache[sym] = sec_id
                        elif exch == "BSE" and sym not in new_cache:
                            new_cache[sym] = sec_id
                _scrip_cache = new_cache
                _scrip_cache_time = now
        except Exception as exc:
            logger.error(f"[SCRIP LOOKUP] Error downloading Scrip Master: {exc}")

    return _scrip_cache.get(clean_sym)


def auto_heal_security_id(symbol: str) -> Optional[str]:
    """
    Market Hours Fallback: If an invalid security ID is encountered, fetches the latest
    Dhan Scrip Master, auto-updates PostgreSQL `companies` table if changed, and returns the official ID.
    """
    clean_sym = symbol.strip().upper()
    official_id = get_official_scrip_id(clean_sym, force_refresh=True)
    if not official_id:
        logger.warning(f"[AUTO HEAL] Symbol '{clean_sym}' not found in Dhan Scrip Master.")
        return None

    from database.database import SessionLocal
    from database.models import Company

    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.trading_symbol.ilike(clean_sym)).first()
        if company:
            old_id = str(company.dhan_security_id or "").strip()
            if old_id != official_id:
                company.dhan_security_id = official_id
                db.commit()
                logger.info(f"[AUTO HEAL] Auto-updated {clean_sym} Security ID in DB from {old_id} -> {official_id}")
            else:
                logger.info(f"[AUTO HEAL] Verified {clean_sym} Security ID is {official_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"[AUTO HEAL] Error updating company {clean_sym} in DB: {e}")
    finally:
        db.close()

    return official_id


def sync_dhan_scrip_master() -> int:
    """Downloads official Dhan Scrip Master CSV and synchronizes company security IDs in PostgreSQL."""
    import csv
    import io
    import requests
    from database.database import SessionLocal
    from database.models import Company

    url = SCRIP_MASTER_CSV_URL
    logger.info(f"[DHAN MASTER] Downloading official Dhan Scrip Master from {url}...")

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            logger.error(f"[DHAN MASTER] Failed to download Scrip Master (HTTP {resp.status_code})")
            return 0

        dhan_nse = {}
        dhan_bse = {}
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            exch = row.get("SEM_EXM_EXCH_ID", "").strip().upper()
            seg = row.get("SEM_SEGMENT", "").strip().upper()
            sym = (row.get("SEM_TRADING_SYMBOL", "") or row.get("SEM_CUSTOM_SYMBOL", "")).strip().upper()
            sec_id = row.get("SEM_SMST_SECURITY_ID", "").strip()
            
            if seg == "E" and sym and sec_id:
                if exch == "NSE":
                    dhan_nse[sym] = sec_id
                elif exch == "BSE":
                    dhan_bse[sym] = sec_id

        db = SessionLocal()
        updated_count = 0
        try:
            companies = db.query(Company).all()
            for c in companies:
                sym = (c.trading_symbol or "").strip().upper()
                official_id = dhan_nse.get(sym) or dhan_bse.get(sym)
                if official_id and str(c.dhan_security_id or "").strip() != official_id:
                    c.dhan_security_id = official_id
                    updated_count += 1
            
            if updated_count > 0:
                db.commit()
                logger.info(f"[DHAN MASTER] Updated {updated_count} changed Security IDs from Dhan Scrip Master.")
            else:
                logger.info("[DHAN MASTER] All company Security IDs are 100% up to date with Dhan Scrip Master.")
        finally:
            db.close()

        return updated_count
    except Exception as e:
        logger.error(f"[DHAN MASTER] Error syncing Dhan Scrip Master: {e}")
        return 0
