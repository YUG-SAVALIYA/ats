"""
app.trading.strategy
====================
ATS Strategy Engine — Complete Final Strategy Implementation.
"""

from __future__ import annotations

import time
import logging
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple
import pytz

from app.data.candles import get_daily_candles_from_db, get_weekly_candles_from_db, get_monthly_candles_from_db
from app.data.database import SessionLocal
from app.data.models import Company, Signal, Trade, AtsTradeState, DhanAccount, DailyCandle
from app.data.calendar import is_trading_day
from app.trading.monthly_rsi import evaluate_monthly_rsi_signal

logger = logging.getLogger("ats.strategy")
IST = pytz.timezone("Asia/Kolkata")

_engine_enabled = True
_automated_signals: List[Dict[str, Any]] = []


def _resolve_data_client():
    import sys
    for mod_name in ("app.broker.dhan_client", "app.trading.strategy"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "get_dhan_data_client"):
            fn = getattr(mod, "get_dhan_data_client")
            from app.broker.dhan_client import get_dhan_data_client as default_fn
            if fn is not default_fn:
                return fn()
    from app.broker.dhan_client import get_dhan_data_client
    return get_dhan_data_client()


def _resolve_account_context(account_id: str):
    import sys
    for mod_name in ("app.trading.execution", "app.broker.dhan_client", "app.trading.strategy"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "get_account_context"):
            fn = getattr(mod, "get_account_context")
            from app.broker.dhan_client import get_account_context as default_fn
            if fn is not default_fn:
                return fn(account_id)
    from app.broker.dhan_client import get_account_context
    return get_account_context(account_id)



def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """Calculates Relative Strength Index (RSI 14) for a series of close prices."""
    if len(prices) < period + 1:
        return 0.0

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change >= 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_supertrend(candles: List[Any], period: int = 21, multiplier: float = 1.5, lows: Optional[List[float]] = None, closes: Optional[List[float]] = None) -> List[int]:
    """Computes Supertrend directions for OHLC candles. Returns 1 for GREEN, -1 for RED."""
    if lows is not None and closes is not None:
        high_list = candles
        low_list = lows
        close_list = closes
    else:
        if len(candles) < period + 1:
            return []
        high_list = [c["high"] if isinstance(c, dict) else c.high for c in candles]
        low_list = [c["low"] if isinstance(c, dict) else c.low for c in candles]
        close_list = [c["close"] if isinstance(c, dict) else c.close for c in candles]

    n = len(high_list)
    if n < period + 1:
        return []

    tr = []
    for i in range(n):
        high = float(high_list[i])
        low = float(low_list[i])
        if i == 0:
            tr.append(high - low)
        else:
            prev_close = float(close_list[i - 1])
            tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = [0.0] * n
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    upper_band = [0.0] * n
    lower_band = [0.0] * n
    for i in range(n):
        hl2 = (float(high_list[i]) + float(low_list[i])) / 2.0
        upper_band[i] = hl2 + (multiplier * atr[i])
        lower_band[i] = hl2 - (multiplier * atr[i])

    final_upper = [0.0] * n
    final_lower = [0.0] * n
    directions = [1] * n

    for i in range(period, n):
        close = float(close_list[i])
        prev_close = float(close_list[i - 1])

        if upper_band[i] < final_upper[i - 1] or prev_close > final_upper[i - 1]:
            final_upper[i] = upper_band[i]
        else:
            final_upper[i] = final_upper[i - 1]

        if lower_band[i] > final_lower[i - 1] or prev_close < final_lower[i - 1]:
            final_lower[i] = lower_band[i]
        else:
            final_lower[i] = final_lower[i - 1]

        if directions[i - 1] == 1:
            if close < final_lower[i]:
                directions[i] = -1
            else:
                directions[i] = 1
        else:
            if close > final_upper[i]:
                directions[i] = 1
            else:
                directions[i] = -1

    return directions


