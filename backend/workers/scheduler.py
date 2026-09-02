"""
workers/scheduler.py — APScheduler Background Cron Jobs
========================================================
Schedules automated trading operations:
- 09:00 IST: Pre-market token refresh (TOTP) & Dhan Scrip Master security ID sync
- 15:20 IST: Fast pre-execution candle sync for actionable securities
- 15:25 IST: 3:25 PM entry condition evaluation & Supertrend RED exit check
- 15:46 IST: Post-market candle sync (3:46 PM)
- 17:00 IST: Post-market signal scan (5:00 PM)
- 22:00 IST: Full daily candle sync
- Dec 31 23:50 IST: Annual Indian holiday calendar update
"""

from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from database.database import SessionLocal
from database.models import Company, Signal, Trade, AtsTradeState
from market.calendar import is_trading_day, fetch_and_store_holidays
from market.candles import sync_actionable_companies, sync_all_active_companies, get_daily_candles_from_db
from dhan.auth import refresh_all_broker_tokens
from dhan.market import sync_dhan_scrip_master
from dhan.client import get_dhan_data_client
from dhan.portfolio import PortfolioService
from trading.risk import get_strategy_settings
from trading.strategies import calculate_supertrend
from trading.signals import calculate_reference_price, scan_signals_from_db
from trading.orders import get_order_executor
from trading.trade_manager import get_trade_engine

logger = logging.getLogger("ats.workers.scheduler")
IST = pytz.timezone("Asia/Kolkata")

scheduler = BackgroundScheduler()
_portfolio_service = PortfolioService()


