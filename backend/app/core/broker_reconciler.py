"""
app/core/broker_reconciler.py
==============================
Production-Grade Broker Reconciliation Engine for ATS Backend.

Provides 3-way synchronization between:
1. Dhan Live Broker API (`GET /v2/positions` & `GET /v2/orders`)
2. PostgreSQL Database (`trades`, `ats_orders`, `positions`)
3. In-Memory Execution Cache (`TradeCacheManager`)

Schedules & Triggers:
- Startup Reconciliation: Runs immediately on backend startup.
- Runtime Reconciliation: Periodic background worker running every 30 seconds.
- Post-Execution Reconciliation: Called on demand after order execution.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any, List

import pytz

from app.database import SessionLocal
from app.models import Trade, AtsOrder, AtsTradeState, OrderPurpose, TradeEvent
from app.services.dhan_client import get_dhan_client, is_empty_portfolio_response
from app.services.portfolio import PortfolioService
from app.core.cache_manager import get_cache_manager, TradeCacheManager

logger = logging.getLogger("ats.broker_reconciler")
IST = pytz.timezone("Asia/Kolkata")

_RECONCILE_INTERVAL_SEC = 30.0  # Periodic reconciliation every 30s


def _log_event(db, trade_id: str, event_type: str, detail: str = "") -> None:
    try:
        db.add(TradeEvent(
            trade_id=trade_id,
            event_type=event_type,
            detail=detail,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as exc:
        logger.warning(f"[BROKER_RECONCILE] Audit event log failed: {exc}")


class BrokerReconciler:
    """
    Manages broker reconciliation between Dhan API, PostgreSQL DB, and memory cache.
    """

    def __init__(self, confirm_fill_fn: Callable = None, ws_manager = None):
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
        """
        Executes one full 3-way reconciliation pass.
        """
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

                # ── A. Handle ENTRY_PENDING orders ───────────────────────────
                if state == "ENTRY_PENDING":
                    entry_order = (
                        db.query(AtsOrder)
                        .filter(AtsOrder.trade_id == tid, AtsOrder.order_purpose == OrderPurpose.ENTRY)
                        .first()
                    )
                    if entry_order and entry_order.dhan_order_id:
                        try:
                            client = get_dhan_client()
                            url = f"https://api.dhan.co/v2/orders/{entry_order.dhan_order_id}"
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

                # ── B. Handle EXIT_REQUESTED & EXIT_UNKNOWN trade states ─────
                elif state in ("EXIT_REQUESTED", "EXIT_UNKNOWN", "EXIT_FAILED"):
                    exit_order = (
                        db.query(AtsOrder)
                        .filter(AtsOrder.trade_id == tid, AtsOrder.transaction_type == "SELL")
                        .order_by(AtsOrder.created_at.desc())
                        .first()
                    )
                    
                    # ── Grace Period Check for CLAIMED / PENDING orders ───────
                    if exit_order and not exit_order.dhan_order_id:
                        placed_time = exit_order.placed_at or exit_order.created_at
                        if placed_time:
                            if placed_time.tzinfo is None:
                                placed_time = placed_time.replace(tzinfo=timezone.utc)
                            elapsed_sec = (datetime.now(timezone.utc) - placed_time).total_seconds()
                            if elapsed_sec < 30.0:
                                logger.debug(f"[BROKER_RECONCILE] Trade {tid} exit order claimed {elapsed_sec:.1f}s ago (< 30s grace period). Skipping.")
                                continue
                            else:
                                # VERIFYING_BROKER Crash Recovery Phase
                                logger.error(f"[BROKER_RECONCILE] CRASH RECOVERY: Trade {tid} stuck in {state} with order {exit_order.id} for {elapsed_sec:.1f}s without dhan_order_id. Entering VERIFYING_BROKER phase.")
                                
                                broker_qty = broker_pos_by_sec.get(sec_id, 0)
                                if broker_qty <= 0:
                                    # Broker says position is closed! We sent the request and crashed before getting dhan_order_id.
                                    logger.info(f"[BROKER_RECONCILE] Dhan net_qty=0 for trade {tid}. Marking CLOSED from VERIFYING_BROKER phase.")
                                    
                                    from app.core.state_machine import validate_state_transition
                                    if validate_state_transition(trade, AtsTradeState.CLOSED):
                                        trade.trade_status = "CLOSED"
                                        trade.remaining_quantity = 0
                                        trade.closed_at = datetime.now(timezone.utc)
                                        db.commit()
                                        self.cache_manager.remove_trade(tid)
                                        _log_event(db, tid, "CRASH_RECOVERY_CLOSED", "Trade closed on broker despite missing order ID.")
                                        discrepancies_fixed += 1
                                    continue
                                else:
                                    # Broker says we still have the position! We never sent the request. Revert state to retry.
                                    exit_order.status = "FAILED_CRASH_RECOVERY"
                                    exit_order.updated_at = datetime.now(timezone.utc)
                                    
                                    from app.core.state_machine import validate_state_transition
                                    new_state = AtsTradeState.PARTIAL_EXIT if getattr(trade, 'partial_exit_completed', False) else AtsTradeState.OPEN
                                    if validate_state_transition(trade, new_state):
                                        db.commit()
                                        self.cache_manager.update_trade(trade)
                                        _log_event(db, tid, "CRASH_RECOVERY_REVERT", f"Reverted trade state to {new_state} due to missing dhan_order_id and active broker qty")
                                        discrepancies_fixed += 1
                                    continue

                    if exit_order and exit_order.dhan_order_id:
                        try:
                            client = get_dhan_client()
                            url = f"https://api.dhan.co/v2/orders/{exit_order.dhan_order_id}"
                            res = client.execute_v2_get(url)
                            if isinstance(res, list) and len(res) > 0:
                                res = res[0]

                            if isinstance(res, dict):
                                ord_status = str(res.get("orderStatus") or res.get("status") or "").upper()
                                fill_qty = int(res.get("filledQty") or res.get("tradedQuantity") or 0)
                                fill_price = float(res.get("avgTradedPrice") or res.get("averageTradedPrice") or res.get("tradedPrice") or 0.0)

                                if ord_status in ("TRADED", "FILLED", "PART_TRADED") and fill_qty > 0 and fill_price > 0:
                                    from app.core.executor import confirm_exit_fill
                                    updated_trade = confirm_exit_fill(tid, fill_price, fill_qty, exit_order.dhan_order_id)
                                    if updated_trade and str(getattr(updated_trade.ats_state, "value", updated_trade.ats_state)) not in ("CLOSED", "CANCELLED", "FAILED"):
                                        self.cache_manager.update_trade(updated_trade)
                                    else:
                                        self.cache_manager.remove_trade(tid)
                                    discrepancies_fixed += 1
                                    _log_event(db, tid, "EXIT_RECONCILIATED", f"Confirmed exit fill @ ₹{fill_price} (qty={fill_qty})")
                                    logger.info(f"[BROKER_RECONCILE] Confirmed exit fill for trade {tid} @ ₹{fill_price}")

                                elif ord_status in ("REJECTED", "CANCELLED", "EXPIRED"):
                                    from app.core.state_machine import validate_state_transition
                                    if validate_state_transition(trade, AtsTradeState.EXIT_FAILED):
                                        exit_order.status = ord_status
                                        db.commit()
                                        self.cache_manager.update_trade(trade)
                                        discrepancies_fixed += 1
                                        logger.warning(f"[BROKER_RECONCILE] Exit order for trade {tid} was {ord_status} on broker.")
                        except Exception as exc:
                            logger.warning(f"[BROKER_RECONCILE] Failed to reconcile exit order for {tid}: {exc}")

                    # Fallback net position check for EXIT_REQUESTED/UNKNOWN
                    broker_qty = broker_pos_by_sec.get(sec_id, 0)
                    if broker_qty <= 0:
                        logger.info(f"[BROKER_RECONCILE] Trade {tid} in state {state} has net_qty=0 on Dhan. Marking CLOSED.")
                        from app.core.state_machine import validate_state_transition
                        if validate_state_transition(trade, AtsTradeState.CLOSED):
                            trade.trade_status = "CLOSED"
                            trade.remaining_quantity = 0
                            trade.closed_at = datetime.now(timezone.utc)
                        db.commit()
                        self.cache_manager.remove_trade(tid)
                        _log_event(db, tid, "BROKER_RECONCILED_CLOSED", f"Broker net_qty=0 for trade in {state}")
                        discrepancies_fixed += 1

                # ── C. Handle OPEN & PARTIAL_EXIT positions ──────────────────
                elif state in ("OPEN", "PARTIAL_EXIT"):
                    broker_qty = broker_pos_by_sec.get(sec_id, 0)
                    local_qty = trade.remaining_quantity or trade.allocated_quantity or 0

                    # Case 1: Position is 0 on broker (Closed externally on broker)
                    if broker_qty <= 0:
                        logger.warning(
                            f"[BROKER_RECONCILE] DISCREPANCY DETECTED: Trade {tid} (sec={sec_id}) "
                            f"is {state} locally (qty={local_qty}) but net_qty=0 on Dhan broker! Closing trade."
                        )
                        trade.ats_state = AtsTradeState.CLOSED
                        trade.trade_status = "CLOSED"
                        trade.remaining_quantity = 0
                        trade.exit_reason = "BROKER_RECONCILED_CLOSED"
                        trade.closed_at = datetime.now(timezone.utc)
                        db.commit()

                        self.cache_manager.remove_trade(tid)
                        _log_event(db, tid, "BROKER_DISCREPANCY_CLOSED", f"Closed trade because broker net_qty=0")
                        discrepancies_fixed += 1

                    # Case 2: Broker net_qty is less than local remaining_quantity
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
                        _log_event(db, tid, "BROKER_DISCREPANCY_QTY_UPDATED", f"Updated remaining_quantity from {local_qty} to {broker_qty}")
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


def init_broker_reconciler(confirm_fill_fn: Callable = None, ws_manager = None) -> BrokerReconciler:
    global _broker_reconciler_instance
    _broker_reconciler_instance = BrokerReconciler(confirm_fill_fn=confirm_fill_fn, ws_manager=ws_manager)
    return _broker_reconciler_instance
