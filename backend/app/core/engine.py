"""
app/core/engine.py
==================
TradeEngine — central async state machine for managing open ATS trades,
integrated with production-safe TradeCacheManager.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set, List

from app.database import SessionLocal
from app.models import Trade, AtsTradeState, TradeEvent
from app.core.levels import (
    sl_price_for_stage,
    stage_1_trigger, stage_2_trigger, stage_3_trigger, final_target,
)
from app.core.cache_manager import get_cache_manager, TradeCacheManager

logger = logging.getLogger("ats.trade_engine")


def _r2(v: float) -> float:
    return round(v, 2)


def _log_event(
    db,
    trade_id: str,
    event_type: str,
    detail: str = "",
    price: float | None = None,
    quantity: int | None = None,
) -> None:
    try:
        db.add(TradeEvent(
            trade_id=trade_id,
            event_type=event_type,
            detail=detail,
            price=price,
            quantity=quantity,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as exc:
        logger.warning(f"[ENGINE] Event log failed ({event_type}) for {trade_id}: {exc}")
        try:
            db.rollback()
        except Exception:
            pass


class TradeEngine:
    """Central in-memory trade state machine backed by TradeCacheManager & PostgreSQL."""

    def __init__(self, place_market_sell_fn: Callable, ws_manager=None):
        self._sell = place_market_sell_fn
        self.ws_manager = ws_manager
        self.cache_manager: TradeCacheManager = get_cache_manager()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._exit_in_progress: Set[str] = set()
        self._first_tick_seen: Set[str] = set()
        self._last_tick_map: Dict[str, float] = {}

    def set_ws_manager(self, ws_manager) -> None:
        """Inject WebSocket feed manager for automatic resubscription."""
        self.ws_manager = ws_manager

    async def recover_from_db(self) -> int:
        """Load all active trades from PostgreSQL DB and build cache."""
        count = self.cache_manager.build_cache_from_db()
        for sec_id in self.cache_manager.get_active_security_ids():
            for t in self.cache_manager.get_trades_for_security(sec_id):
                self._locks[t.id] = asyncio.Lock()
        logger.info(f"[ENGINE] Startup recovery complete — {count} active trades cached from DB.")
        return count

    async def register_trade(self, trade: Trade) -> None:
        """Register a freshly filled trade for monitoring in DB + Cache."""
        self.cache_manager.add_trade(trade)
        self._locks[trade.id] = asyncio.Lock()
        logger.info(
            f"[ENGINE] Registered trade {trade.id} "
            f"entry={trade.entry_price} sl={trade.stop_price} "
            f"t1={trade.target1_price} t2={trade.target2_price}"
        )

    async def deregister_trade(self, trade_id: str) -> None:
        """Remove trade from cache after close/fail."""
        # Get the trade first to know its security_id
        trade = self.cache_manager.get_trade(trade_id)
        sec_id = str(trade.security_id) if trade and trade.security_id else None

        self.cache_manager.remove_trade(trade_id)
        self._locks.pop(trade_id, None)
        self._exit_in_progress.discard(trade_id)
        self._first_tick_seen.discard(trade_id)

        # Unsubscribe if no more active trades require this security
        if sec_id and self.ws_manager:
            active_for_sec = self.cache_manager.get_trades_for_security(sec_id)
            if not active_for_sec:
                logger.info(f"[ENGINE] No more active trades for {sec_id}. Unsubscribing from feed.")
                await self.ws_manager.unsubscribe([sec_id])

    async def tick_health_loop(self) -> None:
        """
        Background task: Verifies all active security feeds are receiving ticks.
        Runs every 60s. Alerts and resubscribes if dead during market hours.
        """
        import time
        from app.utils.time_utils import _is_market_hours
        
        while True:
            try:
                await asyncio.sleep(60.0)
                if not _is_market_hours():
                    continue
                    
                now = time.time()
                active_sec_ids = self.cache_manager.get_active_security_ids()
                dead_feeds = []
                
                for sec_id in active_sec_ids:
                    last_tick = self._last_tick_map.get(sec_id, 0)
                    if now - last_tick > 60.0:
                        dead_feeds.append(sec_id)
                        
                if dead_feeds:
                    logger.critical(f"[TICK_HEALTH] 🔴 {len(dead_feeds)} active feeds went silent for >60s! {dead_feeds}")
                    if self.ws_manager:
                        logger.info(f"[TICK_HEALTH] Forcing resubscription for dead feeds...")
                        await self.ws_manager.subscribe(dead_feeds)
                        for sec_id in dead_feeds:
                            self._last_tick_map[sec_id] = time.time()  # Reset to avoid spam
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TICK_HEALTH] Error in health loop: {e}")

    async def on_tick(self, security_id: str, ltp: float) -> None:
        """Entry point for every market feed tick."""
        if security_id == "__reconnect__":
            logger.info("[ENGINE] WebSocket reconnected — monitoring resumed")
            return

        import time
        self._last_tick_map[security_id] = time.time()

        # Recovery mechanism after cache loss check
        self.cache_manager.ensure_cache_valid(ws_manager=self.ws_manager)

        # Retrieve active trades for security from memory cache
        matching_trades = self.cache_manager.get_trades_for_security(security_id, ws_manager=self.ws_manager)

        for trade in matching_trades:
            trade_id = str(trade.id)
            if trade_id in self._exit_in_progress:
                continue
            if trade_id not in self._locks:
                self._locks[trade_id] = asyncio.Lock()
            await self._evaluate(trade, ltp)

    async def _evaluate(self, trade: Trade, ltp: float) -> None:
        """Evaluate a single trade against the current LTP."""
        trade_id = str(trade.id)
        lock = self._locks.get(trade_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[trade_id] = lock

        async with lock:
            if trade_id in self._exit_in_progress:
                return

            is_first = trade_id not in self._first_tick_seen
            self._first_tick_seen.add(trade_id)

            state = str(trade.ats_state.value if hasattr(trade.ats_state, "value") else trade.ats_state)

            # --- INVARIANT ASSERTIONS ---
            if state == "CLOSED":
                logger.error(f"[INVARIANT_ERROR] Trade {trade_id} is CLOSED but still in memory cache! Evicting.")
                self.cache_manager.remove_trade(trade_id)
                return
            
            if (trade.remaining_quantity or trade.allocated_quantity or 0) <= 0:
                logger.error(f"[INVARIANT_ERROR] Trade {trade_id} has qty <= 0 but is in state {state}! Evicting.")
                self.cache_manager.remove_trade(trade_id)
                return
            # ----------------------------

            if state == "OPEN" or state == AtsTradeState.OPEN:
                await self._evaluate_open(trade, ltp, is_first)
            elif state == "PARTIAL_EXIT" or state == AtsTradeState.PARTIAL_EXIT:
                await self._evaluate_partial_exit(trade, ltp, is_first)

    async def _evaluate_open(self, trade: Trade, ltp: float, is_first: bool) -> None:
        """Evaluate a fully OPEN trade."""
        tid = str(trade.id)
        entry = trade.entry_price or 0.0
        if not entry:
            return

        current_sl = trade.stop_price or _r2(entry * 0.95)
        current_stage = trade.sl_stage or 0

        if is_first and ltp <= current_sl:
            logger.warning(f"[ENGINE] GAP DOWN on first tick for {tid}: LTP={ltp} <= SL={current_sl}")
            await self._record_gap(tid, ltp, "GAP_DOWN_SL")

        if ltp <= current_sl:
            logger.info(f"[ENGINE] SL HIT for {tid}: LTP={ltp} <= SL={current_sl} (stage={current_stage})")
            await self._execute_final_exit(trade, ltp, "SL_HIT")
            return

        new_stage = self._compute_new_stage(current_stage, ltp, entry)
        if new_stage > current_stage:
            await self._apply_stage_upgrade(trade, ltp, current_stage, new_stage)

    async def _evaluate_partial_exit(self, trade: Trade, ltp: float, is_first: bool) -> None:
        """Evaluate a trade in PARTIAL_EXIT state."""
        tid = str(trade.id)
        sl = trade.stop_price or 0.0
        t2 = trade.target2_price or 0.0

        if sl and ltp <= sl:
            logger.info(f"[ENGINE] SL HIT (post-partial) for {tid}: LTP={ltp} <= SL={sl}")
            await self._execute_final_exit(trade, ltp, "SL_HIT")
            return

        if t2 and ltp >= t2:
            logger.info(f"[ENGINE] TARGET2 HIT for {tid}: LTP={ltp} >= T2={t2}")
            await self._execute_final_exit(trade, ltp, "TARGET2_HIT")
            return

    def _compute_new_stage(self, current_stage: int, ltp: float, entry: float) -> int:
        """Compute the new stage given LTP. SL only ever moves up."""
        if current_stage >= 3:
            return 3
        if ltp >= stage_3_trigger(entry):
            return 3
        if current_stage >= 2:
            return 2
        if ltp >= stage_2_trigger(entry):
            return 2
        if current_stage >= 1:
            return 1
        if ltp >= stage_1_trigger(entry):
            return 1
        return current_stage

    async def _apply_stage_upgrade(
        self, trade: Trade, ltp: float, old_stage: int, new_stage: int
    ) -> None:
        """
        Persist the SL stage upgrade and stop_price update immediately to PostgreSQL,
        then update in-memory cache.
        """
        tid = str(trade.id)
        entry = trade.entry_price or 0.0
        new_sl = sl_price_for_stage(entry, new_stage)

        db = SessionLocal()
        try:
            t = db.query(Trade).filter(Trade.id == tid).with_for_update().first()
            if not t:
                logger.error(f"[ENGINE] Stage upgrade: trade {tid} not found in DB")
                return

            if (t.sl_stage or 0) >= new_stage:
                logger.debug(f"[ENGINE] Stage {new_stage} already applied for {tid} — skip")
                trade.sl_stage = t.sl_stage
                trade.stop_price = t.stop_price
                self.cache_manager.update_trade(trade)
                return

            t.sl_stage = new_stage
            t.stop_price = new_sl
            db.commit()

            # Update in-memory trade & cache
            trade.sl_stage = new_stage
            trade.stop_price = new_sl
            self.cache_manager.update_trade(trade)

            logger.info(
                f"[ENGINE] Stage upgrade {old_stage}→{new_stage} for {tid}: "
                f"SL={new_sl}, LTP={ltp}"
            )
            stage_labels = {1: "+5%→SL+2%", 2: "+8%→SL+4%", 3: "+12%→SL+5%"}
            _log_event(
                db, tid, f"SL_STAGE_{new_stage}",
                f"Trailing SL upgraded: {stage_labels.get(new_stage, '')} "
                f"LTP={ltp}, new SL={new_sl}",
                price=ltp,
            )
        finally:
            db.close()

        if new_stage == 3:
            await self._execute_partial_exit(trade, ltp)

    async def _execute_partial_exit(self, trade: Trade, ltp: float) -> None:
        """
        Sell 50% of original position at MARKET.
        Persists PARTIAL_EXIT state to PostgreSQL DB and updates memory cache.
        """
        tid = str(trade.id)

        if trade.partial_exit_completed:
            logger.debug(f"[ENGINE] Partial exit already done for {tid} — skip")
            return

        self._exit_in_progress.add(tid)
        db = SessionLocal()
        try:
            t = db.query(Trade).filter(Trade.id == tid).with_for_update().first()
            if not t or t.partial_exit_completed:
                logger.debug(f"[ENGINE] Partial exit already done (DB check) for {tid}")
                return

            total_qty = t.remaining_quantity or t.allocated_quantity or 0
            if total_qty < 2:
                logger.warning(f"[ENGINE] qty={total_qty} too small to split for {tid}, doing full exit")
                db.close()
                db = None
                self._exit_in_progress.discard(tid)
                await self._execute_final_exit(trade, ltp, "TARGET1_HIT_FULL_EXIT")
                return

            partial_qty = total_qty // 2
            remaining_qty = total_qty - partial_qty

            _log_event(db, tid, "TARGET1_HIT",
                       f"LTP={ltp} >= +12%. Placing partial SELL {partial_qty}/{total_qty} qty",
                       price=ltp, quantity=partial_qty)

            await self._sell(
                trade_id=tid,
                security_id=str(trade.security_id),
                qty=partial_qty,
                purpose="PARTIAL_EXIT",
                tag=f"ATS_P1_{tid[:8].upper()}",
            )

            _log_event(db, tid, "PARTIAL_EXIT_REQUESTED",
                       f"Partial SELL {partial_qty} requested at LTP={ltp}. Awaiting fill confirmation.",
                       price=ltp, quantity=partial_qty)

            logger.info(f"[ENGINE] Partial exit order requested for {tid}: qty={partial_qty}, total_qty={total_qty}")

        except Exception as exc:
            logger.error(f"[ENGINE] Partial exit failed for {tid}: {exc}", exc_info=True)
        finally:
            if db:
                db.close()
            self._exit_in_progress.discard(tid)

    async def _execute_final_exit(self, trade: Trade, ltp: float, reason: str) -> None:
        """
        Sell all remaining quantity at MARKET.
        Persists CLOSED state to PostgreSQL DB and removes trade from memory cache.
        """
        tid = str(trade.id)
        self._exit_in_progress.add(tid)
        db = SessionLocal()
        try:
            t = db.query(Trade).filter(Trade.id == tid).with_for_update().first()
            if not t:
                return
            state_str = str(t.ats_state.value if hasattr(t.ats_state, "value") else t.ats_state)
            if state_str == "CLOSED" or state_str == AtsTradeState.CLOSED:
                logger.debug(f"[ENGINE] Trade {tid} already CLOSED in DB — skip final exit")
                return

            qty = t.remaining_quantity or t.allocated_quantity or 0
            if qty <= 0:
                logger.warning(f"[ENGINE] Trade {tid} has qty={qty} — nothing to sell")
                return

            _log_event(db, tid, "EXIT_CONDITION_MATCHED",
                       f"Exit condition matched. LTP={ltp}, qty={qty}, reason={reason}",
                       price=ltp, quantity=qty)

            ord_res = await self._sell(
                trade_id=tid,
                security_id=str(trade.security_id),
                qty=qty,
                purpose="FINAL_EXIT",
                tag=f"ATS_FX_{tid[:8].upper()}",
            )

            t2 = db.query(Trade).filter(Trade.id == tid).first()
            if t2:
                t2.exit_reason = reason
                db.commit()

                state_val = str(t2.ats_state.value if hasattr(t2.ats_state, "value") else t2.ats_state)
                trade.ats_state = t2.ats_state

                if state_val in ("CLOSED", "FAILED", "CANCELLED"):
                    self.cache_manager.remove_trade(tid)
                    self._locks.pop(tid, None)
                    self._first_tick_seen.discard(tid)
                else:
                    self.cache_manager.update_trade(trade)

            logger.info(f"[ENGINE] Final exit request processed for {tid}: reason={reason}, qty={qty}, ltp={ltp}")

        except Exception as exc:
            logger.error(f"[ENGINE] Final exit failed for {tid}: {exc}", exc_info=True)
        finally:
            db.close()
            self._exit_in_progress.discard(tid)

    async def trigger_supertrend_exit(self, trade: Trade, ltp: float) -> None:
        """Called by strategy engine when Supertrend flips RED."""
        if str(trade.id) in self._exit_in_progress:
            return
        logger.info(f"[ENGINE] Supertrend RED exit for {trade.id} @ LTP={ltp}")
        await self._execute_final_exit(trade, ltp, "SUPERTREND_RED")

    async def _record_gap(self, trade_id: str, ltp: float, gap_type: str) -> None:
        db = SessionLocal()
        try:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
            if t:
                t.gap_detected = True
                t.gap_pct = _r2(((ltp - (t.entry_price or ltp)) / (t.entry_price or ltp)) * 100)
                db.commit()
            _log_event(db, trade_id, "GAP_DETECTED",
                       f"type={gap_type} LTP={ltp}", price=ltp)
        finally:
            db.close()

    def get_active_security_ids(self) -> list[str]:
        return list(self.cache_manager.get_active_security_ids())

    def get_active_trade_count(self) -> int:
        return self.cache_manager.get_active_trade_count()

    def get_trade_snapshot(self, trade_id: str) -> Optional[Trade]:
        return self.cache_manager.get_trade(trade_id)

    def get_all_snapshots(self) -> list[dict]:
        out = []
        for sec_id in self.cache_manager.get_active_security_ids():
            for t in self.cache_manager.get_trades_for_security(sec_id, ws_manager=self.ws_manager):
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


_engine_instance: Optional[TradeEngine] = None


def get_trade_engine() -> Optional[TradeEngine]:
    return _engine_instance


def init_trade_engine(place_market_sell_fn: Callable, ws_manager=None) -> TradeEngine:
    global _engine_instance
    _engine_instance = TradeEngine(place_market_sell_fn, ws_manager=ws_manager)
    return _engine_instance
