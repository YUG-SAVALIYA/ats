"""
app/services/strategy.py
=========================
ATS Strategy Engine — Complete Final Strategy Implementation

Signal Generation (Post-Market Close):
- Completed candles only (latest daily, previous completed weekly for weekly RSI).
- Market Cap > ₹8,000 Cr.
- Daily RSI(14) in [50, 90].
- Weekly RSI(14) in [65, 85] (using previous completed weekly candle).
- Daily Supertrend (21, 1.5) flips from RED (-1) to GREEN (+1) on latest daily candle.
- Candle Range ((High - Low) / Low) * 100 in [3%, 12%].

Signal Validity & Reference Point:
- Signal valid ONLY for the next trading day.
- Reference Point: If Today Open > Signal High -> Reference = Today Open (gap-up), else Reference = Signal High.

Entry Evaluation (3:25 PM IST Only):
- Today High >= Reference * 1.03
- LTP > Signal High
- If met: Place MARKET BUY order (MTF if is_mtf=True, else CNC).

Signal Rejection Rules:
- Reject if Today Low <= Signal Low (SIGNAL_LOW_BROKEN)
- Reject if Today Low <= Reference * 0.95 (SIGNAL_HIGH_5_PERCENT_DRAWDOWN)
- Reject if 3:25 PM entry conditions not met.

Daily Supertrend RED Exit (3:25 PM IST Mandatory):
- Evaluates OPEN/PARTIAL_EXIT trades against Daily Supertrend(21, 1.5) using today's 3:25 PM bar.
- Exits immediately via MARKET SELL if Supertrend turns RED (-1).
"""

from __future__ import annotations

import time
import logging
import uuid
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
import pytz

from app.services.dhan_client import get_dhan_client
from app.services.portfolio import PortfolioService
from app.services.candle_sync import get_daily_candles_from_db, get_weekly_candles_from_db
from app.database import SessionLocal
from app.models import Company, Signal, Trade, AtsTradeState

logger = logging.getLogger("ats.strategy")
IST = pytz.timezone("Asia/Kolkata")

