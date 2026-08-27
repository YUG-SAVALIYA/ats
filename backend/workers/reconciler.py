"""
workers/reconciler.py — Fill Reconciler & 3-Way Broker Synchronization Engine
=============================================================================
Provides:
1. OrderReconciler: 5-second polling worker for pending entry order fills.
2. BrokerReconciler: Startup and 30-second 3-way reconciliation between Dhan broker API,
   PostgreSQL DB, and TradeCacheManager memory cache.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any, List
import pytz

from database.database import SessionLocal
from database.models import Trade, AtsOrder, AtsTradeState, OrderPurpose
from database.repositories import log_trade_event
from dhan.client import get_dhan_client
from dhan.endpoints import get_order_by_id_url
from dhan.portfolio import PortfolioService
from trading.trades import get_cache_manager, TradeCacheManager, validate_state_transition
from trading.orders import confirm_exit_fill

logger = logging.getLogger("ats.workers.reconciler")
IST = pytz.timezone("Asia/Kolkata")

_POLL_INTERVAL_SEC = 5.0
_RECONCILE_INTERVAL_SEC = 30.0


def _is_market_hours() -> bool:
    """Return True if current IST time is within market hours (9:15–15:35 Mon–Fri)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 10) <= t <= (15 * 60 + 35)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PENDING ORDER FILL RECONCILER (Polls every 5s)
# ═══════════════════════════════════════════════════════════════════════════════

class OrderReconciler:
    """Polls Dhan for fill status of ENTRY_PENDING orders."""

    def __init__(
        self,
        confirm_fill_fn: Callable,
        register_trade_fn: Callable,
        subscribe_fn: Callable,
    ):
        self._confirm_fill = confirm_fill_fn
        self._register_trade = register_trade_fn
        self._subscribe = subscribe_fn
        self._running = False

    async def run(self) -> None:
        """Infinite reconciliation loop."""
        self._running = True
        logger.info("[RECONCILER] Order reconciler started")
        while self._running:
            try:
                if _is_market_hours():
                    await self._reconcile_cycle()
            except Exception as exc:
                logger.error(f"[RECONCILER] Cycle error: {exc}", exc_info=True)
            await asyncio.sleep(_POLL_INTERVAL_SEC)

    async def stop(self) -> None:
        self._running = False
        logger.info("[RECONCILER] Order reconciler stopped")

    async def _reconcile_cycle(self) -> None:
        """One reconciliation pass: load pending trades and poll Dhan."""
        db = SessionLocal()
        try:
            pending_trades = (
                db.query(Trade)
                .filter(Trade.ats_state == AtsTradeState.ENTRY_PENDING)
                .all()
            )
        finally:
            db.close()

        if not pending_trades:
            return

        logger.debug(f"[RECONCILER] Checking {len(pending_trades)} ENTRY_PENDING trade(s)")

        for trade in pending_trades:
            await self._check_trade_fill(trade)

    async def _check_trade_fill(self, trade: Trade) -> None:
        """Poll Dhan for the entry order status of one trade."""
        db = SessionLocal()
        try:
            entry_order = (
                db.query(AtsOrder)
                .filter(
                    AtsOrder.trade_id == trade.id,
                    AtsOrder.order_purpose == OrderPurpose.ENTRY,
                )
                .first()
            )
            if not entry_order or not entry_order.dhan_order_id:
                return
            if entry_order.status in ("FILLED", "CANCELLED", "REJECTED"):
                return

            dhan_order_id = entry_order.dhan_order_id
            entry_order_id = entry_order.id
        finally:
            db.close()

        try:
            client = get_dhan_client()
            url = get_order_by_id_url(dhan_order_id)
            res = client.execute_v2_get(url)

            if not isinstance(res, dict):
                return

            order_status = str(res.get("orderStatus") or res.get("status") or "").upper()
            filled_qty = int(
                res.get("filledQty") or res.get("tradedQuantity") or
                res.get("filled_qty") or 0
            )
            avg_price = float(
                res.get("avgTradedPrice") or res.get("tradedPrice") or
                res.get("price") or res.get("avg_traded_price") or 0.0
            )

            if order_status in ("TRADED", "PART_TRADED") and filled_qty > 0 and avg_price > 0:
                await self._on_fill(trade, filled_qty, avg_price, entry_order_id)
            elif order_status in ("CANCELLED", "REJECTED", "EXPIRED", "INACTIVE"):
                await self._on_rejected(trade, entry_order_id, order_status)

        except Exception as exc:
            logger.warning(f"[RECONCILER] Dhan poll failed for order {dhan_order_id}: {exc}")

    async def _on_fill(
        self, trade: Trade, fill_qty: int, fill_price: float, entry_order_id: str
    ) -> None:
        """Handle a confirmed entry fill."""
        logger.info(
            f"[RECONCILER] Entry fill confirmed: trade={trade.id}, "
            f"qty={fill_qty}, price={fill_price}"
        )

        updated_trade = self._confirm_fill(trade.id, fill_price, fill_qty)
        if not updated_trade:
            logger.error(f"[RECONCILER] confirm_entry_fill returned None for {trade.id}")
            return

        await self._register_trade(updated_trade)

        if updated_trade.security_id:
            await self._subscribe([str(updated_trade.security_id)])
            logger.info(
                f"[RECONCILER] Subscribed sec {updated_trade.security_id} to WS after fill for trade {trade.id}"
            )

    async def _on_rejected(self, trade: Trade, entry_order_id: str, reason: str) -> None:
        """Handle a rejected/cancelled entry order."""
        logger.warning(f"[RECONCILER] Entry order {reason} for trade {trade.id}")
        db = SessionLocal()
        try:
            t = db.query(Trade).filter(Trade.id == trade.id).with_for_update().first()
            o = db.query(AtsOrder).filter(AtsOrder.id == entry_order_id).first()
            if t:
                t.ats_state = AtsTradeState.CANCELLED
                t.trade_status = "CANCELLED"
            if o:
                o.status = reason
                o.updated_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as exc:
            logger.error(f"[RECONCILER] Failed to mark trade {trade.id} cancelled: {exc}")
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()


