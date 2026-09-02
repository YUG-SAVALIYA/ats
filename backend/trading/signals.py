"""
trading/signals.py — Signal Generation, Reference Math & Evaluation Service
===========================================================================
Scans active securities for strategy triggers, calculates reference points,
manages signal persistence in DB, and evaluates 3:25 PM qualification.
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, date, timedelta
from collections import defaultdict
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import text

from database.database import SessionLocal
from database.models import Company, Signal, Trade, StrategySettings
from market.candles import (
    get_daily_candles_from_db,
    get_weekly_candles_from_db,
    get_monthly_candles_from_db,
)
from trading.strategies import (
    evaluate_stock_signal,
    evaluate_monthly_rsi_signal,
)

logger = logging.getLogger("ats.trading.signals")


def calculate_reference_price(signal_high: float, today_open: float) -> Tuple[float, bool]:
    """
    Determines reference point on next trading day:
    - If today Open > Signal High -> Reference = Today Open (gap-up)
    - Otherwise -> Reference = Signal High
    """
    if today_open > signal_high:
        return today_open, True
    return signal_high, False


def save_signal_to_db(company_id: str, signal_dict: Dict[str, Any], strategy_type: str = "SUPERTREND") -> Signal:
    """Saves detected signal to PostgreSQL `signals` table."""
    db = SessionLocal()
    try:
        sig_date = datetime.strptime(signal_dict["signal_date"], "%Y-%m-%d").date() if isinstance(signal_dict["signal_date"], str) else signal_dict["signal_date"]

        existing = db.query(Signal).filter(
            Signal.company_id == company_id,
            Signal.date == sig_date,
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
        logger.error(f"[SIGNALS] Error saving signal to DB: {exc}")
        raise exc
    finally:
        db.close()


def get_signals_from_db(
    status: Optional[str] = None,
    strategy_type: Optional[str] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Fetches signals from DB joined with companies and trades, enriched with active strategy target/SL settings."""
    db = SessionLocal()
    try:
        settings = db.query(StrategySettings).first()
        cfg_target_pct = float(settings.target1_pct) if settings and settings.target1_pct is not None else 20.0
        cfg_sl_pct = abs(float(settings.initial_sl_pct)) if settings and settings.initial_sl_pct is not None else 3.0

        query = (
            db.query(Signal, Company, Trade)
            .join(Company, Signal.company_id == Company.id)
            .outerjoin(Trade, Signal.id == Trade.signal_id)
        )
        if status:
            query = query.filter(Signal.status == status)
        if strategy_type:
            query = query.filter(Signal.strategy_type == strategy_type)

        results = query.order_by(Signal.date.desc(), Signal.created_at.desc()).limit(limit).all()

        output = []
        seen_signal_ids = set()
        for sig, comp, trade in results:
            if sig.id in seen_signal_ids:
                continue
            seen_signal_ids.add(sig.id)
            raw = sig.raw_signal_data or {}

            sig_high = float(raw.get("signal_high") or raw.get("ref_price") or 0.0)
            trade_target_pct = getattr(trade, "target_pct", None) if trade else None
            trade_sl_pct = getattr(trade, "stoploss_pct", None) if trade else None
            trade_target_price = getattr(trade, "target1_price", None) if trade else None
            trade_stop_price = getattr(trade, "stop_price", None) if trade else None

            sig_target_pct = float(trade_target_pct) if trade_target_pct is not None else cfg_target_pct
            sig_sl_pct = abs(float(trade_sl_pct)) if trade_sl_pct is not None else cfg_sl_pct

            calc_target_price = round(sig_high * (1 + sig_target_pct / 100.0), 2) if sig_high > 0 else 0.0
            calc_stop_loss = round(sig_high * (1 - sig_sl_pct / 100.0), 2) if sig_high > 0 else 0.0

            output.append({
                "id": sig.id,
                "company_id": sig.company_id,
                "trading_symbol": comp.trading_symbol,
                "security_id": comp.dhan_security_id,
                "company_name": comp.company_name,
                "strategy_type": sig.strategy_type or "SUPERTREND",
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
                "target_pct": sig_target_pct,
                "sl_pct": sig_sl_pct,
                "target_price": trade_target_price if trade_target_price else calc_target_price,
                "stop_loss": trade_stop_price if trade_stop_price else calc_stop_loss,
                "status": sig.status,
                "rejection_reason": sig.rejection_reason,
                "evaluation": raw.get("evaluation"),
                "executed_price": getattr(trade, "entry_price", None) if trade else None,
                "new_target_pct": trade_target_pct if trade_target_pct is not None else sig_target_pct,
                "new_sl_pct": abs(trade_sl_pct) if trade_sl_pct is not None else sig_sl_pct,
                "created_at": str(sig.created_at)
            })
        return output
    finally:
        db.close()