def evaluate_and_execute_325_entries() -> dict:
    """Evaluates PENDING signals at 3:25 PM IST using live Dhan OHLC/LTP."""
    today = date.today()
    db = SessionLocal()
    try:
        all_pending = (
            db.query(Signal, Company)
            .join(Company, Signal.company_id == Company.id)
            .filter(Signal.status == "PENDING")
            .all()
        )

        # Allow signals from today or previous trading day (e.g. yesterday)
        min_valid_date = today - timedelta(days=5)
        for d_offset in range(1, 10):
            check_d = today - timedelta(days=d_offset)
            if is_trading_day(check_d):
                min_valid_date = check_d
                break

        pending_signals = []
        for sig, comp in all_pending:
            if sig.date < min_valid_date:
                sig.status = "EXPIRED"
                sig.expiry_date = datetime.utcnow()
                sig.expiry_reason = "TARGET_AND_CLOSE_NOT_MET"
                db.commit()
                logger.info(f"[SCHEDULER 3:25] Expired stale signal {sig.id[:8]} for {comp.trading_symbol} (Date: {sig.date})")
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

        data_client = get_dhan_data_client()
        ohlc_feed = data_client.get_marketfeed_ohlc(list(sec_map.keys()))

        executed_count = 0
        rejected_count = 0

        for sec_id_int, (sig, comp) in sec_map.items():
            feed_item = ohlc_feed.get(str(sec_id_int)) or ohlc_feed.get(sec_id_int)
            if not feed_item or not isinstance(feed_item, dict):
                logger.warning(f"[SCHEDULER 3:25] No live OHLC feed for {comp.trading_symbol}")
                continue

            ohlc_dict = feed_item.get("ohlc", {}) if isinstance(feed_item.get("ohlc"), dict) else {}
            today_open = float(ohlc_dict.get("open") or feed_item.get("open") or 0.0)
            today_high = float(ohlc_dict.get("high") or feed_item.get("high") or 0.0)
            today_low = float(ohlc_dict.get("low") or feed_item.get("low") or 0.0)
            ltp = float(feed_item.get("last_price") or ohlc_dict.get("close") or feed_item.get("close") or 0.0)

            settings = get_strategy_settings(strategy_type=sig.strategy_type)
            raw = sig.raw_signal_data or {}
            signal_high = float(raw.get("signal_high", 0.0))
            signal_low = float(raw.get("signal_low", 0.0))

            if sig.strategy_type == "MONTHLY_RSI":
                max_entry_gap_pct = settings.get("max_entry_gap_pct", 5.0)
                signal_close = float(raw.get("signal_close", 0.0))

                if signal_close <= 0 or today_open <= 0 or ltp <= 0:
                    continue

                entry_gap_pct = ((today_open - signal_close) / signal_close) * 100
                if entry_gap_pct > max_entry_gap_pct:
                    sig.status = "REJECTED"
                    sig.rejection_reason = (
                        f"ENTRY_GAP_TOO_LARGE: Gap {entry_gap_pct:.2f}% > Max Allowed {max_entry_gap_pct:.1f}% "
                        f"(Open: Rs {today_open:.2f}, Signal Close: Rs {signal_close:.2f})"
                    )
                    sig.rejection_date = datetime.utcnow()
                    raw["evaluation"] = {
                        "evaluated_at": datetime.utcnow().isoformat(),
                        "today_open": today_open,
                        "signal_close": signal_close,
                        "entry_gap_pct": round(entry_gap_pct, 2),
                        "max_entry_gap_pct": max_entry_gap_pct,
                        "today_ltp": ltp,
                        "passed": False,
                    }
                    sig.raw_signal_data = raw
                    db.commit()
                    rejected_count += 1
                    logger.info(f"[SCHEDULER 3:25] Signal REJECTED for {comp.trading_symbol}: {sig.rejection_reason}")
                    continue
                    
                cond_met = True
                raw["evaluation"] = {
                    "evaluated_at": datetime.utcnow().isoformat(),
                    "today_open": today_open,
                    "signal_close": signal_close,
                    "entry_gap_pct": round(entry_gap_pct, 2),
                    "today_ltp": ltp,
                    "passed": True,
                }
            else:
                if signal_high <= 0 or signal_low <= 0 or ltp <= 0:
                    continue

                ref_price, is_gap_up = calculate_reference_price(signal_high, today_open)
                raw["ref_price"] = ref_price
                raw["is_gap_up"] = is_gap_up

                rejection_reason = None
                if today_low > 0 and today_low <= signal_low:
                    rejection_reason = (
                        f"SIGNAL_LOW_BROKEN: Today Low Rs {today_low:.2f} <= Signal Low Rs {signal_low:.2f}"
                    )
                elif today_low > 0 and today_low <= (ref_price * 0.95):
                    rejection_reason = (
                        f"DRAWDOWN_5PCT: Today Low Rs {today_low:.2f} <= 5% Drawdown Limit Rs {ref_price * 0.95:.2f} (Ref: Rs {ref_price:.2f})"
                    )

                if rejection_reason:
                    sig.status = "REJECTED"
                    sig.rejection_reason = rejection_reason
                    sig.rejection_date = datetime.utcnow()
                    raw["evaluation"] = {
                        "evaluated_at": datetime.utcnow().isoformat(),
                        "today_open": today_open,
                        "today_high": today_high,
                        "today_low": today_low,
                        "today_ltp": ltp,
                        "signal_high": signal_high,
                        "signal_low": signal_low,
                        "ref_price": ref_price,
                        "passed": False,
                        "failure_stage": "LOW_OR_DRAWDOWN_BREACHED"
                    }
                    sig.raw_signal_data = raw
                    db.commit()
                    rejected_count += 1
                    logger.info(f"[SCHEDULER 3:25] Signal REJECTED for {comp.trading_symbol}: {rejection_reason}")
                    continue

                breakout_multiplier = 1.0 + (settings.get("entry_high_breakout_pct", 3.0) / 100.0)
                req_high = round(ref_price * breakout_multiplier, 2)
                cond_high_breakout = (today_high >= req_high)
                cond_ltp_above_high = (ltp > signal_high)
                cond_met = cond_high_breakout and cond_ltp_above_high

                if not cond_met:
                    fail_details = []
                    if not cond_high_breakout:
                        fail_details.append(f"High Rs {today_high:.2f} < Req +3% Rs {req_high:.2f} (Ref: Rs {ref_price:.2f})")
                    if not cond_ltp_above_high:
                        fail_details.append(f"LTP Rs {ltp:.2f} <= Signal High Rs {signal_high:.2f}")

                    sig.status = "REJECTED"
                    sig.rejection_reason = "CONDITIONS_NOT_MET: " + " | ".join(fail_details)
                    sig.rejection_date = datetime.utcnow()
                    raw["evaluation"] = {
                        "evaluated_at": datetime.utcnow().isoformat(),
                        "today_open": today_open,
                        "today_high": today_high,
                        "today_low": today_low,
                        "today_ltp": ltp,
                        "signal_high": signal_high,
                        "signal_low": signal_low,
                        "ref_price": ref_price,
                        "req_high": req_high,
                        "high_breakout_met": cond_high_breakout,
                        "ltp_above_high_met": cond_ltp_above_high,
                        "passed": False
                    }
                    sig.raw_signal_data = dict(raw)
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(sig, "raw_signal_data")
                    db.commit()
                    rejected_count += 1
                    logger.info(f"[SCHEDULER 3:25] Signal REJECTED for {comp.trading_symbol}: {sig.rejection_reason}")
                    continue

            if cond_met:
                logger.info(f"[SCHEDULER 3:25] 🟢 ENTRY TRIGGERED for {comp.trading_symbol} ({sig.strategy_type})")
                try:
                    portfolio = _portfolio_service.get_fund_limits()
                    avail_balance = float(portfolio.get("availabelBalance") or portfolio.get("available_balance") or 0.0)
                    capital_pct = settings.get("capital_allocation_pct", 20.0) / 100.0
                    allocated_margin = round(avail_balance * capital_pct, 2)
                    if allocated_margin <= 0 and avail_balance > 0:
                        allocated_margin = avail_balance

                    # Compute MTF leverage multiplier
                    leverage = 1.0
                    if comp.is_mtf and comp.mtf_leverage:
                        try:
                            leverage = float(str(comp.mtf_leverage).replace("x", "").strip())
                        except Exception:
                            leverage = 3.0

                    total_purchasing_power = round(allocated_margin * leverage, 2)
                    if ltp <= 0 or total_purchasing_power < ltp:
                        sig.status = "REJECTED"
                        sig.rejection_reason = (
                            f"INSUFFICIENT_FUNDS: Purchasing power Rs {total_purchasing_power:.2f} "
                            f"(Margin Rs {allocated_margin:.2f} @ {leverage:.2f}x) < 1 Share LTP Rs {ltp:.2f}"
                        )
                        sig.rejection_date = datetime.utcnow()
                        raw["evaluation"] = {
                            "evaluated_at": datetime.utcnow().isoformat(),
                            "today_open": today_open,
                            "today_high": today_high,
                            "today_low": today_low,
                            "today_ltp": ltp,
                            "signal_high": signal_high,
                            "ref_price": ref_price if sig.strategy_type != "MONTHLY_RSI" else None,
                            "req_high": req_high if sig.strategy_type != "MONTHLY_RSI" else None,
                            "allocated_margin": allocated_margin,
                            "leverage": leverage,
                            "total_purchasing_power": total_purchasing_power,
                            "passed": False,
                            "failure_stage": "INSUFFICIENT_FUNDS"
                        }
                        sig.raw_signal_data = dict(raw)
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(sig, "raw_signal_data")
                        db.commit()
                        rejected_count += 1
                        logger.warning(f"[SCHEDULER 3:25] Signal REJECTED for {comp.trading_symbol}: {sig.rejection_reason}")
                        continue

                    quantity = int(total_purchasing_power // ltp)

                    # Save full passed evaluation details into raw_signal_data
                    raw["evaluation"] = {
                        "evaluated_at": datetime.utcnow().isoformat(),
                        "today_open": today_open,
                        "today_high": today_high,
                        "today_low": today_low,
                        "today_ltp": ltp,
                        "signal_high": signal_high,
                        "signal_low": signal_low,
                        "ref_price": ref_price if sig.strategy_type != "MONTHLY_RSI" else None,
                        "req_high": req_high if sig.strategy_type != "MONTHLY_RSI" else None,
                        "high_breakout_met": True,
                        "ltp_above_high_met": True,
                        "allocated_margin": allocated_margin,
                        "leverage": leverage,
                        "total_purchasing_power": total_purchasing_power,
                        "quantity": quantity,
                        "passed": True
                    }
                    sig.raw_signal_data = dict(raw)
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(sig, "raw_signal_data")
                    db.commit()

                    executor = get_order_executor()
                    order_res = executor.place_entry_order(
                        security_id=comp.dhan_security_id,
                        trading_symbol=comp.trading_symbol,
                        company_id=comp.id,
                        signal_id=sig.id,
                        quantity=quantity,
                        allocated_capital=allocated_margin,
                        exchange_segment="NSE_EQ",
                        strategy_type=sig.strategy_type,
                    )

                    if order_res.get("status") in ("placed", "executed"):
                        executed_count += 1
                        sig.status = "EXECUTED"
                        sig.rejection_reason = None
                        sig.execution_date = datetime.utcnow()
                        raw["evaluation"]["broker_order_status"] = order_res.get("status")
                        raw["evaluation"]["broker_order_id"] = order_res.get("order_id")
                        sig.raw_signal_data = dict(raw)
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(sig, "raw_signal_data")
                        db.commit()
                    else:
                        err_msg = order_res.get("error") or order_res.get("message") or "Broker Rejected Order"
                        sig.status = "EXECUTED"
                        sig.rejection_reason = f"ORDER_FAILED: {err_msg}"
                        sig.execution_date = datetime.utcnow()
                        raw["evaluation"]["broker_order_status"] = "FAILED"
                        raw["evaluation"]["broker_error"] = err_msg
                        sig.raw_signal_data = dict(raw)
                        from sqlalchemy.orm.attributes import flag_modified
                        flag_modified(sig, "raw_signal_data")
                        db.commit()
                        executed_count += 1
                        logger.warning(f"[SCHEDULER 3:25] Signal PASSED & EXECUTED, but Broker Order failed for {comp.trading_symbol}: {err_msg}")

                except Exception as exc:
                    logger.error(f"[SCHEDULER 3:25] Failed to execute entry for {comp.trading_symbol}: {exc}", exc_info=True)
                    sig.status = "REJECTED"
                    sig.rejection_reason = f"EXECUTION_FAILED: {str(exc)}"
                    sig.rejection_date = datetime.utcnow()
                    db.commit()

        return {
            "status": "completed",
            "evaluated": len(sec_map),
            "executed": executed_count,
            "rejected": rejected_count,
        }
    finally:
        db.close()


def evaluate_and_execute_325_exits() -> dict:
    """Evaluates OPEN and PARTIAL_EXIT trades at 3:25 PM IST against Daily Supertrend."""
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
                    sec_map[int(company.dhan_security_id)] = (trade, company)
                except ValueError:
                    continue

        if not sec_map:
            return {"status": "no_valid_security_ids", "exited": 0}

        data_client = get_dhan_data_client()
        ohlc_feed = data_client.get_marketfeed_ohlc(list(sec_map.keys()))
        exited_count = 0

        for sec_id_int, (trade, company) in sec_map.items():
            feed_item = ohlc_feed.get(str(sec_id_int)) or ohlc_feed.get(sec_id_int)
            if not feed_item or not isinstance(feed_item, dict):
                continue

            live_close = float(feed_item.get("last_price") or feed_item.get("close") or 0.0)
            live_high = float(feed_item.get("high") or live_close)
            live_low = float(feed_item.get("low") or live_close)

            if live_close <= 0.0:
                continue

            settings = get_strategy_settings(strategy_type=trade.strategy_type)

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

                st_dirs = calculate_supertrend(
                    combined_candles,
                    period=settings["supertrend_period"],
                    multiplier=settings["supertrend_multiplier"]
                )
                if not st_dirs:
                    continue

                latest_st = st_dirs[-1]
                logger.info(
                    f"[SCHEDULER 3:25 EXIT] {company.trading_symbol}: Close={live_close}, "
                    f"Supertrend={'GREEN (+1)' if latest_st == 1 else 'RED (-1)'}"
                )

                if latest_st == -1 and trade_engine:
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(trade_engine.trigger_supertrend_exit(trade, live_close))
                    except RuntimeError:
                        asyncio.run(trade_engine.trigger_supertrend_exit(trade, live_close))
                    exited_count += 1

            except Exception as exc:
                logger.error(f"[SCHEDULER 3:25 EXIT] Error for sec_id={sec_id_int}: {exc}")

        return {
            "status": "completed",
            "evaluated": len(sec_map),
            "exited": exited_count,
        }
    finally:
        db.close()


def scheduled_candle_sync():
    """Checks if today is a trading day before running fast candle sync at 3:20 PM."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping candle sync because {now.date()} is not a trading day.")
        return
        
    logger.info("[SCHEDULER] Triggering scheduled fast actionable candle sync...")
    try:
        result = sync_actionable_companies()
        logger.info(f"[SCHEDULER] Scheduled fast sync complete: {result}")
    except Exception as exc:
        logger.error(f"[SCHEDULER] Scheduled fast sync failed: {exc}")


def scheduled_325_execution():
    """Runs 3:25 PM entry evaluations and Supertrend RED exit evaluations."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping 3:25 PM execution because {now.date()} is not a trading day.")
        return

    logger.info("[SCHEDULER] Triggering 3:25 PM Entry & Supertrend RED Exit Evaluation...")
    try:
        entry_res = evaluate_and_execute_325_entries()
        exit_res = evaluate_and_execute_325_exits()
        logger.info(f"[SCHEDULER] 3:25 PM Execution completed: Entries={entry_res}, Exits={exit_res}")
    except Exception as exc:
        logger.error(f"[SCHEDULER] 3:25 PM Execution failed: {exc}")


