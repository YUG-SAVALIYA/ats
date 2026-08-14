"""
app/core/reconciler.py
======================
Background worker that polls Dhan order status for pending entry orders and confirms fills.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

import pytz

from app.database import SessionLocal
from app.models import Trade, AtsOrder, AtsTradeState, OrderPurpose
from app.services.dhan_client import get_dhan_client

logger = logging.getLogger("ats.reconciler")
IST = pytz.timezone("Asia/Kolkata")

_POLL_INTERVAL = 5.0        # seconds between reconciliation cycles
_DHAN_ORDER_URL = "https://api.dhan.co/v2/orders/{order_id}"


def _is_market_hours() -> bool:
    """Return True if current IST time is within market hours (9:15–15:35 Mon–Fri)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 10) <= t <= (15 * 60 + 35)


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
        """Infinite reconciliation loop. Call as asyncio.create_task(reconciler.run())."""
        self._running = True
        logger.info("[RECONCILER] Order reconciler started")
        while self._running:
            try:
                if _is_market_hours():
                    await self._reconcile_cycle()
                # Outside market hours: just sleep, do nothing
            except Exception as exc:
                logger.error(f"[RECONCILER] Cycle error: {exc}", exc_info=True)
            await asyncio.sleep(_POLL_INTERVAL)

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
        finally:
            db.close()

        try:
            client = get_dhan_client()
            url = f"https://api.dhan.co/v2/orders/{dhan_order_id}"
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

            logger.debug(
                f"[RECONCILER] trade={trade.id} order={dhan_order_id} "
                f"orderStatus={order_status} filledQty={filled_qty} avgTradedPrice={avg_price}"
            )

            if order_status in ("TRADED", "PART_TRADED") and filled_qty > 0 and avg_price > 0:
                await self._on_fill(trade, filled_qty, avg_price, entry_order.id)

            elif order_status in ("CANCELLED", "REJECTED", "EXPIRED", "INACTIVE"):
                await self._on_rejected(trade, entry_order.id, order_status)

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
                f"[RECONCILER] Subscribed sec {updated_trade.security_id} "
                f"to WS after fill for trade {trade.id}"
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