def filter_completed_weekly_candles(weekly_candles: List[Dict[str, Any]], current_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """Returns only completed weekly candles using ISO calendar week comparison."""
    if not weekly_candles:
        return []
    if current_date is None:
        current_date = date.today()

    curr_iso = current_date.isocalendar()[:2]
    
    week_is_complete = True
    curr = current_date + timedelta(days=1)
    while curr.weekday() <= 6 and curr.isocalendar()[:2] == curr_iso:
        if is_trading_day(curr):
            week_is_complete = False
            break
        curr += timedelta(days=1)

    completed = []
    for c in weekly_candles:
        w_start = c.get("date") or c.get("week_start_date")
        if isinstance(w_start, str):
            w_start_date = datetime.strptime(w_start[:10], "%Y-%m-%d").date()
        elif isinstance(w_start, datetime):
            w_start_date = w_start.date()
        else:
            w_start_date = w_start

        if w_start_date:
            candle_iso = w_start_date.isocalendar()[:2]
            if candle_iso < curr_iso:
                completed.append(c)
            elif candle_iso == curr_iso and week_is_complete:
                completed.append(c)

    return completed if completed else (weekly_candles[:-1] if len(weekly_candles) > 1 else [])


def calculate_ema(prices: List[float], period: int = 20) -> float:
    if len(prices) < period:
        return 0.0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * multiplier + ema
    return ema


def calculate_rel_vol(volumes: List[float], period: int = 20) -> float:
    if len(volumes) < period:
        return 0.0
    sma = sum(volumes[-period:]) / period
    if sma == 0:
        return 0.0
    return volumes[-1] / sma


def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period * 2:
        return 0.0
    
    tr_list = []
    pdm_list = []
    ndm_list = []
    
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        ph = highs[i-1]
        pl = lows[i-1]
        pc = closes[i-1]
        
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
        
        up_move = h - ph
        down_move = pl - l
        
        pdm = up_move if up_move > down_move and up_move > 0 else 0.0
        ndm = down_move if down_move > up_move and down_move > 0 else 0.0
        
        pdm_list.append(pdm)
        ndm_list.append(ndm)
        
    str_val = sum(tr_list[:period])
    spdm = sum(pdm_list[:period])
    sndm = sum(ndm_list[:period])
    
    dx_list = []
    for i in range(period, len(tr_list)):
        str_val = str_val - (str_val / period) + tr_list[i]
        spdm = spdm - (spdm / period) + pdm_list[i]
        sndm = sndm - (sndm / period) + ndm_list[i]
        
        di_plus = 100 * (spdm / str_val) if str_val > 0 else 0.0
        di_minus = 100 * (sndm / str_val) if str_val > 0 else 0.0
        
        dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus) if (di_plus + di_minus) > 0 else 0.0
        dx_list.append(dx)
        
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
        
    return adx


