"""
app.trading.cache
=================
Dedicated Production Trade Cache Manager Service for ATS Trade Engine.
Dual-indexed by trade_id and security_id.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Set, Any
from sqlalchemy.orm import make_transient

from app.data.database import SessionLocal
from app.data.models import Trade, AtsTradeState

logger = logging.getLogger("ats.trading_cache")

ACTIVE_STATES = (
    AtsTradeState.ENTRY_PENDING,
    AtsTradeState.OPEN,
    AtsTradeState.PARTIAL_EXIT,
    "ENTRY_PENDING",
    "OPEN",
    "PARTIAL_EXIT",
)


class TradeCacheManager:
    """
    In-memory cache manager for active ATS trades.
    Dual-indexed by trade_id and security_id.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._trades_by_id: Dict[str, Trade] = {}
        # security_id -> dhan_account_id -> list[Trade]
        self._trades_by_security: Dict[str, Dict[str, List[Trade]]] = {}

    def build_cache_from_db(self, db_session=None) -> int:
        """
        Loads all active trades (ENTRY_PENDING, OPEN, PARTIAL_EXIT) from PostgreSQL.
        Rebuilds the in-memory cache completely.
        """
        close_session = False
        if db_session is None:
            db_session = SessionLocal()
            close_session = True

        with self._lock:
            try:
                active_trades = (
                    db_session.query(Trade)
                    .filter(Trade.ats_state.in_([
                        AtsTradeState.ENTRY_PENDING,
                        AtsTradeState.OPEN,
                        AtsTradeState.PARTIAL_EXIT,
                    ]))
                    .all()
                )

                self._trades_by_id.clear()
                self._trades_by_security.clear()

                for t in active_trades:
                    if t.partial_exit_completed and (t.sl_stage or 0) < 3:
                        t.sl_stage = 3
                        db_session.commit()

                    self._insert_into_index(t)

                sec_count = len(self._trades_by_security)
                trade_count = len(self._trades_by_id)

                logger.info(
                    f"[CACHE] Rebuilt active trade cache from DB: {trade_count} active trade(s) "
                    f"loaded across {sec_count} unique security ID(s)."
                )
                return trade_count

            except Exception as exc:
                logger.error(f"[CACHE] Failed to build cache from DB: {exc}", exc_info=True)
                return 0
            finally:
                if close_session:
                    db_session.close()

    def _insert_into_index(self, trade: Trade) -> None:
        """Internal helper to insert/update a trade into memory indices."""
        tid = str(trade.id)
        sec_id = str(trade.security_id or "")
        account_id = str(trade.dhan_account_id or "")

        self._trades_by_id[tid] = trade

        if sec_id:
            if sec_id not in self._trades_by_security:
                self._trades_by_security[sec_id] = {}
            if account_id not in self._trades_by_security[sec_id]:
                self._trades_by_security[sec_id][account_id] = []
            
            self._trades_by_security[sec_id][account_id] = [
                t for t in self._trades_by_security[sec_id][account_id] if str(t.id) != tid
            ]
            self._trades_by_security[sec_id][account_id].append(trade)

    def add_trade(self, trade: Trade) -> None:
        """Add a freshly opened or pending trade to cache."""
        state_str = str(trade.ats_state.value if hasattr(trade.ats_state, "value") else trade.ats_state)
        if state_str not in ACTIVE_STATES:
            logger.warning(f"[CACHE] Refusing to add non-active trade {trade.id} (state={state_str}) to cache.")
            return

        with self._lock:
            self._insert_into_index(trade)
            logger.info(
                f"[CACHE] Added trade {trade.id} to memory cache "
                f"(sec={trade.security_id}, state={trade.ats_state}, SL={trade.stop_price})."
            )

    def update_trade(self, trade: Trade) -> None:
        """Update an existing cached trade's state / attributes."""
        state_str = str(trade.ats_state.value if hasattr(trade.ats_state, "value") else trade.ats_state)

        with self._lock:
            if state_str not in ACTIVE_STATES:
                self.remove_trade(str(trade.id))
            else:
                self._insert_into_index(trade)

    def remove_trade(self, trade_id: str) -> Optional[Trade]:
        """Remove a trade from memory cache (e.g. when CLOSED, CANCELLED, or FAILED)."""
        tid = str(trade_id)
        with self._lock:
            trade = self._trades_by_id.pop(tid, None)
            if trade:
                sec_id = str(trade.security_id or "")
                account_id = str(trade.dhan_account_id or "")
                if sec_id in self._trades_by_security:
                    if account_id in self._trades_by_security[sec_id]:
                        self._trades_by_security[sec_id][account_id] = [
                            t for t in self._trades_by_security[sec_id][account_id] if str(t.id) != tid
                        ]
                        if not self._trades_by_security[sec_id][account_id]:
                            self._trades_by_security[sec_id].pop(account_id, None)
                    if not self._trades_by_security[sec_id]:
                        self._trades_by_security.pop(sec_id, None)
                logger.info(f"[CACHE] Removed trade {tid} (sec={sec_id}, acc={account_id}) from memory cache.")
            return trade

    def get_trade(self, trade_id: str) -> Optional[Trade]:
        """Get single trade by ID from memory cache."""
        with self._lock:
            return self._trades_by_id.get(str(trade_id))

    def get_trades_for_security(self, security_id: str, ws_manager=None) -> List[Trade]:
        """Get all active trades for a given security ID across all accounts."""
        self.ensure_cache_valid(ws_manager=ws_manager)
        sec_id = str(security_id)
        with self._lock:
            sec_dict = self._trades_by_security.get(sec_id, {})
            out = []
            for account_trades in sec_dict.values():
                out.extend(account_trades)
            return out

    def get_active_security_ids(self) -> Set[str]:
        """Get set of all security IDs currently in memory cache."""
        with self._lock:
            return set(self._trades_by_security.keys())

    def get_active_trade_count(self) -> int:
        """Get total number of active trades currently in memory cache."""
        with self._lock:
            return len(self._trades_by_id)

    def get_all_active_trades(self) -> List[Trade]:
        """Get all active trades currently in memory cache."""
        with self._lock:
            return list(self._trades_by_id.values())

    def clear_cache(self) -> None:
        """Wipe the in-memory cache completely."""
        with self._lock:
            self._trades_by_id.clear()
            self._trades_by_security.clear()
            logger.warning("[CACHE] In-memory trade cache cleared.")

    def rebuild_cache(self, ws_manager=None) -> int:
        """Clears and rebuilds the cache from PostgreSQL DB."""
        with self._lock:
            self.clear_cache()
            count = self.build_cache_from_db()
            active_ids = list(self.get_active_security_ids())

            if ws_manager and active_ids:
                try:
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(ws_manager.subscribe(active_ids))
                    except RuntimeError:
                        asyncio.run(ws_manager.subscribe(active_ids))
                    logger.info(
                        f"[CACHE_REBUILD] Resubscribed {len(active_ids)} securities to WebSocket feed "
                        f"after cache rebuild: {active_ids}"
                    )
                except Exception as exc:
                    logger.warning(f"[CACHE_REBUILD] Failed to auto-resubscribe WebSocket: {exc}")

            return count

    def ensure_cache_valid(self, ws_manager=None) -> bool:
        """Recovery Mechanism after Cache Loss."""
        with self._lock:
            if len(self._trades_by_id) > 0:
                return False

        db = SessionLocal()
        try:
            db_active_count = (
                db.query(Trade)
                .filter(Trade.ats_state.in_([
                    AtsTradeState.ENTRY_PENDING,
                    AtsTradeState.OPEN,
                    AtsTradeState.PARTIAL_EXIT,
                ]))
                .count()
            )
            if db_active_count > 0:
                logger.warning(
                    f"[CACHE_LOSS_DETECTED] In-memory cache is empty, but PostgreSQL DB contains "
                    f"{db_active_count} active trade(s)! Auto-rebuilding cache from DB..."
                )
                self.rebuild_cache(ws_manager=ws_manager)
                logger.info(f"[CACHE_RECOVERED] Successfully recovered {db_active_count} active trade(s) from DB.")
                return True
        except Exception as exc:
            logger.error(f"[CACHE] Error checking DB for cache validation: {exc}")
        finally:
            db.close()

        return False

    def get_all_snapshots(self) -> list[dict]:
        """Read-only snapshots for API display."""
        out = []
        with self._lock:
            for t in self._trades_by_id.values():
                out.append({
                    "trade_id": str(t.id),
                    "dhan_account_id": str(t.dhan_account_id or ""),
                    "security_id": str(t.security_id or ""),
                    "ats_state": str(t.ats_state.value if hasattr(t.ats_state, "value") else t.ats_state),
                    "sl_stage": t.sl_stage,
                    "stop_price": t.stop_price,
                    "entry_price": t.entry_price,
                    "target1_price": t.target1_price,
                    "target2_price": t.target2_price,
                    "remaining_quantity": t.remaining_quantity,
                    "partial_exit_completed": t.partial_exit_completed,
                })
        return out

    async def audit_consistency(self, ws_manager=None) -> dict:
        """Background auditor comparing PostgreSQL active trades with memory cache."""
        import asyncio
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
            
            with self._lock:
                cache_ids = set(self._trades_by_id.keys())
                db_ids = {str(t.id) for t in db_active}
                
                missing_in_cache_ids = db_ids - cache_ids
                missing_in_db_ids = cache_ids - db_ids
                
                if missing_in_db_ids:
                    for tid in missing_in_db_ids:
                        self.remove_trade(tid)
                        missing_db.append(tid)
                        logger.warning(f"[CACHE_AUDIT] Trade {tid} found in cache but not active in DB. Evicted from cache.")
                
                if missing_in_cache_ids:
                    missing_sec_ids = set()
                    for tid in missing_in_cache_ids:
                        t = next(tr for tr in db_active if str(tr.id) == tid)
                        self.update_trade(t)
                        missing_cache.append(tid)
                        if t.security_id:
                            missing_sec_ids.add(str(t.security_id))
                        logger.warning(f"[CACHE_AUDIT] Trade {tid} found active in DB but missing from cache. Re-registered.")
                        
                    if ws_manager and missing_sec_ids:
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(ws_manager.subscribe(list(missing_sec_ids)))
                            logger.info(f"[CACHE_AUDIT] Resubscribed to {len(missing_sec_ids)} missing security feed(s).")
                        except Exception as exc:
                            logger.error(f"[CACHE_AUDIT] Failed to resubscribe WebSocket during audit repair: {exc}")
                            
            status = "OK" if not missing_cache and not missing_db else "REPAIRED"
            return {
                "db_active": len(db_active),
                "cache_active": len(self._trades_by_id),
                "missing_cache": missing_cache,
                "missing_db": missing_db,
                "status": status
            }
        except Exception as exc:
            logger.error(f"[CACHE_AUDIT] Auditor failed: {exc}", exc_info=True)
            return {"status": "ERROR"}
        finally:
            db.close()


_cache_manager_instance: Optional[TradeCacheManager] = None


def get_cache_manager() -> TradeCacheManager:
    global _cache_manager_instance
    if _cache_manager_instance is None:
        _cache_manager_instance = TradeCacheManager()
    return _cache_manager_instance