_engine_enabled = True
_automated_signals: List[Dict[str, Any]] = []


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculates Relative Strength Index (RSI 14) for a series of close prices.
    Uses Wilder's smoothed moving average (EMA-like), standard implementation.
    - On an UP day:   gain = change,        loss = 0
    - On a DOWN day:  gain = 0,             loss = abs(change)
    """
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
            gains.append(0.0)           # UP contribution is 0 on a down day
            losses.append(abs(change))  # LOSS is the absolute price drop

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calculate_supertrend(candles: List[Dict[str, float]], period: int = 21, multiplier: float = 1.5) -> List[int]:
    """Computes Supertrend directions for OHLC candles. Returns 1 for GREEN, -1 for RED."""
    if len(candles) < period + 1:
        return []

    tr = []
    for i in range(len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        if i == 0:
            tr.append(high - low)
        else:
            prev_close = candles[i - 1]["close"]
            tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr = [0.0] * len(candles)
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, len(candles)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    upper_band = [0.0] * len(candles)
    lower_band = [0.0] * len(candles)
    for i in range(len(candles)):
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2.0
        upper_band[i] = hl2 + (multiplier * atr[i])
        lower_band[i] = hl2 - (multiplier * atr[i])

    final_upper = [0.0] * len(candles)
    final_lower = [0.0] * len(candles)
    directions = [1] * len(candles)

    for i in range(period, len(candles)):
        close = candles[i]["close"]
        prev_close = candles[i - 1]["close"]

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
    """
    Returns only completed weekly candles using ISO calendar week comparison.
    A weekly candle is COMPLETED if its ISO week (year, week_num) is strictly BEFORE current_date's ISO week.
    If current_date is the last trading day of its week, that week is also considered COMPLETED.
    """
    if not weekly_candles:
        return []
    if current_date is None:
        current_date = date.today()

    curr_iso = current_date.isocalendar()[:2]  # (year, week_number)
    
    from app.services.market_calendar import is_trading_day
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


def evaluate_stock_signal(
    symbol: str,
    security_id: str,
    exchange_segment: str,
    daily_candles: List[Dict[str, Any]],
    weekly_candles: List[Dict[str, Any]],
    market_cap_cr: float,
    current_date: Optional[date] = None
) -> Optional[Dict[str, Any]]:
    """
    Evaluates signal generation rules after market close using completed candles only.
    """
    # Rule 1: Market cap > ₹8,000 Cr
    if market_cap_cr <= 8000.0:
        return None

    if len(daily_candles) < 22:
        return None

    # Completed weekly candles only for weekly RSI
    completed_weekly = filter_completed_weekly_candles(weekly_candles, current_date=current_date)
    if len(completed_weekly) < 15:
        return None

    # Daily RSI(14)
    daily_closes = [float(c["close"]) for c in daily_candles]
    daily_rsi = calculate_rsi(daily_closes, period=14)

    # Weekly RSI(14) using previous completed weekly candles
    weekly_closes = [float(c["close"]) for c in completed_weekly]
    weekly_rsi = calculate_rsi(weekly_closes, period=14)

    # Rule 2: Daily RSI between 50 and 90 (inclusive)
    if not (50.0 <= daily_rsi <= 90.0):
        return None

    # Rule 3: Weekly RSI between 65 and 85 (inclusive)
    if not (65.0 <= weekly_rsi <= 85.0):
        return None

    # Rule 4: Daily Supertrend (21, 1.5) flips from RED (-1) to GREEN (+1) on latest daily candle
    st_directions = calculate_supertrend(daily_candles, period=21, multiplier=1.5)
    if len(st_directions) < 2:
        return None

    prev_st = st_directions[-2]
    curr_st = st_directions[-1]

    if not (prev_st == -1 and curr_st == 1):
        return None

    # Rule 5: Signal candle range between 3% and 12% (inclusive)
    signal_candle = daily_candles[-1]
    high = float(signal_candle["high"])
    low = float(signal_candle["low"])
    open_p = float(signal_candle.get("open", high))
    close_p = float(signal_candle["close"])

    if low <= 0:
        return None

    candle_range = round(((high - low) / low) * 100.0, 2)
    if not (3.0 <= candle_range <= 12.0):
        return None

    sig_date_val = signal_candle.get("date", date.today())
    if isinstance(sig_date_val, str):
        sig_date_val = datetime.strptime(sig_date_val[:10], "%Y-%m-%d").date()
    elif isinstance(sig_date_val, datetime):
        sig_date_val = sig_date_val.date()
        
    # Prevent generating signals from historical/stale candles
    c_date = current_date or date.today()
    if sig_date_val < c_date:
        return None

    sig_date_str = sig_date_val.strftime("%Y-%m-%d")

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
        "status": "PENDING"
    }


def calculate_reference_price(signal_high: float, today_open: float) -> tuple[float, bool]:
    """
    Determines reference point on next trading day:
    - If today Open > Signal High -> Reference = Today Open (gap-up)
    - Otherwise -> Reference = Signal High
    """
    if today_open > signal_high:
        return today_open, True
    return signal_high, False


def _save_signal_to_db(company_id: str, signal_dict: Dict[str, Any]) -> Signal:
    """Saves detected signal to PostgreSQL `signals` table."""
    db = SessionLocal()
    try:
        sig_date = datetime.strptime(signal_dict["signal_date"], "%Y-%m-%d").date() if isinstance(signal_dict["signal_date"], str) else signal_dict["signal_date"]

        existing = db.query(Signal).filter(
            Signal.company_id == company_id,
            Signal.date == sig_date,
            Signal.status == "PENDING"
        ).first()

        if existing:
            return existing

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

        signal_obj = Signal(
            id=str(uuid.uuid4()),
            company_id=company_id,
            date=sig_date,
            raw_signal_data=raw_data,
            status="PENDING",
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


def get_signals_from_db(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetches signals from DB joined with companies."""
    db = SessionLocal()
    try:
        query = db.query(Signal, Company).join(Company, Signal.company_id == Company.id)
        if status:
            query = query.filter(Signal.status == status)

        results = query.order_by(Signal.date.desc(), Signal.created_at.desc()).limit(limit).all()

        output = []
        for sig, comp in results:
            raw = sig.raw_signal_data or {}
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
                "created_at": str(sig.created_at)
            })
        return output
    finally:
        db.close()


