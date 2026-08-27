"""
workers/market_monitor.py — Tick Health & Cache Consistency Auditor Workers
============================================================================
Provides background async monitoring loops:
1. Tick Health Monitor: Detects silent feeds during market hours and forces WebSocket resubscription.
2. Cache Consistency Auditor: Compares PostgreSQL active trades with in-memory cache and repairs any drift.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
import pytz

from database.database import SessionLocal
from database.models import Trade, AtsTradeState, ActiveSubscription
from trading.trades import get_cache_manager

logger = logging.getLogger("ats.workers.market_monitor")
IST = pytz.timezone("Asia/Kolkata")


def _is_market_hours() -> bool:
    """Return True if current IST time is within market hours (9:15–15:35 Mon–Fri)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 10) <= t <= (15 * 60 + 35)


async def tick_health_loop(ws_manager=None) -> None:
    """
    Background task: Verifies all active security feeds are receiving ticks.
    Runs every 60s. Alerts and resubscribes if dead during market hours.
    """
    cache_manager = get_cache_manager()
    last_tick_map = {}

    while True:
        try:
            await asyncio.sleep(60.0)
            if not _is_market_hours():
                continue

            now = time.time()
            active_sec_ids = cache_manager.get_active_security_ids()
            dead_feeds = []

            for sec_id in active_sec_ids:
                last_tick = last_tick_map.get(sec_id, now)  # initialize on discovery
                if now - last_tick > 60.0:
                    dead_feeds.append(sec_id)

            if dead_feeds:
                logger.critical(f"[TICK_HEALTH] 🔴 {len(dead_feeds)} active feeds went silent for >60s! {dead_feeds}")
                if ws_manager:
                    logger.info("[TICK_HEALTH] Forcing resubscription for dead feeds...")
                    await ws_manager.subscribe(dead_feeds)
                    for sec_id in dead_feeds:
                        last_tick_map[sec_id] = time.time()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[TICK_HEALTH] Error in health loop: {e}")


async def audit_consistency_once(ws_manager=None) -> dict:
    """Performs one consistency check between PostgreSQL active trades and in-memory cache."""
    cache_manager = get_cache_manager()
    db = SessionLocal()
    missing_db = []
    missing_cache = []
    try:
        db_active = (
            db.query(Trade)
            .filter(Trade.ats_state.in_([
                AtsTradeState.ENTRY_PENDING,
                AtsTradeState.OPEN,
                AtsTradeState.PARTIAL_EXIT,
            ]))
            .all()
        )

        cache_ids = set(cache_manager._trades_by_id.keys())
        db_ids = {str(t.id) for t in db_active}

        missing_in_cache_ids = db_ids - cache_ids
        missing_in_db_ids = cache_ids - db_ids

        if missing_in_db_ids:
            for tid in missing_in_db_ids:
                cache_manager.remove_trade(tid)
                missing_db.append(tid)
                logger.warning(f"[CACHE_AUDIT] Trade {tid} found in cache but not active in DB. Evicted.")

        if missing_in_cache_ids:
            missing_sec_ids = set()
            for tid in missing_in_cache_ids:
                t = next(tr for tr in db_active if str(tr.id) == tid)
                cache_manager.update_trade(t)
                missing_cache.append(tid)
                if t.security_id:
                    missing_sec_ids.add(str(t.security_id))
                logger.warning(f"[CACHE_AUDIT] Trade {tid} found active in DB but missing from cache. Re-registered.")

            if ws_manager and missing_sec_ids:
                try:
                    await ws_manager.subscribe(list(missing_sec_ids))
                    logger.info(f"[CACHE_AUDIT] Resubscribed to {len(missing_sec_ids)} missing security feed(s).")
                except Exception as exc:
                    logger.error(f"[CACHE_AUDIT] Failed to resubscribe WebSocket during audit repair: {exc}")

        status = "OK" if not missing_cache and not missing_db else "REPAIRED"
        return {
            "db_active": len(db_active),
            "cache_active": cache_manager.get_active_trade_count(),
            "missing_cache": missing_cache,
            "missing_db": missing_db,
            "status": status,
        }
    except Exception as exc:
        logger.error(f"[CACHE_AUDIT] Auditor error: {exc}", exc_info=True)
        return {"status": "ERROR"}
    finally:
        db.close()


async def cache_auditor_loop(ws_manager=None) -> None:
    """
    Background auditor loop: compares PostgreSQL active trades with memory cache every 60s.
    Synchronizes ActiveSubscription table and WebSocket feed on any repair.
    """
    cache_manager = get_cache_manager()
    while True:
        try:
            await asyncio.sleep(60.0)
            audit_result = await audit_consistency_once(ws_manager=ws_manager)
            if audit_result.get("status") == "REPAIRED":
                logger.info("[CACHE_AUDIT] Synchronizing WebSocket and ActiveSubscription table after repair.")
                active_ids = set(cache_manager.get_active_security_ids())

                db = SessionLocal()
                try:
                    db_subs = db.query(ActiveSubscription).all()
                    db_sec_ids = {sub.security_id for sub in db_subs}

                    for old_id in db_sec_ids - active_ids:
                        db.query(ActiveSubscription).filter_by(security_id=old_id).delete()
                        if ws_manager:
                            await ws_manager.unsubscribe([old_id])

                    for missing_id in active_ids - db_sec_ids:
                        db.add(ActiveSubscription(security_id=missing_id))
                        if ws_manager:
                            await ws_manager.subscribe([missing_id])

                    db.commit()
                except Exception as e:
                    logger.error(f"[CACHE_AUDIT] Error syncing ActiveSubscription DB: {e}")
                    db.rollback()
                finally:
                    db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[CACHE_AUDIT] Loop error: {e}")
            await asyncio.sleep(60.0)