_last_candle_sync_status = {
    "date": None,
    "success": False
}


def scheduled_post_market_candle_sync():
    """Runs post-market full candle sync at 3:46 PM."""
    global _last_candle_sync_status
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping post-market candle sync because {now.date()} is not a trading day.")
        return

    logger.info("[SCHEDULER] Triggering post-market candle sync at 3:46 PM...")
    try:
        sync_all_active_companies(limit=4000)
        _last_candle_sync_status = {"date": now.date(), "success": True}
        logger.info("[SCHEDULER] Post-market candle sync completed successfully.")
    except Exception as exc:
        _last_candle_sync_status = {"date": now.date(), "success": False}
        logger.error(f"[SCHEDULER] Post-market candle sync failed: {exc}")


def scheduled_post_market_signal_scan():
    """Runs post-market signal scanning at 7:00 PM (syncing fresh daily candles first)."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping signal scan because {now.date()} is not a trading day.")
        return

    logger.info("[SCHEDULER] Triggering post-market candle sync and signal scan at 7:00 PM...")
    try:
        # 1. Sync fresh daily candles from Dhan for eligible universe
        sync_all_active_companies(limit=1000)
        # 2. Run signal scan for today's market close
        new_signals = scan_signals_from_db(target_date=now.date())
        logger.info(f"[SCHEDULER] Post-market signal scan completed: {len(new_signals)} new signals found.")
    except Exception as exc:
        logger.error(f"[SCHEDULER] Post-market signal scan failed: {exc}")


def scheduled_full_candle_sync():
    """Runs fallback full candle sync at 10:00 PM ONLY if 3:46 PM sync failed or did not run."""
    global _last_candle_sync_status
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping full candle sync because {now.date()} is not a trading day.")
        return

    # Check if 3:46 PM post-market candle sync already succeeded for today
    if _last_candle_sync_status.get("date") == now.date() and _last_candle_sync_status.get("success") is True:
        logger.info(f"[SCHEDULER 10:00 PM] Skipping 10:00 PM full candle sync because 3:46 PM sync already completed successfully for {now.date()}.")
        return

    logger.warning(f"[SCHEDULER 10:00 PM] 3:46 PM sync failed or did not run for {now.date()}. Running fallback 10:00 PM candle sync...")
    try:
        result = sync_all_active_companies(limit=4000)
        _last_candle_sync_status = {"date": now.date(), "success": True}
        logger.info(f"[SCHEDULER 10:00 PM] Fallback full candle sync completed: {result}")
    except Exception as exc:
        logger.error(f"[SCHEDULER 10:00 PM] Fallback full candle sync failed: {exc}")


def scheduled_pre_market_auth_and_scrip_sync():
    """Runs at 9:00 AM on trading days to generate fresh broker access tokens, reloads all system clients & WebSocket connections, and updates company security IDs."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER 9:00 AM] Skipping pre-market sync because {now.date()} is not a trading day.")
        return

    logger.info("[SCHEDULER 9:00 AM] Triggering 9:00 AM Pre-Market Token Refresh, System Reload & Scrip Master Sync...")
    try:
        # 1. Refresh all broker tokens via TOTP
        token_res = refresh_all_broker_tokens()
        logger.info(f"[SCHEDULER 9:00 AM] Broker Token Refresh Results: {token_res}")

        # 2. Reset Dhan client singletons so whole platform immediately uses new token
        from dhan.client import reset_dhan_clients
        from dhan.websocket import get_market_feed_manager
        import asyncio

        reset_dhan_clients()

        # 3. Restart WebSocket feed with new token
        ws_mgr = get_market_feed_manager()
        if ws_mgr and ws_mgr._running:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(ws_mgr.restart())
                else:
                    loop.run_until_complete(ws_mgr.restart())
            except Exception:
                pass

        logger.info("[SCHEDULER 9:00 AM] System clients and WebSocket feed reloaded with fresh token.")

        # 4. Sync Dhan Scrip Master
        scrip_updated = sync_dhan_scrip_master()
        logger.info(f"[SCHEDULER 9:00 AM] Scrip Master Sync Completed: {scrip_updated} IDs updated.")
    except Exception as exc:
        logger.error(f"[SCHEDULER 9:00 AM] Pre-market sync failed: {exc}")