class AutomatedStrategyEngine:
    """Automated Trading & Signal Engine."""

    def __init__(self):
        self.portfolio_service = PortfolioService()

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
        """
        Evaluates PENDING signals at 3:25 PM IST using live Dhan OHLC/LTP.
        Only evaluates signals generated for TODAY. Stale signals (from prior days)
        are automatically marked as EXPIRED.
        """
        global _engine_enabled
        if not _engine_enabled:
            return {"status": "paused", "reason": "Engine disabled"}

        today = date.today()
        db = SessionLocal()
        try:
            all_pending = (
                db.query(Signal, Company)
                .join(Company, Signal.company_id == Company.id)
                .filter(Signal.status == "PENDING")
                .all()
            )

            # Auto-expire stale signals from prior trading days
            pending_signals = []
            for sig, comp in all_pending:
                if sig.date < today:
                    sig.status = "EXPIRED"
                    sig.expiry_date = datetime.utcnow()
                    sig.expiry_reason = "TARGET_AND_CLOSE_NOT_MET"
                    db.commit()
                    logger.info(f"[ENGINE] Auto-expired stale signal {sig.id[:8]} for {comp.trading_symbol} (signal_date={sig.date})")
                else:
                    pending_signals.append((sig, comp))

            if not pending_signals:
                return {"status": "no_pending_signals", "evaluated": 0, "executed": 0, "rejected": 0}

            sec_map = {}
            for sig, comp in pending_signals:
                if comp.dhan_security_id:
                    try:
                        sid_int = int(comp.dhan_security_id)
                        sec_map[sid_int] = (sig, comp)
                    except ValueError:
                        continue

            if not sec_map:
                return {"status": "no_valid_security_ids", "executed": 0}

            from app.services.dhan_client import get_dhan_data_client
            data_client = get_dhan_data_client()
            sec_ids_int = list(sec_map.keys())
            ohlc_feed = data_client.get_marketfeed_ohlc(sec_ids_int)

            executed_count = 0
            rejected_count = 0

            for sec_id_int, (sig, comp) in sec_map.items():
                feed_item = ohlc_feed.get(str(sec_id_int)) or ohlc_feed.get(sec_id_int)
                if not feed_item or not isinstance(feed_item, dict):
                    logger.warning(f"[ENGINE][3:25 PM] No live OHLC feed for {comp.trading_symbol} (sec_id={sec_id_int})")
                    continue

                today_open = float(feed_item.get("open") or 0.0)
                today_high = float(feed_item.get("high") or 0.0)
                today_low = float(feed_item.get("low") or 0.0)
                ltp = float(feed_item.get("last_price") or feed_item.get("close") or 0.0)

                raw = sig.raw_signal_data or {}
                signal_high = float(raw.get("signal_high") or 0.0)
                signal_low = float(raw.get("signal_low") or 0.0)

                if signal_high <= 0 or signal_low <= 0 or ltp <= 0:
                    continue

                # Reference price calculation
                ref_price, is_gap_up = calculate_reference_price(signal_high, today_open)
                raw["ref_price"] = ref_price
                raw["is_gap_up"] = is_gap_up
                sig.raw_signal_data = raw

                # ── Signal Rejection Rules ──────────────────────────────────
                rejection_reason = None
                if today_low > 0 and today_low <= signal_low:
                    rejection_reason = "SIGNAL_LOW_BROKEN"
                    logger.warning(f"[ENGINE][3:25 PM] REJECTED {comp.trading_symbol}: Today Low ({today_low}) <= Signal Low ({signal_low})")
                elif today_low > 0 and today_low <= (ref_price * 0.95):
                    rejection_reason = "SIGNAL_HIGH_5_PERCENT_DRAWDOWN"
                    logger.warning(f"[ENGINE][3:25 PM] REJECTED {comp.trading_symbol}: Today Low ({today_low}) <= Ref -5% ({ref_price * 0.95})")

                if rejection_reason:
                    sig.status = "REJECTED"
                    sig.rejection_reason = rejection_reason
                    sig.rejection_date = datetime.utcnow()
                    db.commit()
                    rejected_count += 1
                    continue

                # ── Entry Conditions Check (3:25 PM IST) ─────────────────────
                # 1. Today High >= Reference * 1.03
                # 2. LTP > Signal High
                cond_high_breakout = (today_high >= (ref_price * 1.03))
                cond_ltp_above_high = (ltp > signal_high)

                if cond_high_breakout and cond_ltp_above_high:
                    logger.info(
                        f"[ENGINE][3:25 PM] 🟢 ENTRY TRIGGERED for {comp.trading_symbol}! "
                        f"High ({today_high}) >= Ref+3% ({ref_price * 1.03}) AND LTP ({ltp}) > Signal High ({signal_high})"
                    )

                    try:
                        portfolio = self.portfolio_service.get_fund_limits()
                        avail_balance = float(portfolio.get("availabelBalance", portfolio.get("available_balance", 100000.0)) or 100000.0)

                        max_allocation_per_trade = min(avail_balance * 0.20, 50000.0)
                        allocated_capital = max(max_allocation_per_trade, 10000.0)
                        quantity = max(1, int(allocated_capital // ltp))

                        from app.core.executor import get_order_executor
                        executor = get_order_executor()

                        order_res = executor.place_entry_order(
                            security_id=comp.dhan_security_id,
                            trading_symbol=comp.trading_symbol,
                            company_id=comp.id,
                            signal_id=sig.id,
                            quantity=quantity,
                            allocated_capital=allocated_capital,
                            exchange_segment="NSE_EQ"
                        )

                        if order_res.get("status") in ("placed", "executed"):
                            executed_count += 1
                            sig.status = "EXECUTED"
                            sig.execution_date = datetime.utcnow()
                            db.commit()

                    except Exception as exc:
                        logger.error(f"[ENGINE][3:25 PM] Failed to execute entry for {comp.trading_symbol}: {exc}", exc_info=True)

                else:
                    # 3:25 PM entry conditions not met -> Reject signal
                    sig.status = "REJECTED"
                    sig.rejection_reason = "325_ENTRY_CONDITIONS_NOT_MET"
                    sig.rejection_date = datetime.utcnow()
                    db.commit()
                    rejected_count += 1
                    logger.info(
                        f"[ENGINE][3:25 PM] Signal REJECTED for {comp.trading_symbol}: "
                        f"HighBreakout={cond_high_breakout} (High={today_high} vs Ref+3%={ref_price * 1.03}), "
                        f"LTPBreakout={cond_ltp_above_high} (LTP={ltp} vs High={signal_high})"
                    )

            return {
                "status": "completed",
                "evaluated": len(sec_map),
                "executed": executed_count,
                "rejected": rejected_count
            }
        finally:
            db.close()

    def evaluate_and_execute_325_exits(self) -> Dict[str, Any]:
        """
        Evaluates OPEN and PARTIAL_EXIT trades at 3:25 PM IST against Daily Supertrend(21, 1.5).
        If Supertrend turns RED (-1), places MARKET SELL for remaining quantity immediately.
        """
        global _engine_enabled
        if not _engine_enabled:
            return {"status": "paused", "reason": "Engine disabled"}

        from app.core.engine import get_trade_engine
        trade_engine = get_trade_engine()

        db = SessionLocal()
        try:
            open_trades = (
                db.query(Trade, Company)
                .join(Company, Trade.company_id == Company.id)
                .filter(Trade.ats_state.in_([AtsTradeState.OPEN, AtsTradeState.PARTIAL_EXIT]))
                .all()
            )

            if not open_trades:
                return {"status": "no_open_trades", "exited": 0}

            sec_map = {}
            for trade, company in open_trades:
                if company.dhan_security_id:
                    try:
                        sid_int = int(company.dhan_security_id)
                        sec_map[sid_int] = (trade, company)
                    except ValueError:
                        continue

            if not sec_map:
                return {"status": "no_valid_security_ids", "exited": 0}

            from app.services.dhan_client import get_dhan_data_client
            data_client = get_dhan_data_client()
            sec_ids_int = list(sec_map.keys())
            ohlc_feed = data_client.get_marketfeed_ohlc(sec_ids_int)

            exited_count = 0

            for sec_id_int, (trade, company) in sec_map.items():
                feed_item = ohlc_feed.get(str(sec_id_int)) or ohlc_feed.get(sec_id_int)
                if not feed_item or not isinstance(feed_item, dict):
                    logger.warning(f"[ENGINE][3:25 PM EXIT] No live OHLC feed for {company.trading_symbol} (sec_id={sec_id_int})")
                    continue

                live_close = float(feed_item.get("last_price") or feed_item.get("close") or 0.0)
                live_high = float(feed_item.get("high") or live_close)
                live_low = float(feed_item.get("low") or live_close)

                if live_close <= 0.0:
                    continue

                try:
                    daily_candles = get_daily_candles_from_db(company.id, limit=60)
                    if len(daily_candles) < 22:
                        continue

                    today_date_str = date.today().strftime("%Y-%m-%d")
                    if daily_candles and daily_candles[-1].get("date") == today_date_str:
                        daily_candles[-1]["close"] = live_close
                        daily_candles[-1]["high"] = max(daily_candles[-1].get("high", live_high), live_high)
                        daily_candles[-1]["low"] = min(daily_candles[-1].get("low", live_low), live_low)
                        combined_candles = daily_candles
                    else:
                        live_candle = {
                            "date": today_date_str,
                            "open": live_close,
                            "high": live_high,
                            "low": live_low,
                            "close": live_close,
                            "volume": 0
                        }
                        combined_candles = daily_candles + [live_candle]

                    st_dirs = calculate_supertrend(combined_candles, period=21, multiplier=1.5)
                    if not st_dirs:
                        continue

                    latest_st = st_dirs[-1]

                    logger.info(
                        f"[ENGINE][3:25 PM] {company.trading_symbol} (sec_id={sec_id_int}): "
                        f"Close={live_close}, Supertrend(21, 1.5)={'GREEN (+1)' if latest_st == 1 else 'RED (-1)'}"
                    )

                    if latest_st == -1:
                        logger.info(f"[ENGINE][3:25 PM] 🔴 SUPERTREND RED for {company.trading_symbol}! Triggering exit.")

                        if trade_engine:
                            import asyncio
                            try:
                                loop = asyncio.get_running_loop()
                                loop.create_task(trade_engine.trigger_supertrend_exit(trade, live_close))
                            except RuntimeError:
                                asyncio.run(trade_engine.trigger_supertrend_exit(trade, live_close))
                            exited_count += 1

                except Exception as exc:
                    logger.error(f"[ENGINE][3:25 PM EXIT] Error processing exit for sec_id={sec_id_int}: {exc}")

            return {
                "status": "completed",
                "evaluated": len(sec_map),
                "exited": exited_count
            }
        finally:
            db.close()

    def scan_signals_from_db(self) -> List[Dict[str, Any]]:
        """Scan all active companies in DB after 100% candle sync & rate-limited fetching completes."""
        global _automated_signals
        logger.info("[ENGINE] Step 1: Checking missing candles & running 0.3s rate-limited fetch for all active companies...")

        from app.services.candle_sync import sync_all_active_companies
        sync_summary = sync_all_active_companies(limit=4000, delay_sec=0.3)
        logger.info(f"[ENGINE] Step 1 Complete: candles fetched/updated ({sync_summary}). Step 2: Expiring stale signals...")

        # Expire any PENDING signals from prior trading days before generating new ones
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

                if len(daily_candles) < 22 or len(weekly_candles) < 15:
                    continue

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
                    _save_signal_to_db(company.id, signal)
                    new_signals.append(signal)
                    logger.info(f"[ENGINE] NEW SIGNAL: {company.trading_symbol} — {signal['strategy']}")

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
