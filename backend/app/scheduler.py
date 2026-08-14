import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from app.services.candle_sync import sync_all_active_companies
from app.services.market_calendar import is_trading_day
from app.services.holiday_manager import fetch_and_store_holidays

logger = logging.getLogger("ats.scheduler")

scheduler = BackgroundScheduler()


def scheduled_candle_sync():
    """Checks if today is a trading day before running candle sync."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping candle sync because {now.date()} is not a trading day.")
        return
        
    logger.info("[SCHEDULER] Triggering scheduled candle sync...")
    try:
        result = sync_all_active_companies(limit=4000)
        logger.info(f"[SCHEDULER] Scheduled sync complete: {result}")
    except Exception as exc:
        logger.error(f"[SCHEDULER] Scheduled sync failed: {exc}")


def scheduled_325_execution():
    """Runs 3:25 PM entry evaluations and Supertrend RED exit evaluations."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping 3:25 PM execution because {now.date()} is not a trading day.")
        return

    logger.info("[SCHEDULER] Triggering 3:25 PM Entry & Supertrend RED Exit Evaluation...")
    try:
        from app.services.strategy import get_strategy_engine
        engine = get_strategy_engine()
        entry_res = engine.evaluate_and_execute_325_entries()
        exit_res = engine.evaluate_and_execute_325_exits()
        logger.info(f"[SCHEDULER] 3:25 PM Execution completed: Entries={entry_res}, Exits={exit_res}")
    except Exception as exc:
        logger.error(f"[SCHEDULER] 3:25 PM Execution failed: {exc}")


def scheduled_post_market_scan():
    """Runs post-market candle sync & signal scanning."""
    now = datetime.now()
    if not is_trading_day(now.date()):
        logger.info(f"[SCHEDULER] Skipping signal scan because {now.date()} is not a trading day.")
        return

    logger.info("[SCHEDULER] Triggering post-market signal scan...")
    try:
        from app.services.strategy import get_strategy_engine
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
    import app.services.market_calendar as mc
    mc._cache_populated = False


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
        scheduled_post_market_scan,
        trigger=CronTrigger(day_of_week='mon-fri', hour=15, minute=40),
        id='scan_signals_1540',
        name='Post-Market Signal Scan at 3:40 PM',
        replace_existing=True
    )
    
    scheduler.add_job(
        scheduled_candle_sync,
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