def scheduled_holiday_update():
    """Runs on Dec 31 to fetch holidays for the next year."""
    next_year = datetime.now().year + 1
    logger.info(f"[SCHEDULER] Triggering holiday update for year {next_year}...")
    fetch_and_store_holidays(next_year)


def start_scheduler():
    """Configure and start the APScheduler background instance."""
    if scheduler.running:
        return
        
    scheduler.add_job(
        scheduled_pre_market_auth_and_scrip_sync,
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=IST),
        id="pre_market_sync_0900",
        name="Pre-Market Token Refresh & Scrip Master Sync at 9:00 AM",
        replace_existing=True
    )

    scheduler.add_job(
        scheduled_candle_sync,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=20, timezone=IST),
        id="sync_candles_1520",
        name="Pre-3:25 PM Candle Sync at 3:20 PM",
        replace_existing=True
    )

    scheduler.add_job(
        scheduled_325_execution,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=25, timezone=IST),
        id="execute_325_window",
        name="3:25 PM Entry & Supertrend Exit Window",
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_post_market_candle_sync,
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=46, timezone=IST),
        id="sync_candles_1546",
        name="Post-Market Candle Sync at 3:46 PM",
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_post_market_signal_scan,
        trigger=CronTrigger(day_of_week="mon-fri", hour=19, minute=0, timezone=IST),
        id="scan_signals_1700",
        name="Post-Market Signal Scan at 7:00 PM",
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_full_candle_sync,
        trigger=CronTrigger(day_of_week="mon-fri", hour=22, minute=0, timezone=IST),
        id="sync_candles_2200",
        name="Fallback Candle Sync at 10:00 PM (Only if 3:46 PM failed)",
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_holiday_update,
        trigger=CronTrigger(month=12, day=31, hour=23, minute=50, timezone=IST),
        id="update_market_holidays",
        name="Annual Holiday Update",
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("[SCHEDULER] Background scheduler started successfully.")


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Background scheduler stopped.")
