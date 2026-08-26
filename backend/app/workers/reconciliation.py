"""
app.workers.reconciliation
==========================
Production-Grade Broker Reconciliation Engine for ATS Backend.

Provides 3-way synchronization between:
1. Dhan Live Broker API (`GET /v2/positions` & `GET /v2/orders`)
2. PostgreSQL Database (`trades`, `ats_orders`, `positions`)
3. In-Memory Execution Cache (`TradeCacheManager`)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, Any, List

import pytz

from app.data.database import SessionLocal
from app.data.models import Trade, AtsOrder, AtsTradeState, OrderPurpose, TradeEvent, DhanAccount
from app.broker.dhan_client import is_empty_portfolio_response, get_account_context
from app.trading.cache import get_cache_manager, TradeCacheManager
from app.trading.state_machine import validate_state_transition
from app.trading.execution import confirm_exit_fill

logger = logging.getLogger("ats.reconciliation")
IST = pytz.timezone("Asia/Kolkata")

_RECONCILE_INTERVAL_SEC = 30.0


def _resolve_account_context(account_id: str):
    import sys
    for mod_name in ("app.trading.execution", "app.broker.dhan_client", "app.workers.reconciliation"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "get_account_context"):
            fn = getattr(mod, "get_account_context")
            from app.broker.dhan_client import get_account_context as default_fn
            if fn is not default_fn:
                return fn(account_id)
    from app.broker.dhan_client import get_account_context
    return get_account_context(account_id)



def _log_event(db, trade_id: str, event_type: str, detail: str = "", dhan_account_id: str | None = None) -> None:
    try:
        if not dhan_account_id and trade_id:
            t = db.query(Trade.dhan_account_id).filter(Trade.id == trade_id).first()
            if t:
                dhan_account_id = t[0]
        if not dhan_account_id:
            logger.warning(f"[BROKER_RECONCILE] Cannot log TradeEvent without dhan_account_id for trade {trade_id}")
            return
        db.add(TradeEvent(
            dhan_account_id=dhan_account_id,
            trade_id=trade_id,
            event_type=event_type,
            detail=detail,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as exc:
        logger.warning(f"[BROKER_RECONCILE] Audit event log failed: {exc}")


class BrokerReconciler:
    """Manages broker reconciliation between Dhan API, PostgreSQL DB, and memory cache."""

    def __init__(self, confirm_fill_fn: Callable = None, ws_manager = None):
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

    def _recover_orphaned_order(self, client, db, ats_order) -> bool:
        if not ats_order.correlation_id:
            return False
            
        try:
            res = client.execute_v2_get("https://api.dhan.co/v2/orders")
            if isinstance(res, list):
                for ord_data in res:
                    if ord_data.get("correlationId") == ats_order.correlation_id or ord_data.get("tag") == ats_order.correlation_id:
                        dhan_order_id = str(ord_data.get("orderId"))
                        ats_order.dhan_order_id = dhan_order_id
                        db.commit()
                        logger.info(f"[BROKER_RECONCILE] Recovered orphaned order {ats_order.id}, found dhan_order_id={dhan_order_id}")
                        return True
        except Exception as exc:
            logger.warning(f"[BROKER_RECONCILE] Failed to fetch orders for orphaned recovery: {exc}")
            
        created_at = ats_order.created_at
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        elapsed_seconds = (now_utc - created_at).total_seconds() if created_at else 999.0
        if elapsed_seconds > 60:
            logger.warning(f"[BROKER_RECONCILE] Orphaned order {ats_order.id} not found on broker after 60s. Marking FAILED.")
            ats_order.status = "FAILED"
            ats_order.last_error = "Orphaned order never reached broker."
            db.commit()
            
        return False


    async def reconcile_cycle(self, is_startup: bool = False) -> Dict[str, Any]:
        """Executes one full 3-way reconciliation pass across ALL active accounts."""
        db = SessionLocal()
        discrepancies_fixed = 0
        reconciled_trades = 0

        try:
            active_accounts = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").all()
            
            if not active_accounts:
                return {"status": "no_active_accounts"}

            for account in active_accounts:
                acc_id = account.id
                client = _resolve_account_context(acc_id)

                try:
                    res = client.execute_v2_get('/positions')
                    live_positions = res.get("data", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
                except Exception as exc:
                    logger.warning(f"[BROKER_RECONCILE] Failed to fetch positions for {acc_id}: {exc}")
                    continue

                broker_pos_by_sec = {}
                if live_positions:
                    for pos in live_positions:
                        sec_id = str(pos.get("securityId", pos.get("security_id", ""))).strip()
                        net_qty = int(pos.get("netQty", pos.get("net_qty", 0)) or 0)
                        if sec_id:
                            broker_pos_by_sec[sec_id] = broker_pos_by_sec.get(sec_id, 0) + net_qty

                active_trades = (
                    db.query(Trade)
                    .filter(Trade.dhan_account_id == acc_id)
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

                reconciled_trades += len(active_trades)

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
                        if entry_order and not entry_order.dhan_order_id:
                            self._recover_orphaned_order(client, db, entry_order)
                            
                        if entry_order and entry_order.dhan_order_id:
                            try:
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
                                            logger.info(f"[BROKER_RECONCILE] Confirmed entry fill {tid} @ ₹{fill_price}")

                                    elif ord_status in ("CANCELLED", "REJECTED", "EXPIRED"):
                                        trade.ats_state = AtsTradeState.CANCELLED
                                        trade.trade_status = "CANCELLED"
                                        entry_order.status = ord_status
                                        db.commit()
                                        self.cache_manager.remove_trade(tid)
                                        discrepancies_fixed += 1
                                        logger.warning(f"[BROKER_RECONCILE] Entry trade {tid} was {ord_status}.")
                            except Exception as exc:
                                logger.warning(f"[BROKER_RECONCILE] Failed to check pending order {tid}: {exc}")
                        elif entry_order and entry_order.status == "FAILED":
                            trade.ats_state = AtsTradeState.FAILED
                            trade.trade_status = "CANCELLED"
                            db.commit()
                            self.cache_manager.remove_trade(tid)
                            discrepancies_fixed += 1
                            logger.warning(f"[BROKER_RECONCILE] Entry trade {tid} was FAILED (orphaned timeout).")

                    # ── B. Handle EXIT_REQUESTED & EXIT_UNKNOWN ──────────────────
                    elif state in ("EXIT_REQUESTED", "EXIT_UNKNOWN", "EXIT_FAILED"):
                        exit_order = (
                            db.query(AtsOrder)
                            .filter(AtsOrder.trade_id == tid, AtsOrder.transaction_type == "SELL")
                            .order_by(AtsOrder.created_at.desc())
                            .first()
                        )

                        if exit_order and not exit_order.dhan_order_id:
                            self._recover_orphaned_order(client, db, exit_order)
                            
                        if exit_order and exit_order.dhan_order_id:
                            try:
                                url = f"https://api.dhan.co/v2/orders/{exit_order.dhan_order_id}"
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
                                        _log_event(db, tid, "EXIT_RECONCILIATED", f"Confirmed exit fill @ ₹{fill_price} (qty={fill_qty})")
                                        logger.info(f"[BROKER_RECONCILE] Confirmed exit fill for trade {tid} @ ₹{fill_price}")

                                    elif ord_status in ("REJECTED", "CANCELLED", "EXPIRED"):
                                        if validate_state_transition(trade, AtsTradeState.EXIT_FAILED):
                                            exit_order.status = ord_status
                                            db.commit()
                                            self.cache_manager.update_trade(trade)
                                            discrepancies_fixed += 1
                                        logger.warning(f"[BROKER_RECONCILE] Exit order for trade {tid} was {ord_status}.")
                            except Exception as exc:
                                logger.warning(f"[BROKER_RECONCILE] Failed to reconcile exit order {tid}: {exc}")
                        
                        elif state == "EXIT_UNKNOWN" and exit_order and exit_order.status == "CLAIMED":
                            pass
                        elif exit_order and exit_order.status == "FAILED":
                            if validate_state_transition(trade, AtsTradeState.EXIT_FAILED):
                                db.commit()
                                self.cache_manager.update_trade(trade)
                                discrepancies_fixed += 1
                                logger.warning(f"[BROKER_RECONCILE] Exit trade {tid} was FAILED (orphaned timeout).")

                        # Fallback net position check
                        broker_qty = broker_pos_by_sec.get(sec_id, 0)
                        if broker_qty <= 0:
                            logger.info(f"[BROKER_RECONCILE] Trade {tid} has net_qty=0 on Dhan. Marking CLOSED.")
                            if validate_state_transition(trade, AtsTradeState.CLOSED):
                                trade.trade_status = "CLOSED"
                                trade.remaining_quantity = 0
                                trade.closed_at = datetime.now(timezone.utc)
                            db.commit()
                            self.cache_manager.remove_trade(tid)
                            _log_event(db, tid, "BROKER_RECONCILED_CLOSED", f"Broker net_qty=0")
                            discrepancies_fixed += 1

                    # ── C. Handle OPEN & PARTIAL_EXIT positions ──────────────────
                    elif state in ("OPEN", "PARTIAL_EXIT"):
                        broker_qty = broker_pos_by_sec.get(sec_id, 0)
                        local_qty = trade.remaining_quantity or trade.allocated_quantity or 0

                        if broker_qty <= 0:
                            logger.warning(
                                f"[BROKER_RECONCILE] DISCREPANCY: Trade {tid} "
                                f"is {state} (qty={local_qty}) but net_qty=0 on Dhan! Closing."
                            )
                            trade.ats_state = AtsTradeState.CLOSED
                            trade.trade_status = "CLOSED"
                            trade.remaining_quantity = 0
                            trade.exit_reason = "BROKER_RECONCILED_CLOSED"
                            trade.closed_at = datetime.now(timezone.utc)
                            db.commit()
                            self.cache_manager.remove_trade(tid)
                            _log_event(db, tid, "BROKER_DISCREPANCY_CLOSED", "Closed trade because broker net_qty=0")
                            discrepancies_fixed += 1

                        elif broker_qty < local_qty:
                            logger.warning(
                                f"[BROKER_RECONCILE] DISCREPANCY: Trade {tid} "
                                f"qty ({local_qty}) > broker net_qty ({broker_qty}). Updating."
                            )
                            trade.remaining_quantity = broker_qty
                            trade.ats_state = AtsTradeState.PARTIAL_EXIT
                            trade.partial_exit_completed = True
                            db.commit()
                            self.cache_manager.update_trade(trade)
                            _log_event(db, tid, "BROKER_DISCREPANCY_QTY_UPDATED", f"Updated qty from {local_qty} to {broker_qty}")
                            discrepancies_fixed += 1

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
