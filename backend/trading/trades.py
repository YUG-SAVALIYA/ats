"""
trading/trades.py — Trade Lifecycle State Machine & In-Memory Execution Cache
=============================================================================
Defines valid trade state transitions and provides thread-safe, dual-indexed in-memory
caching for real-time sub-millisecond tick evaluations.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional, Set, Any

from database.database import SessionLocal
from database.models import Trade, AtsTradeState

logger = logging.getLogger("ats.trading.trades")

# ═══════════════════════════════════════════════════════════════════════════════
# STATE MACHINE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

ALLOWED_TRANSITIONS = {
    "SIGNAL": ["ENTRY_PENDING", "CANCELLED", "FAILED"],
    "ENTRY_PENDING": ["OPEN", "CANCELLED", "FAILED"],
    "OPEN": ["PARTIAL_EXIT", "EXIT_REQUESTED", "CLOSED"],
    "PARTIAL_EXIT": ["EXIT_REQUESTED", "CLOSED"],
    "EXIT_REQUESTED": ["OPEN", "PARTIAL_EXIT", "EXIT_UNKNOWN", "EXIT_FAILED", "CLOSED"],
    "EXIT_FAILED": ["EXIT_REQUESTED", "OPEN", "PARTIAL_EXIT", "CLOSED", "FAILED"],
    "EXIT_UNKNOWN": ["EXIT_REQUESTED", "OPEN", "PARTIAL_EXIT", "CLOSED", "FAILED"],
    "CLOSED": [],
    "CANCELLED": [],
    "FAILED": []
}


def validate_state_transition(trade: Trade, new_state: AtsTradeState) -> bool:
    """
    Validates and transitions trade states.
    Returns True if valid, False if blocked by state machine rules.
    """
    old = getattr(trade, "ats_state", None)
    new_str = str(new_state.value if hasattr(new_state, "value") else new_state)
    old_str = str(old.value if hasattr(old, "value") else old) if old else None
    
    if old_str and old_str != new_str:
        if new_str not in ALLOWED_TRANSITIONS.get(old_str, []):
            logger.critical(f"[STATE_MACHINE] Invalid transition blocked: {old_str} -> {new_str} for Trade {trade.id}")
            return False
            
    trade.ats_state = new_state
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE CACHE MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

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
        self._trades_by_security: Dict[str, Dict[str, Trade]] = {}

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
                    # Data integrity normalization
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

        self._trades_by_id[tid] = trade

        if sec_id:
            if sec_id not in self._trades_by_security:
                self._trades_by_security[sec_id] = {}
            self._trades_by_security[sec_id][tid] = trade

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
                if sec_id in self._trades_by_security:
                    self._trades_by_security[sec_id].pop(tid, None)
                    if not self._trades_by_security[sec_id]:
                        self._trades_by_security.pop(sec_id, None)
                logger.info(f"[CACHE] Removed trade {tid} (sec={sec_id}) from memory cache.")
            return trade

    def get_trade(self, trade_id: str) -> Optional[Trade]:
        """Get single trade by ID from memory cache."""
        with self._lock:
            return self._trades_by_id.get(str(trade_id))

    def get_trades_for_security(self, security_id: str, ws_manager=None) -> List[Trade]:
        """Get all active trades for a given security ID."""
        self.ensure_cache_valid(ws_manager=ws_manager)
        sec_id = str(security_id)
        with self._lock:
            sec_dict = self._trades_by_security.get(sec_id, {})
            return list(sec_dict.values())

    def get_active_security_ids(self) -> Set[str]:
        """Get set of all security IDs currently in memory cache."""
        with self._lock:
            return set(self._trades_by_security.keys())

    def get_active_trade_count(self) -> int:
        """Get total number of active trades currently in memory cache."""
        with self._lock:
            return len(self._trades_by_id)

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
                        f"[CACHE_REBUILD] Resubscribed {len(active_ids)} securities to WebSocket feed: {active_ids}"
                    )
                except Exception as exc:
                    logger.warning(f"[CACHE_REBUILD] Failed to auto-resubscribe WebSocket: {exc}")

            return count

    def ensure_cache_valid(self, ws_manager=None) -> bool:
        """Recovery Mechanism after Cache Loss: rebuilds cache if empty but active trades exist in DB."""
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
                    f"[CACHE_LOSS_DETECTED] In-memory cache empty, but PostgreSQL DB contains "
                    f"{db_active_count} active trade(s)! Rebuilding cache from DB..."
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


_cache_manager_instance: Optional[TradeCacheManager] = None


def get_cache_manager() -> TradeCacheManager:
    global _cache_manager_instance
    if _cache_manager_instance is None:
        _cache_manager_instance = TradeCacheManager()
    return _cache_manager_instance
