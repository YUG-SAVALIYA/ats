"""
app.workers.scheduler
=====================
Production background cron scheduler for candle synchronization,
3:25 PM execution window, 3:40 PM candle sync, 4:00 PM signal scanning, and holiday updates.
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from app.data.candles import sync_all_active_companies, sync_actionable_companies
from app.data.calendar import is_trading_day
from app.data.holidays import fetch_and_store_holidays
import app.data.calendar as mc
from app.data.locks import (
    advisory_lock_guard,
    LOCK_JOB_320_FAST_SYNC,
    LOCK_JOB_325_EXECUTION,
    LOCK_JOB_340_POST_SYNC,
    LOCK_JOB_400_SIGNAL_SCAN,
    LOCK_JOB_2200_FULL_SYNC,
)

logger = logging.getLogger("ats.scheduler")

scheduler = BackgroundScheduler()


def scheduled_candle_sync():
    """Checks if today is a trading day before running fast candle sync at 3:20 PM."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping candle sync because {now.date()} is not a trading day.")
        return

    with advisory_lock_guard(LOCK_JOB_320_FAST_SYNC, "3:20 PM Fast Candle Sync") as acquired:
        if not acquired:
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

    with advisory_lock_guard(LOCK_JOB_325_EXECUTION, "3:25 PM Entry & Supertrend Exit") as acquired:
        if not acquired:
            return
        logger.info("[SCHEDULER] Triggering 3:25 PM Entry & Supertrend RED Exit Evaluation...")
        try:
            from app.trading.strategy import get_strategy_engine
            engine = get_strategy_engine()
            entry_res = engine.evaluate_and_execute_325_entries()
            exit_res = engine.evaluate_and_execute_325_exits()
            logger.info(f"[SCHEDULER] 3:25 PM Execution completed: Entries={entry_res}, Exits={exit_res}")
        except Exception as exc:
            logger.error(f"[SCHEDULER] 3:25 PM Execution failed: {exc}")


def scheduled_post_market_candle_sync():
    """Runs post-market full candle sync."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping post-market candle sync because {now.date()} is not a trading day.")
        return

    with advisory_lock_guard(LOCK_JOB_340_POST_SYNC, "3:40 PM Post-Market Candle Sync") as acquired:
        if not acquired:
            return
        logger.info("[SCHEDULER] Triggering post-market candle sync...")
        try:
            sync_all_active_companies(limit=4000)
            logger.info("[SCHEDULER] Post-market candle sync completed.")
        except Exception as exc:
            logger.error(f"[SCHEDULER] Post-market candle sync failed: {exc}")


def scheduled_post_market_signal_scan():
    """Runs post-market signal scanning."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping signal scan because {now.date()} is not a trading day.")
        return

    with advisory_lock_guard(LOCK_JOB_400_SIGNAL_SCAN, "4:00 PM Post-Market Signal Scan") as acquired:
        if not acquired:
            return
        logger.info("[SCHEDULER] Triggering post-market signal scan...")
        try:
            from app.trading.strategy import get_strategy_engine
            engine = get_strategy_engine()
            new_signals = engine.scan_signals_from_db()
            logger.info(f"[SCHEDULER] Post-market signal scan completed: {len(new_signals)} new signals found.")
        except Exception as exc:
            logger.error(f"[SCHEDULER] Post-market signal scan failed: {exc}")


def scheduled_holiday_update():
    """Runs on Dec 31 to fetch holidays for the next year."""
    next_year = datetime.now().year + 1
    logger.info(f"[SCHEDULER] Triggering holiday update for year {next_year}...")
    fetch_and_store_holidays(next_year)
    mc._cache_populated = False


def scheduled_full_candle_sync():
    """Checks if today is a trading day before running full candle sync at 10:00 PM."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping full candle sync because {now.date()} is not a trading day.")
        return

    with advisory_lock_guard(LOCK_JOB_2200_FULL_SYNC, "10:00 PM Full Candle Sync") as acquired:
        if not acquired:
            return
        logger.info("[SCHEDULER] Triggering scheduled full candle sync...")
        try:
            result = sync_all_active_companies(limit=4000)
            logger.info(f"[SCHEDULER] Scheduled full sync complete: {result}")
        except Exception as exc:
            logger.error(f"[SCHEDULER] Scheduled full sync failed: {exc}")


def start_scheduler():
    """Configure and start the APScheduler background instance."""
    if scheduler.running:
        return
        
    scheduler.add_job(
        scheduled_candle_sync,
        trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=20),
        id='sync_candles_1520',
        name='Pre-3:25 PM Candle Sync at 3:20 PM',
        replace_existing=True
    )

    scheduler.add_job(
        scheduled_325_execution,
        trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=25),
        id='execute_325_window',
        name='3:25 PM Entry & Supertrend Exit Window',
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_post_market_candle_sync,
        trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=40),
        id='sync_candles_1540',
        name='Post-Market Candle Sync at 3:40 PM',
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_post_market_signal_scan,
        trigger=CronTrigger(day_of_week='mon-fri', hour=16, minute=0),
        id='scan_signals_1600',
        name='Post-Market Signal Scan at 4:00 PM',
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_full_candle_sync,
        trigger=CronTrigger(day_of_week='mon-fri', hour=22, minute=0),
        id='sync_candles_2200',
        name='Candle Sync at 10:00 PM',
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_holiday_update,
        trigger=CronTrigger(month=12, day=31, hour=23, minute=50),
        id='update_market_holidays',
        name='Annual Holiday Update',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("[SCHEDULER] Background scheduler started successfully with 3:25 PM entry/exit & 3:40 PM scan jobs.")


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Background scheduler stopped.")