def scan_signals_from_db(target_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """Scan all active companies in DB for strategy signals for a specific target trading date."""
    from market.calendar import is_trading_day

    today = date.today()
    if target_date is None:
        # Default to latest completed trading day
        target_date = today
        if not is_trading_day(target_date):
            for d_offset in range(1, 10):
                check_d = today - timedelta(days=d_offset)
                if is_trading_day(check_d):
                    target_date = check_d
                    break

    logger.info(f"[SIGNALS] Starting signal scan for target_date={target_date}...")

    # Expire any PENDING signals older than the previous trading day
    min_valid_date = target_date - timedelta(days=5)
    for d_offset in range(1, 10):
        check_d = target_date - timedelta(days=d_offset)
        if is_trading_day(check_d):
            min_valid_date = check_d
            break

    exp_db = SessionLocal()
    try:
        stale = exp_db.query(Signal).filter(Signal.status == "PENDING", Signal.date < min_valid_date).all()
        if stale:
            for s in stale:
                s.status = "EXPIRED"
                s.expiry_date = datetime.utcnow()
                s.expiry_reason = "TARGET_AND_CLOSE_NOT_MET"
            exp_db.commit()
            logger.info(f"[SIGNALS] Expired {len(stale)} stale PENDING signal(s) prior to {min_valid_date}.")
    except Exception as exc:
        logger.warning(f"[SIGNALS] Failed to expire stale signals: {exc}")
        try:
            exp_db.rollback()
        except Exception:
            pass
    finally:
        exp_db.close()

    db = SessionLocal()
    new_signals = []
    try:
        from sqlalchemy import text
        from collections import defaultdict
        from market.weekly import filter_completed_weekly_candles

        # 1. Fetch eligible companies with market cap >= 8,000 Cr
        comp_rows = db.execute(text(
            "SELECT id, trading_symbol, dhan_security_id, market_cap "
            "FROM companies WHERE is_active = true AND dhan_security_id != '' AND market_cap >= 8000"
        )).fetchall()

        comp_map = {
            r[0]: {
                "id": r[0],
                "symbol": r[1],
                "sec_id": r[2],
                "mcap": float(r[3] or 0)
            }
            for r in comp_rows
        }

        if not comp_map:
            return []

        # 2. Single fast SQL join to fetch daily candles (last 730 days for full RMA warm-up)
        cutoff_daily = target_date - timedelta(days=730)
        daily_rows = db.execute(
            text(
                "SELECT d.company_id, d.date, d.open, d.high, d.low, d.close, d.volume "
                "FROM daily_candles d "
                "JOIN companies c ON d.company_id = c.id "
                "WHERE c.is_active = true AND c.market_cap >= 8000 AND d.date >= :cutoff "
                "ORDER BY d.company_id, d.date ASC"
            ),
            {"cutoff": cutoff_daily}
        ).fetchall()

        daily_by_comp = defaultdict(list)
        for r in daily_rows:
            daily_by_comp[r[0]].append({
                "date": r[1],
                "open": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "close": float(r[5]),
                "volume": int(r[6] or 0)
            })

        for cid, comp in comp_map.items():
            daily = daily_by_comp.get(cid, [])
            if len(daily) < 22 or daily[-1]["date"] != target_date:
                continue

            symbol = comp["symbol"]
            sec_id = comp["sec_id"]
            mcap = comp["mcap"]

            # Aggregate completed weekly candles
            weekly_map = {}
            for r in daily:
                mon = r["date"] - timedelta(days=r["date"].weekday())
                if mon not in weekly_map:
                    weekly_map[mon] = {"date": mon, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
                else:
                    weekly_map[mon]["high"] = max(weekly_map[mon]["high"], r["high"])
                    weekly_map[mon]["low"] = min(weekly_map[mon]["low"], r["low"])
                    weekly_map[mon]["close"] = r["close"]
                    weekly_map[mon]["volume"] += r["volume"]
            weekly = sorted(weekly_map.values(), key=lambda x: x["date"])
            completed_weekly = filter_completed_weekly_candles(weekly, current_date=daily[-1]["date"])

            # 1. Evaluate Supertrend Strategy
            if len(completed_weekly) >= 15:
                try:
                    signal = evaluate_stock_signal(
                        symbol=symbol,
                        security_id=sec_id,
                        exchange_segment="NSE_EQ",
                        daily_candles=daily,
                        weekly_candles=completed_weekly,
                        market_cap_cr=mcap,
                        current_date=daily[-1]["date"]
                    )
                    if signal:
                        save_signal_to_db(cid, signal, strategy_type="SUPERTREND")
                        new_signals.append(signal)
                        logger.info(f"[SIGNALS] 🟢 NEW SUPERTREND SIGNAL: {symbol}")
                except Exception as exc:
                    logger.warning(f"[SIGNALS] Error evaluating {symbol}: {exc}")

            # 2. Evaluate Monthly RSI Strategy
            try:
                # Aggregate monthly candles from pre-fetched daily candles in-memory
                monthly_map = {}
                for r in daily:
                    month_key = r["date"].strftime("%Y-%m")
                    if month_key not in monthly_map:
                        monthly_map[month_key] = {"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
                    else:
                        monthly_map[month_key]["high"] = max(monthly_map[month_key]["high"], r["high"])
                        monthly_map[month_key]["low"] = min(monthly_map[month_key]["low"], r["low"])
                        monthly_map[month_key]["close"] = r["close"]
                        monthly_map[month_key]["volume"] += r["volume"]
                monthly_candles = sorted(monthly_map.values(), key=lambda x: x["date"])

                if len(monthly_candles) >= 14:
                    m_signal = evaluate_monthly_rsi_signal(
                        symbol=symbol,
                        security_id=sec_id,
                        exchange_segment="NSE_EQ",
                        daily_candles=daily,
                        monthly_candles=monthly_candles
                    )
                    if m_signal:
                        save_signal_to_db(cid, m_signal, strategy_type="MONTHLY_RSI")
                        new_signals.append(m_signal)
                        logger.info(f"[SIGNALS] 🟢 NEW MONTHLY RSI SIGNAL: {symbol}")
            except Exception as exc:
                logger.warning(f"[SIGNALS] Error evaluating monthly RSI for {symbol}: {exc}")

    finally:
        db.close()

    logger.info(f"[SIGNALS] Scan complete. New signals found: {len(new_signals)}")
    return new_signals