def evaluate_stock_signal(
    symbol: str,
    security_id: str,
    exchange_segment: str,
    daily_candles: List[Dict[str, Any]],
    weekly_candles: List[Dict[str, Any]],
    market_cap_cr: float,
    current_date: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """Evaluates signal generation rules after market close using completed candles only."""
    from app.services.settings import get_strategy_settings
    settings = get_strategy_settings()
    
    if market_cap_cr <= settings["market_cap_min_cr"]:
        return None

    if len(daily_candles) < 22:
        return None

    completed_weekly = filter_completed_weekly_candles(weekly_candles, current_date=current_date)
    if len(completed_weekly) < 15:
        return None

    daily_closes = [float(c["close"]) for c in daily_candles]
    daily_rsi = calculate_rsi(daily_closes, period=settings["daily_rsi_period"])

    weekly_closes = [float(c["close"]) for c in completed_weekly]
    weekly_rsi = calculate_rsi(weekly_closes, period=settings["weekly_rsi_period"])

    if not (settings["daily_rsi_lower"] <= daily_rsi <= settings["daily_rsi_upper"]):
        return None

    if not (settings["weekly_rsi_lower"] <= weekly_rsi <= settings["weekly_rsi_upper"]):
        return None

    st_directions = calculate_supertrend(daily_candles, period=settings["supertrend_period"], multiplier=settings["supertrend_multiplier"])
    if len(st_directions) < 2:
        return None

    prev_st = st_directions[-2]
    curr_st = st_directions[-1]

    if not (prev_st == -1 and curr_st == 1):
        return None

    signal_candle = daily_candles[-1]
    high = float(signal_candle["high"])
    low = float(signal_candle["low"])
    open_p = float(signal_candle.get("open", high))
    close_p = float(signal_candle["close"])

    if low <= 0:
        return None

    candle_range = round(((high - low) / low) * 100.0, 2)
    if not (settings["candle_range_min"] <= candle_range <= settings["candle_range_max"]):
        return None

    sig_date_val = signal_candle.get("date", date.today())
    if isinstance(sig_date_val, str):
        sig_date_val = datetime.strptime(sig_date_val[:10], "%Y-%m-%d").date()
    elif isinstance(sig_date_val, datetime):
        sig_date_val = sig_date_val.date()
        
    c_date = current_date or date.today()
    if sig_date_val < c_date:
        return None

    sig_date_str = sig_date_val.strftime("%Y-%m-%d")

    ema_20_val = calculate_ema(daily_closes, period=20)
    price_vs_ema = 'Above' if close_p > ema_20_val else 'Below'
    trend_val = 'Uptrend'
    
    weekly_st = calculate_supertrend(completed_weekly, period=settings["supertrend_period"], multiplier=settings["supertrend_multiplier"])
    is_htf_uptrend = len(weekly_st) > 0 and weekly_st[-1] == 1
    
    daily_volumes = [float(c.get("volume", 0.0)) for c in daily_candles]
    rel_vol_val = calculate_rel_vol(daily_volumes, period=20)
    
    daily_highs = [float(c["high"]) for c in daily_candles]
    daily_lows = [float(c["low"]) for c in daily_candles]
    adx_val = calculate_adx(daily_highs, daily_lows, daily_closes, period=14)
    
    score = 0.0
    if price_vs_ema == 'Above': score += 15
    if trend_val == 'Uptrend': score += 10
    if is_htf_uptrend: score += 10
        
    if rel_vol_val >= 2.0: score += 20
    elif rel_vol_val >= 1.5: score += 15
    elif rel_vol_val >= 1.0: score += 10
    elif rel_vol_val >= 0.8: score += 5
        
    if daily_rsi >= 70: score += 30
    elif daily_rsi >= 60: score += 20
    elif daily_rsi >= 50: score += 10
        
    if adx_val >= 25: score += 15
    elif adx_val >= 20: score += 10
    elif adx_val >= 15: score += 5
        
    if score < settings.get("min_score", 65.0):
        return None

    return {
        "symbol": symbol.upper(),
        "security_id": str(security_id),
        "exchange_segment": exchange_segment,
        "strategy": "Supertrend(21,1.5) Flip + Daily RSI(50-90) + Weekly RSI(65-85) + Range(3-12%)",
        "signal_type": "BUY",
        "signal_date": sig_date_str,
        "signal_high": high,
        "signal_low": low,
        "signal_open": open_p,
        "signal_close": close_p,
        "daily_rsi": daily_rsi,
        "weekly_rsi": weekly_rsi,
        "candle_range": candle_range,
        "supertrend_flip": True,
        "market_cap_cr": market_cap_cr,
        "score": score,
        "status": "PENDING"
    }


def calculate_reference_price(signal_high: float, today_open: float) -> tuple[float, bool]:
    """Determines reference point on next trading day."""
    if today_open > signal_high:
        return today_open, True
    return signal_high, False


def _save_signal_to_db(company_id: str, signal_dict: Dict[str, Any], strategy_type: str = "SUPERTREND") -> Signal:
    """Saves detected signal to PostgreSQL `signals` table."""
    db = SessionLocal()
    try:
        sig_date = datetime.strptime(signal_dict["signal_date"], "%Y-%m-%d").date() if isinstance(signal_dict["signal_date"], str) else signal_dict["signal_date"]

        existing = db.query(Signal).filter(
            Signal.company_id == company_id,
            Signal.date == sig_date,
            Signal.status == "PENDING",
            Signal.strategy_type == strategy_type
        ).first()

        if existing:
            return existing

        if strategy_type == "SUPERTREND":
            raw_data = {
                "signal_high": signal_dict["signal_high"],
                "signal_low": signal_dict["signal_low"],
                "signal_open": signal_dict["signal_open"],
                "signal_close": signal_dict["signal_close"],
                "daily_rsi": signal_dict["daily_rsi"],
                "weekly_rsi": signal_dict["weekly_rsi"],
                "candle_range": signal_dict["candle_range"],
                "supertrend_flip": signal_dict["supertrend_flip"],
                "market_cap_cr": signal_dict["market_cap_cr"],
            }
        else:
            raw_data = signal_dict.get("raw_signal_data", {})
            raw_data.update({
                "signal_high": signal_dict["signal_high"],
                "signal_low": signal_dict["signal_low"],
                "signal_open": signal_dict["signal_open"],
                "signal_close": signal_dict["signal_close"],
            })

        signal_obj = Signal(
            id=str(uuid.uuid4()),
            company_id=company_id,
            date=sig_date,
            raw_signal_data=raw_data,
            status="PENDING",
            strategy_type=strategy_type,
            created_at=datetime.utcnow()
        )
        db.add(signal_obj)
        db.commit()
        db.refresh(signal_obj)
        return signal_obj
    except Exception as exc:
        db.rollback()
        logger.error(f"[ENGINE] Error saving signal to DB: {exc}")
        raise exc
    finally:
        db.close()


def get_signals_from_db(status: Optional[str] = None, strategy_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetches signals from DB joined with companies and trades."""
    db = SessionLocal()
    try:
        query = db.query(Signal, Company, Trade).join(Company, Signal.company_id == Company.id).outerjoin(Trade, Signal.id == Trade.signal_id)
        if status:
            query = query.filter(Signal.status == status)
        if strategy_type:
            query = query.filter(Signal.strategy_type == strategy_type)

        results = query.order_by(Signal.date.desc(), Signal.created_at.desc()).limit(limit).all()

        output = []
        for sig, comp, trade in results:
            raw = sig.raw_signal_data or {}
            executed_price = trade.entry_price if trade else None
            new_target_pct = trade.target_pct if trade else None
            new_sl_pct = trade.stoploss_pct if trade else None

            output.append({
                "id": sig.id,
                "company_id": sig.company_id,
                "trading_symbol": comp.trading_symbol,
                "security_id": comp.dhan_security_id,
                "company_name": comp.company_name,
                "signal_date": str(sig.date),
                "signal_high": raw.get("signal_high"),
                "signal_low": raw.get("signal_low"),
                "signal_open": raw.get("signal_open"),
                "signal_close": raw.get("signal_close"),
                "daily_rsi": raw.get("daily_rsi"),
                "weekly_rsi": raw.get("weekly_rsi"),
                "candle_range": raw.get("candle_range"),
                "supertrend_flip": raw.get("supertrend_flip"),
                "market_cap_cr": raw.get("market_cap_cr"),
                "ref_price": raw.get("ref_price"),
                "status": sig.status,
                "rejection_reason": sig.rejection_reason,
                "executed_price": executed_price,
                "new_target_pct": new_target_pct,
                "new_sl_pct": new_sl_pct,
                "created_at": str(sig.created_at)
            })
        return output
    finally:
        db.close()


class AutomatedStrategyEngine:
    """Automated Trading & Signal Engine."""

    def __init__(self):
        pass

    def is_enabled(self) -> bool:
        global _engine_enabled
        return _engine_enabled

    def set_enabled(self, state: bool) -> bool:
        global _engine_enabled
        _engine_enabled = state
        logger.info(f"[ENGINE] Strategy Engine set to: {'ENABLED' if _engine_enabled else 'PAUSED'}")
        return _engine_enabled

    def is_market_close_window(self) -> bool:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        return (now.hour == 15 and 30 <= now.minute <= 45) or (now.hour >= 16)

    def is_325_entry_window(self) -> bool:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return False
        return (now.hour == 15 and 20 <= now.minute <= 30)

    def evaluate_and_execute_325_entries(self) -> Dict[str, Any]:
        """Evaluates PENDING signals at 3:25 PM IST using live Dhan OHLC/LTP."""
        global _engine_enabled
        if not _engine_enabled:
            return {"status": "paused", "reason": "Engine disabled"}
            
        from app.services.settings import get_strategy_settings
        from app.data.models import DhanAccount
        settings = get_strategy_settings()

        today = date.today()
        db = SessionLocal()
        try:
            active_accounts = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").all()
            if not active_accounts:
                logger.warning("[ENGINE][3:25 PM] No active Dhan accounts found for entry execution.")
                return {"status": "no_active_accounts"}
            account_ids = [acc.id for acc in active_accounts]

            all_pending = (
                db.query(Signal, Company)
                .join(Company, Signal.company_id == Company.id)
                .filter(Signal.status == "PENDING")
                .all()
            )

            pending_signals = []
            for sig, comp in all_pending:
                if sig.date < today:
                    sig.status = "EXPIRED"
                    sig.expiry_date = datetime.utcnow()
                    sig.expiry_reason = "TARGET_AND_CLOSE_NOT_MET"
                    db.commit()
                    logger.info(f"[ENGINE] Auto-expired stale signal {sig.id[:8]} for {comp.trading_symbol}")
                else:
                    pending_signals.append((sig, comp))

            if not pending_signals:
                return {"status": "no_pending_signals", "evaluated": 0, "executed": 0, "rejected": 0}

            sec_map = {}
            for sig, comp in pending_signals:
                if comp.dhan_security_id:
                    try:
                        sec_map[int(comp.dhan_security_id)] = (sig, comp)
                    except ValueError:
                        continue

            if not sec_map:
                return {"status": "no_valid_security_ids", "executed": 0}

            data_client = _resolve_data_client()
            ohlc_feed = data_client.get_marketfeed_ohlc(list(sec_map.keys()))

            valid_signals = []
            rejected_count = 0

            for sec_id_int, (sig, comp) in sec_map.items():
                feed_item = ohlc_feed.get(str(sec_id_int)) or ohlc_feed.get(sec_id_int)
                if not feed_item or not isinstance(feed_item, dict):
                    logger.warning(f"[ENGINE][3:25 PM] No live OHLC for {comp.trading_symbol}")
                    continue

                today_open = float(feed_item.get("open") or 0.0)
                today_high = float(feed_item.get("high") or 0.0)
                today_low = float(feed_item.get("low") or 0.0)
                ltp = float(feed_item.get("last_price") or feed_item.get("close") or 0.0)

                raw = sig.raw_signal_data or {}
                signal_high = float(raw.get("signal_high", 0.0))
                signal_low = float(raw.get("signal_low", 0.0))

                if sig.strategy_type == "MONTHLY_RSI":
                    max_entry_gap_pct = settings.get("max_entry_gap_pct", 5.0)
                    signal_close = float(raw.get("signal_close", 0.0))
                    if signal_close <= 0 or today_open <= 0 or ltp <= 0: continue
                    entry_gap_pct = ((today_open - signal_close) / signal_close) * 100
                    if entry_gap_pct > max_entry_gap_pct:
                        sig.status = "REJECTED"
                        sig.rejection_reason = "ENTRY_GAP_TOO_LARGE"
                        sig.rejection_date = datetime.utcnow()
                        db.commit()
                        rejected_count += 1
                        continue
                    valid_signals.append((sig, comp, ltp, "MONTHLY_RSI"))
                else:
                    if signal_high <= 0 or signal_low <= 0 or ltp <= 0: continue
                    ref_price, is_gap_up = calculate_reference_price(signal_high, today_open)
                    raw["ref_price"] = ref_price
                    raw["is_gap_up"] = is_gap_up
                    sig.raw_signal_data = raw

                    rejection_reason = None
                    if today_low > 0 and today_low <= signal_low:
                        rejection_reason = "SIGNAL_LOW_BROKEN"
                    elif today_low > 0 and today_low <= (ref_price * 0.95):
                        rejection_reason = "SIGNAL_HIGH_5_PERCENT_DRAWDOWN"

                    if rejection_reason:
                        sig.status = "REJECTED"
                        sig.rejection_reason = rejection_reason
                        sig.rejection_date = datetime.utcnow()
                        db.commit()
                        rejected_count += 1
                        continue

                    breakout_multiplier = 1.0 + (settings["entry_high_breakout_pct"] / 100.0)
                    cond_high_breakout = (today_high >= (ref_price * breakout_multiplier))
                    cond_ltp_above_high = (ltp > signal_high)

                    if cond_high_breakout and cond_ltp_above_high:
                        valid_signals.append((sig, comp, ltp, "SUPERTREND"))
                    else:
                        sig.status = "REJECTED"
                        sig.rejection_reason = "325_ENTRY_CONDITIONS_NOT_MET"
                        sig.rejection_date = datetime.utcnow()
                        db.commit()
                        rejected_count += 1

            if not valid_signals:
                return {"status": "completed", "evaluated": len(sec_map), "executed": 0, "rejected": rejected_count}

            from app.trading.execution import get_order_executor
            from app.broker.dhan_client import get_account_context
            
            executor = get_order_executor()
            capital_pct = settings.get("capital_allocation_pct", 20.0) / 100.0

            def _process_account_sync(acc_id: str):
                placed = 0
                acc_client = _resolve_account_context(acc_id)
                try:
                    portfolio = acc_client.execute_v2_get('/fundlimit')
                    avail_balance = float(portfolio.get("availabelBalance", portfolio.get("available_balance", 100000.0)) or 100000.0)
                except Exception as e:
                    logger.error(f"[ENGINE][3:25 PM] Failed to fetch portfolio for acc {acc_id}: {e}")
                    return 0

                allocated_capital = max(avail_balance * capital_pct, 1000.0)
                
                for vsig, vcomp, vltp, vstrat in valid_signals:
                    quantity = max(1, int(allocated_capital // vltp))
                    try:
                        res = executor.place_entry_order(
                            dhan_account_id=acc_id,
                            security_id=vcomp.dhan_security_id,
                            trading_symbol=vcomp.trading_symbol,
                            company_id=vcomp.id,
                            signal_id=vsig.id,
                            quantity=quantity,
                            allocated_capital=allocated_capital,
                            exchange_segment="NSE_EQ",
                            strategy_type=vstrat
                        )
                        if res.get("status") in ("placed", "executed", "duplicate"):
                            placed += 1
                    except Exception as e:
                        logger.error(f"[ENGINE][3:25 PM] Failed entry for {acc_id} -> {vcomp.trading_symbol}: {e}")
                return placed

            from concurrent.futures import ThreadPoolExecutor
            
            with ThreadPoolExecutor(max_workers=min(10, max(1, len(account_ids)))) as pool:
                results = list(pool.map(_process_account_sync, account_ids))
            total_executed = sum(results)

            for vsig, _, _, _ in valid_signals:
                vsig.status = "EXECUTED"
                vsig.execution_date = datetime.utcnow()
            db.commit()

            return {
                "status": "completed",
                "evaluated": len(sec_map),
                "executed": total_executed,
                "rejected": rejected_count
            }
        finally:
            db.close()

    def evaluate_and_execute_325_exits(self) -> Dict[str, Any]:
        """3:25 PM Exit Evaluation."""
        db = SessionLocal()
        try:
            active_trades = (
                db.query(Trade, Company)
                .join(Company, Trade.company_id == Company.id)
                .filter(Trade.ats_state.in_([AtsTradeState.OPEN, AtsTradeState.PARTIAL_EXIT]))
                .all()
            )
            if not active_trades:
                return {"status": "completed", "evaluated": 0, "exited": 0}

            sec_map: Dict[int, List[Tuple[Trade, Company]]] = {}
            for t, c in active_trades:
                if c.dhan_security_id:
                    sec_id_int = int(c.dhan_security_id)
                    sec_map.setdefault(sec_id_int, []).append((t, c))

            if not sec_map:
                return {"status": "completed", "evaluated": 0, "exited": 0}

            dhan_data = _resolve_data_client()
            live_ohlc_map = dhan_data.get_marketfeed_ohlc(list(sec_map.keys()))

            trades_to_exit: List[Tuple[Trade, float]] = []

            for sec_id_int, trades_list in sec_map.items():
                ohlc_data = live_ohlc_map.get(sec_id_int)
                if not ohlc_data:
                    continue

                live_open = ohlc_data.get("open")
                live_high = ohlc_data.get("high")
                live_low = ohlc_data.get("low")
                live_close = ohlc_data.get("last_price") or ohlc_data.get("close")

                if not all([live_open, live_high, live_low, live_close]):
                    continue

                company = trades_list[0][1]
                hist_candles = (
                    db.query(DailyCandle)
                    .filter(DailyCandle.company_id == company.id)
                    .order_by(DailyCandle.date.desc())
                    .limit(60)
                    .all()
                )
                if not hist_candles:
                    continue

                hist_candles.reverse()

                highs = [c.high for c in hist_candles] + [live_high]
                lows = [c.low for c in hist_candles] + [live_low]
                closes = [c.close for c in hist_candles] + [live_close]

                try:
                    from app.services.settings import get_strategy_settings
                    strat_cfg = get_strategy_settings()
                    st_p = strat_cfg.get("supertrend_period", 21)
                    st_m = strat_cfg.get("supertrend_multiplier", 1.5)
                    st_dirs = calculate_supertrend(highs, period=st_p, multiplier=st_m, lows=lows, closes=closes)
                    if not st_dirs:
                        continue

                    latest_st = st_dirs[-1]
                    if latest_st == -1:
                        for tr, _ in trades_list:
                            trades_to_exit.append((tr, live_close))
                except Exception as exc:
                    logger.error(f"[ENGINE][3:25 PM EXIT] Error for sec_id={sec_id_int}: {exc}")

            if not trades_to_exit:
                return {"status": "completed", "evaluated": len(sec_map), "exited": 0}

            import asyncio
            from app.trading.trade_engine import get_trade_engine
            
            async def _exit_all():
                engine = get_trade_engine()
                sem = asyncio.Semaphore(5)
                async def _bounded_exit(tr, ltp):
                    async with sem:
                        await engine.trigger_supertrend_exit(tr, ltp)
                
                tasks = [_bounded_exit(tr, ltp) for tr, ltp in trades_to_exit]
                await asyncio.gather(*tasks)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_exit_all())
            except RuntimeError:
                asyncio.run(_exit_all())

            return {"status": "completed", "evaluated": len(sec_map), "exited": len(trades_to_exit)}
        finally:
            db.close()

    def scan_signals_from_db(self) -> List[Dict[str, Any]]:
        """Scan all active companies in DB for signals."""
        global _automated_signals
        logger.info("[ENGINE] Starting signal scan...")

        today = date.today()
        exp_db = SessionLocal()
        try:
            stale = exp_db.query(Signal).filter(Signal.status == "PENDING", Signal.date < today).all()
            if stale:
                for s in stale:
                    s.status = "EXPIRED"
                    s.expiry_date = datetime.utcnow()
                    s.expiry_reason = "TARGET_AND_CLOSE_NOT_MET"
                exp_db.commit()
                logger.info(f"[ENGINE] Expired {len(stale)} stale PENDING signal(s) from prior day(s).")
        except Exception as exc:
            logger.warning(f"[ENGINE] Failed to expire stale signals: {exc}")
            try:
                exp_db.rollback()
            except Exception:
                pass
        finally:
            exp_db.close()

        logger.info("[ENGINE] Step 3: Running signal scan...")
        db = SessionLocal()
        try:
            companies = (
                db.query(Company)
                .filter(Company.is_active == True, Company.dhan_security_id != "")
                .all()
            )
        finally:
            db.close()

        new_signals = []

        for company in companies:
            try:
                daily_candles = get_daily_candles_from_db(company.id, limit=60)
                weekly_candles = get_weekly_candles_from_db(company.id, limit=30)

                if len(daily_candles) >= 22 and len(weekly_candles) >= 15:
                    market_cap_cr = float(company.market_cap or 0)
                    signal = evaluate_stock_signal(
                        symbol=company.trading_symbol,
                        security_id=company.dhan_security_id,
                        exchange_segment="NSE_EQ",
                        daily_candles=daily_candles,
                        weekly_candles=weekly_candles,
                        market_cap_cr=market_cap_cr
                    )

                    if signal:
                        _save_signal_to_db(company.id, signal, strategy_type="SUPERTREND")
                        new_signals.append(signal)
                        logger.info(f"[ENGINE] NEW SUPERTREND SIGNAL: {company.trading_symbol}")

                monthly_candles = get_monthly_candles_from_db(company.id, limit=30)
                if len(monthly_candles) >= 14:
                    m_signal = evaluate_monthly_rsi_signal(
                        symbol=company.trading_symbol,
                        security_id=company.dhan_security_id,
                        exchange_segment="NSE_EQ",
                        daily_candles=daily_candles,
                        monthly_candles=monthly_candles
                    )
                    
                    if m_signal:
                        _save_signal_to_db(company.id, m_signal, strategy_type="MONTHLY_RSI")
                        new_signals.append(m_signal)
                        logger.info(f"[ENGINE] NEW MONTHLY RSI SIGNAL: {company.trading_symbol}")

            except Exception as exc:
                logger.warning(f"[ENGINE] Error evaluating {company.trading_symbol}: {exc}")

        _automated_signals = new_signals
        logger.info(f"[ENGINE] Scan complete. New signals found: {len(new_signals)}")
        return new_signals

    def run_iteration(self) -> Dict[str, Any]:
        """Run single automated pipeline check."""
        global _engine_enabled
        now = datetime.now(IST)

        if not _engine_enabled:
            return {"status": "paused", "message": "Engine is paused"}

        in_scan_window = self.is_market_close_window()
        in_325_window = self.is_325_entry_window()
        logger.info(f"[ENGINE] Running check at {now.strftime('%H:%M:%S')} IST (Scan: {in_scan_window}, 3:25 Entry/Exit: {in_325_window})...")

        if in_325_window:
            logger.info("[ENGINE] 3:25 PM Window — Running Entry Evaluation & Supertrend RED Exit Check...")
            self.evaluate_and_execute_325_entries()
            self.evaluate_and_execute_325_exits()

        if in_scan_window:
            logger.info("[ENGINE] Market close window — running signal scan...")
            self.scan_signals_from_db()

        return {
            "status": "running",
            "active_signals": len(get_signals_from_db(status="PENDING", limit=50)),
            "scan_window_active": in_scan_window,
            "entry_325_window_active": in_325_window,
            "last_tick": now.strftime("%H:%M:%S")
        }


_engine_instance: Optional[AutomatedStrategyEngine] = None


def get_strategy_engine() -> AutomatedStrategyEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = AutomatedStrategyEngine()
    return _engine_instance