_reconciler_instance: Optional[OrderReconciler] = None


def get_reconciler() -> Optional[OrderReconciler]:
    return _reconciler_instance


def init_reconciler(
    confirm_fill_fn: Callable,
    register_trade_fn: Callable,
    subscribe_fn: Callable,
) -> OrderReconciler:
    global _reconciler_instance
    _reconciler_instance = OrderReconciler(confirm_fill_fn, register_trade_fn, subscribe_fn)
    return _reconciler_instance


# ═══════════════════════════════════════════════════════════════════════════════
# 2. BROKER RECONCILER (Startup & Periodic 30s)
# ═══════════════════════════════════════════════════════════════════════════════

class BrokerReconciler:
    """Manages 3-way broker reconciliation between Dhan API, PostgreSQL DB, and memory cache."""

    def __init__(self, confirm_fill_fn: Callable = None, ws_manager=None):
        self.portfolio_service = PortfolioService()
        self.cache_manager: TradeCacheManager = get_cache_manager()
        self.confirm_fill_fn = confirm_fill_fn
        self.ws_manager = ws_manager
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def set_ws_manager(self, ws_manager) -> None:
        self.ws_manager = ws_manager

    def set_confirm_fill_fn(self, confirm_fill_fn: Callable) -> None:
        self.confirm_fill_fn = confirm_fill_fn

    async def run(self) -> None:
        """Periodic background reconciliation task."""
        self._running = True
        logger.info(f"[BROKER_RECONCILE] Periodic broker reconciler started (interval: {_RECONCILE_INTERVAL_SEC}s)")
        while self._running:
            try:
                await asyncio.sleep(_RECONCILE_INTERVAL_SEC)
                if self._running:
                    await self.reconcile_cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[BROKER_RECONCILE] Error in periodic cycle: {exc}", exc_info=True)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("[BROKER_RECONCILE] Periodic broker reconciler stopped.")

    async def reconcile_on_startup(self) -> Dict[str, Any]:
        """Runs full reconciliation on backend startup."""
        logger.info("[BROKER_RECONCILE] Running STARTUP broker reconciliation...")
        return await self.reconcile_cycle(is_startup=True)

    async def reconcile_cycle(self, is_startup: bool = False) -> Dict[str, Any]:
        """Executes one full 3-way reconciliation pass."""
        db = SessionLocal()
        discrepancies_fixed = 0
        reconciled_trades = 0

        try:
            # 1. Fetch live positions from Dhan broker API
            live_positions = self.portfolio_service.get_positions()
            broker_pos_by_sec: Dict[str, int] = {}

            if isinstance(live_positions, list):
                for pos in live_positions:
                    sec_id = str(pos.get("securityId", pos.get("security_id", ""))).strip()
                    net_qty = int(pos.get("netQty", pos.get("net_qty", 0)) or 0)
                    if sec_id:
                        broker_pos_by_sec[sec_id] = broker_pos_by_sec.get(sec_id, 0) + net_qty

            # 2. Query all local active trades from PostgreSQL DB
            active_trades = (
                db.query(Trade)
                .filter(Trade.ats_state.in_([
                    AtsTradeState.ENTRY_PENDING,
                    AtsTradeState.OPEN,
                    AtsTradeState.PARTIAL_EXIT,
                    AtsTradeState.EXIT_REQUESTED,
                    AtsTradeState.EXIT_FAILED,
                    AtsTradeState.EXIT_UNKNOWN,
                ]))
                .all()
            )

            reconciled_trades = len(active_trades)

            for trade in active_trades:
                tid = str(trade.id)
                sec_id = str(trade.security_id or "")
                state = str(trade.ats_state.value if hasattr(trade.ats_state, "value") else trade.ats_state)

                # A. Handle ENTRY_PENDING orders
                if state == "ENTRY_PENDING":
                    entry_order = (
                        db.query(AtsOrder)
                        .filter(AtsOrder.trade_id == tid, AtsOrder.order_purpose == OrderPurpose.ENTRY)
                        .first()
                    )
                    if entry_order and entry_order.dhan_order_id:
                        try:
                            client = get_dhan_client()
                            url = get_order_by_id_url(entry_order.dhan_order_id)
                            res = client.execute_v2_get(url)
                            if isinstance(res, list) and len(res) > 0:
                                res = res[0]

                            if isinstance(res, dict):
                                ord_status = str(res.get("orderStatus") or res.get("status") or "").upper()
                                fill_qty = int(res.get("filledQty") or res.get("tradedQuantity") or 0)
                                fill_price = float(res.get("avgTradedPrice") or res.get("averageTradedPrice") or res.get("tradedPrice") or 0.0)

                                if ord_status in ("TRADED", "FILLED", "PART_TRADED") and fill_qty > 0 and fill_price > 0:
                                    if self.confirm_fill_fn:
                                        updated_trade = self.confirm_fill_fn(tid, fill_price, fill_qty)
                                        if updated_trade:
                                            self.cache_manager.update_trade(updated_trade)
                                        discrepancies_fixed += 1
                                        logger.info(f"[BROKER_RECONCILE] Confirmed pending entry fill for trade {tid} @ ₹{fill_price}")

                                elif ord_status in ("CANCELLED", "REJECTED", "EXPIRED"):
                                    trade.ats_state = AtsTradeState.CANCELLED
                                    trade.trade_status = "CANCELLED"
                                    entry_order.status = ord_status
                                    db.commit()
                                    self.cache_manager.remove_trade(tid)
                                    discrepancies_fixed += 1
                                    logger.warning(f"[BROKER_RECONCILE] Pending entry trade {tid} was {ord_status} on broker.")
                        except Exception as exc:
                            logger.warning(f"[BROKER_RECONCILE] Failed to check pending order for trade {tid}: {exc}")

                # B. Handle EXIT_REQUESTED & EXIT_UNKNOWN trade states
                elif state in ("EXIT_REQUESTED", "EXIT_UNKNOWN", "EXIT_FAILED"):
                    exit_order = (
                        db.query(AtsOrder)
                        .filter(AtsOrder.trade_id == tid, AtsOrder.transaction_type == "SELL")
                        .order_by(AtsOrder.created_at.desc())
                        .first()
                    )
                    
                    if exit_order and not exit_order.dhan_order_id:
                        placed_time = exit_order.placed_at or exit_order.created_at
                        if placed_time:
                            if placed_time.tzinfo is None:
                                placed_time = placed_time.replace(tzinfo=timezone.utc)
                            elapsed_sec = (datetime.now(timezone.utc) - placed_time).total_seconds()
                            if elapsed_sec < 30.0:
                                continue
                            else:
                                broker_qty = broker_pos_by_sec.get(sec_id, 0)
                                if broker_qty <= 0:
                                    if validate_state_transition(trade, AtsTradeState.CLOSED):
                                        trade.trade_status = "CLOSED"
                                        trade.remaining_quantity = 0
                                        trade.closed_at = datetime.now(timezone.utc)
                                        db.commit()
                                        self.cache_manager.remove_trade(tid)
                                        log_trade_event(db, tid, "CRASH_RECOVERY_CLOSED", "Trade closed on broker despite missing order ID.")
                                        discrepancies_fixed += 1
                                    continue
                                else:
                                    exit_order.status = "FAILED_CRASH_RECOVERY"
                                    exit_order.updated_at = datetime.now(timezone.utc)
                                    new_state = AtsTradeState.PARTIAL_EXIT if getattr(trade, 'partial_exit_completed', False) else AtsTradeState.OPEN
                                    if validate_state_transition(trade, new_state):
                                        db.commit()
                                        self.cache_manager.update_trade(trade)
                                        log_trade_event(db, tid, "CRASH_RECOVERY_REVERT", f"Reverted trade state to {new_state}")
                                        discrepancies_fixed += 1
                                    continue

                    if exit_order and exit_order.dhan_order_id:
                        try:
                            client = get_dhan_client()
                            url = get_order_by_id_url(exit_order.dhan_order_id)
                            res = client.execute_v2_get(url)
                            if isinstance(res, list) and len(res) > 0:
                                res = res[0]

                            if isinstance(res, dict):
                                ord_status = str(res.get("orderStatus") or res.get("status") or "").upper()
                                fill_qty = int(res.get("filledQty") or res.get("tradedQuantity") or 0)
                                fill_price = float(res.get("avgTradedPrice") or res.get("averageTradedPrice") or res.get("tradedPrice") or 0.0)

                                if ord_status in ("TRADED", "FILLED", "PART_TRADED") and fill_qty > 0 and fill_price > 0:
                                    updated_trade = confirm_exit_fill(tid, fill_price, fill_qty, exit_order.dhan_order_id)
                                    if updated_trade and str(getattr(updated_trade.ats_state, "value", updated_trade.ats_state)) not in ("CLOSED", "CANCELLED", "FAILED"):
                                        self.cache_manager.update_trade(updated_trade)
                                    else:
                                        self.cache_manager.remove_trade(tid)
                                    discrepancies_fixed += 1
                                    log_trade_event(db, tid, "EXIT_RECONCILIATED", f"Confirmed exit fill @ ₹{fill_price} (qty={fill_qty})")
                                    logger.info(f"[BROKER_RECONCILE] Confirmed exit fill for trade {tid} @ ₹{fill_price}")

                                elif ord_status in ("REJECTED", "CANCELLED", "EXPIRED"):
                                    if validate_state_transition(trade, AtsTradeState.EXIT_FAILED):
                                        exit_order.status = ord_status
                                        db.commit()
                                        self.cache_manager.update_trade(trade)
                                        discrepancies_fixed += 1
                        except Exception as exc:
                            logger.warning(f"[BROKER_RECONCILE] Failed to reconcile exit order for {tid}: {exc}")

                    broker_qty = broker_pos_by_sec.get(sec_id, 0)
                    if broker_qty <= 0:
                        if validate_state_transition(trade, AtsTradeState.CLOSED):
                            trade.trade_status = "CLOSED"
                            trade.remaining_quantity = 0
                            trade.closed_at = datetime.now(timezone.utc)
                        db.commit()
                        self.cache_manager.remove_trade(tid)
                        log_trade_event(db, tid, "BROKER_RECONCILED_CLOSED", f"Broker net_qty=0 for trade in {state}")
                        discrepancies_fixed += 1

                # C. Handle OPEN & PARTIAL_EXIT positions
                elif state in ("OPEN", "PARTIAL_EXIT"):
                    broker_qty = broker_pos_by_sec.get(sec_id, 0)
                    local_qty = trade.remaining_quantity or trade.allocated_quantity or 0

                    if broker_qty <= 0:
                        logger.warning(
                            f"[BROKER_RECONCILE] DISCREPANCY DETECTED: Trade {tid} (sec={sec_id}) "
                            f"is {state} locally (qty={local_qty}) but net_qty=0 on Dhan! Closing trade."
                        )
                        trade.ats_state = AtsTradeState.CLOSED
                        trade.trade_status = "CLOSED"
                        trade.remaining_quantity = 0
                        trade.exit_reason = "BROKER_RECONCILED_CLOSED"
                        trade.closed_at = datetime.now(timezone.utc)
                        db.commit()

                        self.cache_manager.remove_trade(tid)
                        log_trade_event(db, tid, "BROKER_DISCREPANCY_CLOSED", "Closed trade because broker net_qty=0")
                        discrepancies_fixed += 1

                    elif broker_qty < local_qty:
                        logger.warning(
                            f"[BROKER_RECONCILE] DISCREPANCY DETECTED: Trade {tid} (sec={sec_id}) "
                            f"remaining_quantity ({local_qty}) > broker net_qty ({broker_qty}). Updating remaining qty."
                        )
                        trade.remaining_quantity = broker_qty
                        trade.ats_state = AtsTradeState.PARTIAL_EXIT
                        trade.partial_exit_completed = True
                        db.commit()

                        self.cache_manager.update_trade(trade)
                        log_trade_event(db, tid, "BROKER_DISCREPANCY_QTY_UPDATED", f"Updated remaining_quantity from {local_qty} to {broker_qty}")
                        discrepancies_fixed += 1

            # 3. Resubscribe WebSocket if securities changed
            active_sec_ids = list(self.cache_manager.get_active_security_ids())
            if self.ws_manager and active_sec_ids:
                try:
                    if asyncio.iscoroutinefunction(self.ws_manager.subscribe):
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(self.ws_manager.subscribe(active_sec_ids))
                        except RuntimeError:
                            asyncio.run(self.ws_manager.subscribe(active_sec_ids))
                except Exception as exc:
                    logger.warning(f"[BROKER_RECONCILE] WebSocket resubscribe notice: {exc}")

            summary = {
                "status": "completed",
                "is_startup": is_startup,
                "reconciled_trades": reconciled_trades,
                "discrepancies_fixed": discrepancies_fixed,
                "timestamp": datetime.now(IST).strftime("%H:%M:%S IST"),
            }

            if discrepancies_fixed > 0 or is_startup:
                logger.info(
                    f"[BROKER_RECONCILE] Pass complete (startup={is_startup}): "
                    f"Reconciled {reconciled_trades} trade(s), fixed {discrepancies_fixed} discrepancy(ies)."
                )

            return summary

        except Exception as exc:
            logger.error(f"[BROKER_RECONCILE] Error during reconciliation pass: {exc}", exc_info=True)
            return {"status": "error", "error": str(exc)}
        finally:
            db.close()


_broker_reconciler_instance: Optional[BrokerReconciler] = None


def get_broker_reconciler() -> BrokerReconciler:
    global _broker_reconciler_instance
    if _broker_reconciler_instance is None:
        _broker_reconciler_instance = BrokerReconciler()
    return _broker_reconciler_instance


def init_broker_reconciler(confirm_fill_fn: Callable = None, ws_manager=None) -> BrokerReconciler:
    global _broker_reconciler_instance
    _broker_reconciler_instance = BrokerReconciler(confirm_fill_fn=confirm_fill_fn, ws_manager=ws_manager)
    return _broker_reconciler_instance
