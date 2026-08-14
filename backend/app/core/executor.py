"""
app/core/executor.py
====================
Production-grade MARKET BUY / MARKET SELL order placement enforcing atomic Two-Phase Exit-Claim
and Dhan-as-Source-of-Truth architecture.
"""

from __future__ import annotations

import logging
import uuid
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.database import SessionLocal
from app.models import AtsOrder, Trade, TradeEvent, AtsTradeState, OrderPurpose, Company, OrderAttempt
from app.services.dhan_client import get_dhan_client, is_error_response

logger = logging.getLogger("ats.executor")

_DHAN_ORDERS_URL = "https://api.dhan.co/v2/orders"


def _r2(v: float) -> float:
    return round(v, 2)


def _correlation_id(trade_id: str, suffix: str) -> str:
    """Build a Dhan-compatible correlation ID with unique attempt nonce (max 25 chars)."""
    short = trade_id.replace("-", "")[:10].upper()
    nonce = hex(int(time.time() * 1000))[-4:].upper()
    return f"ATS-{short}-{suffix}-{nonce}"[:25]


def _log_event(db, trade_id: str, event_type: str, detail: str = "",
               price: float | None = None, quantity: int | None = None) -> None:
    try:
        db.add(TradeEvent(
            trade_id=trade_id, event_type=event_type,
            detail=detail, price=price, quantity=quantity,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as exc:
        logger.warning(f"[EXEC] Event log failed ({event_type}) for {trade_id}: {exc}")
        try:
            db.rollback()
        except Exception:
            pass


async def place_market_sell(
    trade_id: str,
    security_id: str,
    qty: int,
    purpose: str,
    tag: str = "ATS_SELL",
    product_type: Optional[str] = None,
) -> AtsOrder:
    """
    Two-Phase Atomic DB Exit-Claim Protocol:
    
    Phase 1 (Atomic DB Claim < 2ms):
      Locks trade row via WITH FOR UPDATE, checks active status, transitions trade to EXIT_REQUESTED,
      creates AtsOrder(status="CLAIMED") and OrderAttempt(status="INITIATED"), commits DB transaction.
      DB lock is released BEFORE making any HTTP network calls!
      
    Phase 2 (External HTTP Call):
      Sends MARKET SELL to Dhan API.
      - Dhan Accepted: Updates AtsOrder.status = "TRANSIT", OrderAttempt.status = "ACCEPTED".
      - Dhan Rejected: Updates AtsOrder.status = "REJECTED", trade.ats_state = EXIT_FAILED, OrderAttempt.status = "REJECTED".
      - Network Timeout: Updates AtsOrder.status = "UNKNOWN", trade.ats_state = EXIT_UNKNOWN, OrderAttempt.status = "UNKNOWN".
    """
    if qty <= 0:
        raise ValueError(f"[EXEC] Cannot place SELL for qty={qty} on trade {trade_id}")

    purpose_str = purpose.value if hasattr(purpose, "value") else str(purpose)

    # ═════════════════════════════════════════════════════════════════════════
    # PHASE 1: ATOMIC DB EXIT-CLAIM (< 2ms DB Transaction)
    # ═════════════════════════════════════════════════════════════════════════
    db1 = SessionLocal()
    order_rec_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())
    correlation_id = _correlation_id(trade_id, purpose_str[:3].upper())

    try:
        if not product_type or product_type == "AUTO":
            company = db1.query(Company).filter(Company.dhan_security_id == str(security_id)).first()
            if company and company.is_mtf:
                product_type = "MTF"
            else:
                product_type = "CNC"
        else:
            product_type = product_type.upper()

        # Lock trade row atomically via FOR UPDATE
        trade = db1.query(Trade).filter(Trade.id == trade_id).with_for_update().first()
        if not trade:
            raise RuntimeError(f"Trade {trade_id} not found in DB")

        trade_state_str = str(trade.ats_state.value if hasattr(trade.ats_state, "value") else trade.ats_state)
        
        # Check active exit orders including PARTIALLY_FILLED
        active_exit_order = (
            db1.query(AtsOrder)
            .filter(
                AtsOrder.trade_id == trade_id,
                AtsOrder.transaction_type == "SELL",
                AtsOrder.status.in_(["CLAIMED", "PENDING", "TRANSIT", "PLACED", "PARTIALLY_FILLED"]),
            )
            .first()
        )

        if trade_state_str == "CLOSED" or active_exit_order:
            status_desc = active_exit_order.status if active_exit_order else trade_state_str
            logger.warning(f"[EXEC] Duplicate SELL blocked for trade {trade_id}: active order/state={status_desc}")
            if active_exit_order:
                db1.expunge(active_exit_order)
                return active_exit_order
            raise RuntimeError(f"Trade {trade_id} is in state {trade_state_str}")

        remaining = trade.remaining_quantity or trade.allocated_quantity or 0
        if remaining <= 0:
            raise RuntimeError(f"Trade {trade_id} has remaining_qty={remaining} — nothing to sell")
        qty = min(qty, remaining)

        # Transition trade to EXIT_REQUESTED on DB claim!
        trade.ats_state = AtsTradeState.EXIT_REQUESTED
        trade.exit_reason = purpose_str

        # Create persistent AtsOrder in CLAIMED status
        order_rec = AtsOrder(
            id=order_rec_id,
            trade_id=trade_id,
            dhan_order_id=None,
            correlation_id=correlation_id,
            order_purpose=purpose_str,
            transaction_type="SELL",
            security_id=str(security_id),
            quantity=int(qty),
            price=None,
            order_type="MARKET",
            product_type=product_type,
            exchange_segment="NSE_EQ",
            status="CLAIMED",
            fill_qty=0,
            placed_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db1.add(order_rec)

        # Create Audit OrderAttempt in INITIATED status
        attempt = OrderAttempt(
            id=attempt_id,
            trade_id=trade_id,
            ats_order_id=order_rec.id,
            correlation_id=correlation_id,
            order_purpose=purpose_str,
            transaction_type="SELL",
            requested_quantity=int(qty),
            endpoint=_DHAN_ORDERS_URL,
            request_payload={
                "securityId": str(security_id),
                "exchangeSegment": "NSE_EQ",
                "transactionType": "SELL",
                "quantity": int(qty),
                "orderType": "MARKET",
                "productType": product_type,
                "price": 0,
                "tag": correlation_id,
            },
            status="INITIATED",
            created_at=datetime.now(timezone.utc),
        )
        db1.add(attempt)
        db1.commit()

        _log_event(db1, trade_id, "EXIT_CLAIMED",
                   f"Phase 1 Exit-Claim committed: qty={qty}, purpose={purpose_str}, correlation_id={correlation_id}",
                   quantity=qty)

        logger.info(f"[EXEC] Phase 1 Exit-Claim committed for trade {trade_id} (tag={correlation_id})")

    except Exception as exc:
        db1.rollback()
        logger.error(f"[EXEC] Phase 1 Exit-Claim failed for trade {trade_id}: {exc}")
        raise exc
    finally:
        db1.close()

    # ═════════════════════════════════════════════════════════════════════════
    # PHASE 2: EXTERNAL BROKER API CALL (Outside DB Transaction)
    # ═════════════════════════════════════════════════════════════════════════
    client = get_dhan_client()

    db2 = SessionLocal()
    try:
        att = db2.query(OrderAttempt).filter(OrderAttempt.id == attempt_id).first()
        if att:
            att.status = "REQUEST_SENT"
            db2.commit()
    except Exception:
        pass
    finally:
        db2.close()

    try:
        payload = {
            "dhanClientId": str(client.client_id or ""),
            "correlationId": str(correlation_id),
            "transactionType": "SELL",
            "exchangeSegment": "NSE_EQ",
            "productType": product_type,
            "orderType": "MARKET",
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": int(qty),
            "price": 0,           # Required by Dhan API v2 — must be 0 for MARKET orders
            "tag": correlation_id,
        }
        res = client.execute_v2_post(_DHAN_ORDERS_URL, payload)
    except Exception as net_err:
        # Network Timeout / Connection Failure
        logger.error(f"[EXEC] Network/Timeout error sending SELL for trade={trade_id}: {net_err}")
        db_err = SessionLocal()
        try:
            ord_item = db_err.query(AtsOrder).filter(AtsOrder.id == order_rec_id).first()
            tr_item = db_err.query(Trade).filter(Trade.id == trade_id).first()
            att_item = db_err.query(OrderAttempt).filter(OrderAttempt.id == attempt_id).first()

            if ord_item:
                ord_item.status = "UNKNOWN"
                ord_item.last_error = str(net_err)
                ord_item.error_count = (ord_item.error_count or 0) + 1
                ord_item.updated_at = datetime.now(timezone.utc)
            if tr_item:
                tr_item.ats_state = AtsTradeState.EXIT_UNKNOWN
            if att_item:
                att_item.status = "UNKNOWN"
                att_item.error_message = str(net_err)

            db_err.commit()
            _log_event(db_err, trade_id, "EXIT_ORDER_UNKNOWN", f"Network timeout/error during SELL: {net_err}", quantity=qty)
            if ord_item:
                db_err.expunge(ord_item)
                return ord_item
        finally:
            db_err.close()

        ret_ord = AtsOrder(id=order_rec_id, trade_id=trade_id, correlation_id=correlation_id, order_purpose=purpose_str, transaction_type="SELL", security_id=str(security_id), quantity=int(qty), order_type="MARKET", product_type=product_type, exchange_segment="NSE_EQ", status="UNKNOWN")
        return ret_ord

    # ── Process Dhan API Response ────────────────────────────────────────────
    db_resp = SessionLocal()
    try:
        ord_item = db_resp.query(AtsOrder).filter(AtsOrder.id == order_rec_id).first()
        tr_item = db_resp.query(Trade).filter(Trade.id == trade_id).first()
        att_item = db_resp.query(OrderAttempt).filter(OrderAttempt.id == attempt_id).first()

        if att_item:
            att_item.response_body = res if isinstance(res, dict) else {"raw": str(res)}

        if is_error_response(res) or str(res.get("status", "")).lower() in ("failure", "error"):
            remarks = res.get("remarks") or res
            err_msg = str(remarks.get("error_message") if isinstance(remarks, dict) else remarks)
            err_code = str(res.get("errorCode") or res.get("code") or "REJECTED")

            logger.error(f"[EXEC] Dhan rejected SELL: trade={trade_id} err={err_msg}")
            if ord_item:
                ord_item.status = "REJECTED"
                ord_item.last_error = err_msg
                ord_item.error_count = (ord_item.error_count or 0) + 1
                ord_item.updated_at = datetime.now(timezone.utc)
            if tr_item:
                tr_item.ats_state = AtsTradeState.EXIT_FAILED
            if att_item:
                att_item.status = "REJECTED"
                att_item.error_code = err_code
                att_item.error_message = err_msg

            db_resp.commit()
            _log_event(db_resp, trade_id, "EXIT_ORDER_REJECTED", err_msg, quantity=qty)
            if ord_item:
                db_resp.expunge(ord_item)
                return ord_item
            ret_ord = AtsOrder(id=order_rec_id, trade_id=trade_id, correlation_id=correlation_id, order_purpose=purpose_str, transaction_type="SELL", security_id=str(security_id), quantity=int(qty), order_type="MARKET", product_type=product_type, exchange_segment="NSE_EQ", status="REJECTED")
            return ret_ord

        # ── Dhan Accepted Order ──────────────────────────────────────────────
        data = res.get("data", {}) if isinstance(res.get("data"), dict) else res
        dhan_order_id = str(data.get("orderId") or res.get("orderId") or f"UNK-{uuid.uuid4()}")

        if ord_item:
            ord_item.dhan_order_id = dhan_order_id
            ord_item.status = "TRANSIT"
            ord_item.updated_at = datetime.now(timezone.utc)

        if tr_item:
            tr_item.ats_state = AtsTradeState.EXIT_REQUESTED

        if att_item:
            att_item.status = "ACCEPTED"
            att_item.dhan_order_id = dhan_order_id
            att_item.response_status_code = 200

        db_resp.commit()
        _log_event(db_resp, trade_id, "EXIT_ORDER_ACCEPTED", f"MARKET SELL accepted by Dhan: dhan_order_id={dhan_order_id}", quantity=qty)

        logger.info(f"[EXEC] MARKET SELL accepted by Dhan: trade={trade_id}, qty={qty}, dhan_order_id={dhan_order_id}")

        if ord_item:
            db_resp.expunge(ord_item)
            return ord_item

        ret_ord = AtsOrder(id=order_rec_id, trade_id=trade_id, dhan_order_id=dhan_order_id, correlation_id=correlation_id, order_purpose=purpose_str, transaction_type="SELL", security_id=str(security_id), quantity=int(qty), order_type="MARKET", product_type=product_type, exchange_segment="NSE_EQ", status="TRANSIT")
        return ret_ord

    finally:
        db_resp.close()


def confirm_exit_fill(
    trade_id: str,
    fill_price: float,
    fill_qty: int,
    dhan_order_id: Optional[str] = None,
    purpose: Optional[str] = None,
) -> Optional[Trade]:
    """
    Transactionally Locked & Idempotent Fill-Delta Processing:
    
    1. Locks Trade & AtsOrder rows via WITH FOR UPDATE.
    2. Calculates delta_qty = fill_qty - ats_order.fill_qty.
    3. If delta_qty <= 0: Idempotent duplicate fill, aborts transaction without mutation.
    4. If delta_qty > 0: Deducts delta_qty from trade.remaining_quantity.
    5. Sets partial_exit_completed = True strictly when Dhan confirms PARTIAL_EXIT fill.
    """
    new_total_fill_qty = fill_qty
    if fill_price <= 0 or new_total_fill_qty <= 0:
        logger.error(f"[EXEC] Invalid fill details for trade {trade_id}: price={fill_price}, qty={new_total_fill_qty}")
        return None

    db = SessionLocal()
    try:
        trade = db.query(Trade).filter(Trade.id == trade_id).with_for_update().first()
        if not trade:
            logger.error(f"[EXEC] confirm_exit_fill: trade {trade_id} not found in DB")
            return None

        query = db.query(AtsOrder).filter(
            AtsOrder.trade_id == trade_id,
            AtsOrder.transaction_type == "SELL",
        )
        if dhan_order_id:
            query = query.filter(AtsOrder.dhan_order_id == dhan_order_id)

        exit_order = query.order_by(AtsOrder.created_at.desc()).with_for_update().first()
        
        current_order_fill = exit_order.fill_qty if exit_order and exit_order.fill_qty else 0
        delta_qty = new_total_fill_qty - current_order_fill

        if delta_qty <= 0:
            logger.info(f"[EXEC] Idempotent fill confirmation: trade {trade_id} already processed fill_qty={current_order_fill}. Aborting.")
            db.rollback()
            return trade

        # Update AtsOrder fill metrics
        if exit_order:
            exit_order.fill_price = fill_price
            exit_order.fill_qty = new_total_fill_qty
            exit_order.status = "FILLED" if new_total_fill_qty >= exit_order.quantity else "PARTIALLY_FILLED"
            exit_order.filled_at = datetime.now(timezone.utc)
            exit_order.updated_at = datetime.now(timezone.utc)

        current_rem = trade.remaining_quantity or trade.allocated_quantity or 0
        new_rem = max(0, current_rem - delta_qty)
        entry_p = trade.entry_price or fill_price

        order_purpose_str = exit_order.order_purpose if exit_order else (purpose or "")

        if new_rem > 0 and new_rem < (trade.allocated_quantity or (new_rem + delta_qty)):
            trade.partial_exit_completed = True

        if new_rem == 0:
            # ── Trade Fully Closed ──────────────────────────────────────────
            trade.remaining_quantity = 0
            trade.exit_price = fill_price
            trade.exit_qty = trade.allocated_quantity or new_total_fill_qty
            trade.exit_pct = _r2(((fill_price - entry_p) / entry_p) * 100) if entry_p else 0.0
            trade.realized_pnl = _r2((fill_price - entry_p) * (trade.allocated_quantity or new_total_fill_qty)) if entry_p else 0.0
            
            from app.core.state_machine import validate_state_transition
            if not validate_state_transition(trade, AtsTradeState.CLOSED):
                db.rollback()
                return None
                
            trade.trade_status = "CLOSED"
            trade.closed_at = datetime.now(timezone.utc)

            _log_event(db, trade_id, "EXIT_ORDER_FILLED", f"Exit order filled @ ₹{fill_price} (delta={delta_qty}). Trade CLOSED.", price=fill_price, quantity=delta_qty)
            _log_event(db, trade_id, "TRADE_CLOSED", f"Trade CLOSED with confirmed broker fill price=₹{fill_price}, P&L=₹{trade.realized_pnl}", price=fill_price, quantity=delta_qty)
            logger.info(f"[EXEC] Trade {trade_id} CLOSED on broker fill confirmation: fill_price=₹{fill_price}, P&L=₹{trade.realized_pnl}")

        else:
            # ── Partial Exit Confirmed ──────────────────────────────────────
            trade.remaining_quantity = new_rem
            
            from app.core.state_machine import validate_state_transition
            if not validate_state_transition(trade, AtsTradeState.PARTIAL_EXIT):
                db.rollback()
                return None

            _log_event(db, trade_id, "EXIT_ORDER_PARTIALLY_FILLED", f"Exit order fill delta={delta_qty} @ ₹{fill_price}. Remaining={new_rem}.", price=fill_price, quantity=delta_qty)
            logger.info(f"[EXEC] Trade {trade_id} PARTIAL_EXIT confirmed: delta={delta_qty} @ ₹{fill_price}, remaining={new_rem}")

        db.commit()
        db.refresh(trade)
        return trade

    except Exception as exc:
        logger.error(f"[EXEC] confirm_exit_fill failed for trade {trade_id}: {exc}", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        db.close()


class OrderExecutor:
    """Handles placing entry MARKET BUY orders and confirming fills."""

    def __init__(self):
        from app.services.dhan_client import get_dhan_client
        self.client = get_dhan_client()

    def place_entry_order(
        self,
        *,
        security_id: str,
        trading_symbol: str,
        company_id: str,
        signal_id: Optional[str],
        quantity: int,
        allocated_capital: float,
        exchange_segment: str = "NSE_EQ",
        product_type: Optional[str] = None,
    ) -> dict:
        """Place a MARKET BUY entry order on Dhan (POST /v2/orders)."""
        from datetime import date

        db = SessionLocal()
        try:
            trade_id = str(uuid.uuid4())
            correlation_id = _correlation_id(trade_id, "E")

            if not product_type or product_type == "AUTO":
                company = db.query(Company).filter(Company.id == company_id).first()
                if not company:
                    company = db.query(Company).filter(Company.dhan_security_id == str(security_id)).first()
                if company and company.is_mtf:
                    product_type = "MTF"
                else:
                    product_type = "CNC"

            trade = Trade(
                id=trade_id,
                company_id=company_id,
                signal_id=signal_id,
                security_id=str(security_id),
                trade_date=date.today(),
                allocated_capital=_r2(allocated_capital),
                allocated_quantity=quantity,
                remaining_quantity=quantity,
                entry_price=None,
                entry_value=None,
                target_pct=17.0,
                stoploss_pct=5.0,
                target1_price=None,
                target2_price=None,
                stop_price=None,
                partial_exit_completed=False,
                gap_detected=False,
                ats_state=AtsTradeState.ENTRY_PENDING,
                trade_status="OPEN",
                sl_stage=0,
                created_at=datetime.now(timezone.utc),
                executed_at=datetime.now(timezone.utc),
            )
            db.add(trade)
            db.flush()

            order_rec = AtsOrder(
                id=str(uuid.uuid4()),
                trade_id=trade_id,
                dhan_order_id=None,
                correlation_id=correlation_id,
                order_purpose=OrderPurpose.ENTRY,
                transaction_type="BUY",
                security_id=str(security_id),
                quantity=quantity,
                price=None,
                order_type="MARKET",
                product_type=product_type.upper(),
                exchange_segment=exchange_segment.upper(),
                status="PENDING",
                fill_qty=0,
                placed_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            db.add(order_rec)

            attempt = OrderAttempt(
                id=str(uuid.uuid4()),
                trade_id=trade_id,
                ats_order_id=order_rec.id,
                correlation_id=correlation_id,
                order_purpose="ENTRY",
                transaction_type="BUY",
                requested_quantity=quantity,
                endpoint=_DHAN_ORDERS_URL,
                request_payload={
                    "securityId": str(security_id),
                    "exchangeSegment": exchange_segment.upper(),
                    "transactionType": "BUY",
                    "quantity": int(quantity),
                    "orderType": "MARKET",
                    "productType": product_type.upper(),
                    "price": 0,
                    "tag": correlation_id,
                },
                status="INITIATED",
                created_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
            db.commit()

            _log_event(db, trade_id, "ENTRY_ORDER_REQUESTED", f"MARKET BUY {quantity} @ MARKET for sec {security_id}", quantity=quantity)

            payload = {
                "dhanClientId": str(self.client.client_id or ""),
                "correlationId": str(correlation_id),
                "transactionType": "BUY",
                "exchangeSegment": exchange_segment.upper(),
                "productType": product_type.upper(),
                "orderType": "MARKET",
                "validity": "DAY",
                "securityId": str(security_id),
                "quantity": int(quantity),
                "price": 0,           # Required by Dhan API v2 — must be 0 for MARKET orders
                "tag": correlation_id,
            }
            res = self.client.execute_v2_post(_DHAN_ORDERS_URL, payload)

            if is_error_response(res) or str(res.get("status", "")).lower() in ("failure", "error"):
                remarks = res.get("remarks") or res
                err_msg = str(remarks.get("error_message") if isinstance(remarks, dict) else remarks)
                logger.error(f"[EXEC] Dhan rejected entry: trade={trade_id} err={err_msg}")
                order_rec.status = "FAILED"
                order_rec.last_error = err_msg
                order_rec.error_count = (order_rec.error_count or 0) + 1
                order_rec.updated_at = datetime.now(timezone.utc)
                trade.ats_state = AtsTradeState.FAILED
                trade.trade_status = "CANCELLED"
                attempt.status = "REJECTED"
                attempt.error_message = err_msg
                attempt.response_body = res if isinstance(res, dict) else {"raw": str(res)}
                db.commit()
                _log_event(db, trade_id, "ENTRY_REJECTED", err_msg, quantity=quantity)
                return {"status": "failed", "trade_id": trade_id, "error": err_msg}

            data = res.get("data", {}) if isinstance(res.get("data"), dict) else res
            dhan_order_id = str(data.get("orderId") or res.get("orderId") or f"UNK-{uuid.uuid4()}")
            order_rec.dhan_order_id = dhan_order_id
            order_rec.status = "PLACED"
            order_rec.updated_at = datetime.now(timezone.utc)
            attempt.status = "ACCEPTED"
            attempt.dhan_order_id = dhan_order_id
            attempt.response_status_code = 200
            attempt.response_body = res if isinstance(res, dict) else {"raw": str(res)}
            db.commit()

            logger.info(f"[EXEC] Entry MARKET BUY placed: trade={trade_id}, qty={quantity}, dhan_order_id={dhan_order_id}")
            return {"status": "placed", "trade_id": trade_id, "order_rec_id": order_rec.id, "dhan_order_id": dhan_order_id}

        except Exception as exc:
            logger.error(f"[EXEC] Unexpected error placing entry for sec {security_id}: {exc}", exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
            return {"status": "failed", "error": str(exc)}
        finally:
            db.close()

    def confirm_entry_fill(self, trade_id: str, fill_price: float, fill_qty: int) -> Optional[Trade]:
        """Called when Dhan confirms entry order was filled."""
        from app.core.levels import compute_initial_levels

        db = SessionLocal()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).with_for_update().first()
            if not trade:
                return None
            if trade.ats_state not in (AtsTradeState.ENTRY_PENDING, AtsTradeState.OPEN):
                return None

            entry_order = db.query(AtsOrder).filter(AtsOrder.trade_id == trade_id, AtsOrder.order_purpose == OrderPurpose.ENTRY).first()
            if entry_order and entry_order.status == "FILLED":
                logger.debug(f"[EXEC] Idempotent entry fill ignored: trade {trade_id} already FILLED.")
                return trade

            levels = compute_initial_levels(fill_price)
            trade.entry_price = fill_price
            trade.entry_value = _r2(fill_price * fill_qty)
            trade.allocated_quantity = fill_qty
            trade.remaining_quantity = fill_qty
            trade.stop_price = levels["stop_price"]
            trade.target1_price = levels["target1_price"]
            trade.target2_price = levels["target2_price"]
            trade.sl_stage = 0
            
            from app.core.state_machine import validate_state_transition
            if not validate_state_transition(trade, AtsTradeState.OPEN):
                return None
                
            trade.executed_at = datetime.now(timezone.utc)

            if entry_order:
                entry_order.fill_price = fill_price
                entry_order.fill_qty = fill_qty
                entry_order.status = "FILLED"
                entry_order.filled_at = datetime.now(timezone.utc)
                entry_order.updated_at = datetime.now(timezone.utc)

            db.commit()
            db.refresh(trade)
            _log_event(db, trade_id, "ENTRY_FILLED", f"fill_price={fill_price}, fill_qty={fill_qty}.", price=fill_price, quantity=fill_qty)
            return trade
        finally:
            db.close()

    def confirm_exit_fill(self, trade_id: str, fill_price: float, new_total_fill_qty: int, dhan_order_id: Optional[str] = None, purpose: Optional[str] = None) -> Optional[Trade]:
        return confirm_exit_fill(trade_id, fill_price, new_total_fill_qty, dhan_order_id, purpose)

    def cancel_order(self, dhan_order_id: str) -> dict:
        url = f"https://api.dhan.co/v2/orders/{dhan_order_id}"
        res = self.client.execute_v2_delete(url)
        order_status = str(res.get("orderStatus") or res.get("status") or "").upper()
        success = order_status in ("CANCELLED",) or res.get("status") == "accepted"

        if success:
            db = SessionLocal()
            try:
                ats_ord = db.query(AtsOrder).filter(AtsOrder.dhan_order_id == dhan_order_id).first()
                if ats_ord:
                    ats_ord.status = "CANCELLED"
                    ats_ord.updated_at = datetime.now(timezone.utc)
                    db.commit()
            finally:
                db.close()

        return {"success": success, "dhan_order_id": dhan_order_id, "order_status": order_status, "raw": res}

    def cancel_pending_entry(self, trade_id: str, reason: str = "MANUAL_CANCEL") -> dict:
        db = SessionLocal()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).first()
            if not trade or trade.ats_state != AtsTradeState.ENTRY_PENDING:
                return {"status": "invalid_state", "trade_id": trade_id}

            entry_order = db.query(AtsOrder).filter(AtsOrder.trade_id == trade_id, AtsOrder.order_purpose == OrderPurpose.ENTRY).first()
            cancel_result = {"success": False}
            if entry_order and entry_order.dhan_order_id:
                cancel_result = self.cancel_order(entry_order.dhan_order_id)

            if cancel_result.get("success"):
                t = db.query(Trade).filter(Trade.id == trade_id).with_for_update().first()
                if t:
                    t.ats_state = AtsTradeState.CANCELLED
                    t.trade_status = "CANCELLED"
                    t.exit_reason = reason
                    t.closed_at = datetime.now(timezone.utc)
                db.commit()
                return {"status": "cancelled", "trade_id": trade_id}
            return {"status": "cancel_failed", "trade_id": trade_id}
        finally:
            db.close()


_executor_instance: Optional[OrderExecutor] = None

def get_order_executor() -> OrderExecutor:
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = OrderExecutor()
    return _executor_instance
