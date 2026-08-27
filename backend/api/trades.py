"""
api/trades.py — Trade Execution, Engine Control & Audit Log Endpoints
====================================================================
Provides REST endpoints for trade execution and monitoring:
- GET  /api/engine/status
- POST /api/engine/toggle
- GET  /api/engine/trades
- POST /api/engine/cache/rebuild
- POST /api/engine/broker-reconcile
- POST /api/engine/monitor-ltp
- POST /api/trades/manual-entry
- POST /api/trades/{trade_id}/cancel
- POST /api/trades/{trade_id}/exit
- POST /api/trades/exit-by-security
- GET  /api/db/trades
- GET  /api/db/orders
- GET  /api/db/modifications
- GET  /api/db/trade-events
"""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_user
from database.database import get_db, SessionLocal
from database.models import (
    Trade, AtsOrder, TradeOrder, TradeOrderModification,
    TradeEvent, Company, AtsTradeState
)
from dhan.websocket import get_market_feed_manager
from trading.trades import get_cache_manager
from trading.orders import get_order_executor, place_market_sell
from trading.trade_manager import get_trade_engine
from workers.reconciler import get_broker_reconciler

logger = logging.getLogger("ats.api.trades")

router = APIRouter(tags=["Trades"])

_engine_enabled = True


class ManualEntryRequest(BaseModel):
    security_id: str
    quantity: int
    exchange_segment: str = "NSE_EQ"
    product_type: Optional[str] = None
    strategy_type: str = "SUPERTREND"


class ExitBySecurityRequest(BaseModel):
    security_id: str
    purpose: str = "MANUAL_EXIT"


class MonitorLtpRequest(BaseModel):
    security_id: str
    ltp: float


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE STATUS & CONTROL
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/engine/status")
def get_engine_status(current_user: str = Depends(get_current_user)):
    """Status summary for the execution engine, cache, and WebSocket feed."""
    ws = get_market_feed_manager()
    cache_mgr = get_cache_manager()
    engine = get_trade_engine()

    ws_connected = ws.is_connected() if (ws and callable(getattr(ws, "is_connected", None))) else bool(getattr(ws, "is_connected", False))
    active_subs = list(cache_mgr.get_active_security_ids())
    active_count = cache_mgr.get_active_trade_count()

    return {
        "engine_enabled": _engine_enabled,
        "websocket_connected": ws_connected,
        "active_trades_in_cache": active_count,
        "subscribed_security_ids": active_subs,
        "subscribed_count": len(active_subs),
    }


@router.post("/api/engine/toggle")
def toggle_engine(current_user: str = Depends(get_current_user)):
    """Toggle execution engine enabled / paused state."""
    global _engine_enabled
    _engine_enabled = not _engine_enabled
    logger.info(f"[ENGINE] Engine toggled: {'ENABLED' if _engine_enabled else 'PAUSED'}")
    return {"engine_enabled": _engine_enabled}


@router.get("/api/engine/trades")
def get_engine_trades(current_user: str = Depends(get_current_user)):
    """In-memory active trades snapshot."""
    cache_mgr = get_cache_manager()
    return cache_mgr.get_all_snapshots()


@router.post("/api/engine/cache/rebuild")
def rebuild_cache(current_user: str = Depends(get_current_user)):
    """Force rebuild in-memory trade cache from PostgreSQL DB."""
    ws = get_market_feed_manager()
    cache_mgr = get_cache_manager()
    count = cache_mgr.rebuild_cache(ws_manager=ws)
    return {
        "status": "success",
        "message": f"Cache rebuilt from DB: {count} active trades loaded",
        "active_trade_count": count
    }


@router.post("/api/engine/broker-reconcile")
async def trigger_broker_reconciliation(current_user: str = Depends(get_current_user)):
    """Manually trigger 3-way broker reconciliation cycle."""
    reconciler = get_broker_reconciler()
    res = await reconciler.reconcile_cycle()
    return res


@router.post("/api/engine/monitor-ltp")
async def monitor_ltp(req: MonitorLtpRequest, current_user: str = Depends(get_current_user)):
    """Manually simulate a tick for a security ID."""
    engine = get_trade_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="Trade engine not initialized")
    await engine.on_tick(req.security_id, req.ltp)
    return {"status": "ok", "security_id": req.security_id, "ltp": req.ltp}


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE EXECUTION & MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/trades/manual-entry")
def place_manual_entry(req: ManualEntryRequest, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Places a manual MARKET BUY entry order."""
    comp = db.query(Company).filter(Company.dhan_security_id == str(req.security_id)).first()
    if not comp:
        raise HTTPException(status_code=404, detail=f"Company with security_id {req.security_id} not found")

    executor = get_order_executor()
    allocated_capital = float(req.quantity) * 1000.0  # nominal estimated capital

    result = executor.place_entry_order(
        security_id=comp.dhan_security_id,
        trading_symbol=comp.trading_symbol,
        company_id=comp.id,
        signal_id=None,
        quantity=req.quantity,
        allocated_capital=allocated_capital,
        exchange_segment=req.exchange_segment,
        product_type=req.product_type,
        strategy_type=req.strategy_type,
    )
    
    if not result or result.get("status") in ("failed", "error", "rejected"):
        err_msg = result.get("error") or result.get("remarks") or "Entry order rejected by broker"
        logger.error(f"[API][TRADES] Manual entry failed for {comp.trading_symbol}: {err_msg}")
        raise HTTPException(status_code=400, detail=str(err_msg))

    return result


@router.post("/api/trades/{trade_id}/cancel")
def cancel_trade_entry(trade_id: str, current_user: str = Depends(get_current_user)):
    """Cancels a pending entry order."""
    executor = get_order_executor()
    res = executor.cancel_pending_entry(trade_id)
    if not res or res.get("status") in ("failed", "error") or res.get("success") is False:
        err_msg = res.get("error") or res.get("remarks") or "Cancel entry order failed"
        logger.error(f"[API][TRADES] Cancel entry failed for trade {trade_id}: {err_msg}")
        raise HTTPException(status_code=400, detail=str(err_msg))
    return res


@router.post("/api/trades/{trade_id}/exit")
async def exit_trade(
    trade_id: str,
    qty: Optional[int] = Query(None),
    purpose: str = Query("MANUAL_EXIT"),
    current_user: str = Depends(get_current_user)
):
    """Manually exits a trade at MARKET via Two-Phase Exit Claim."""
    db = SessionLocal()
    try:
        t = db.query(Trade).filter(Trade.id == trade_id).first()
        if not t:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")

        remaining = t.remaining_quantity or t.allocated_quantity or 0
        if remaining <= 0:
            raise HTTPException(status_code=400, detail="Trade has no remaining quantity to exit")

        exit_qty = qty if qty and qty > 0 else remaining
        sec_id = str(t.security_id or "")
    finally:
        db.close()

    try:
        res = await place_market_sell(
            trade_id=trade_id,
            security_id=sec_id,
            qty=exit_qty,
            purpose=purpose,
            tag=f"ATS_MANUAL_{trade_id[:6]}"
        )
    except Exception as exc:
        logger.error(f"[API][TRADES] Exit order exception for trade {trade_id}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))

    order_status = res.status if hasattr(res, "status") else "REQUESTED"
    if order_status in ("FAILED", "REJECTED"):
        err_msg = getattr(res, "last_error", None) or f"Exit order {order_status.lower()}"
        logger.error(f"[API][TRADES] Exit order {order_status} for trade {trade_id}: {err_msg}")
        raise HTTPException(status_code=400, detail=str(err_msg))

    return {
        "status": "exit_initiated",
        "trade_id": trade_id,
        "order_status": order_status,
        "dhan_order_id": getattr(res, "dhan_order_id", None)
    }


@router.post("/api/trades/exit-by-security")
async def exit_trade_by_security(req: ExitBySecurityRequest, current_user: str = Depends(get_current_user)):
    """Exits all open trades for a given security ID."""
    cache_mgr = get_cache_manager()
    trades = cache_mgr.get_trades_for_security(req.security_id)
    if not trades:
        db = SessionLocal()
        try:
            trades = db.query(Trade).filter(
                Trade.security_id == str(req.security_id),
                Trade.ats_state.in_([AtsTradeState.OPEN, AtsTradeState.PARTIAL_EXIT])
            ).all()
        finally:
            db.close()

    if not trades:
        raise HTTPException(status_code=404, detail=f"No active trades found for security_id {req.security_id}")

    results = []
    failed_exits = []
    for t in trades:
        rem = t.remaining_quantity or t.allocated_quantity or 0
        if rem > 0:
            try:
                res = await place_market_sell(
                    trade_id=str(t.id),
                    security_id=str(req.security_id),
                    qty=rem,
                    purpose=req.purpose,
                    tag=f"ATS_SEC_{str(t.id)[:6]}"
                )
                ord_status = getattr(res, "status", "REQUESTED")
                if ord_status in ("FAILED", "REJECTED"):
                    failed_exits.append(f"Trade {t.id}: {getattr(res, 'last_error', ord_status)}")
                results.append({
                    "trade_id": str(t.id),
                    "order_status": ord_status,
                    "dhan_order_id": getattr(res, "dhan_order_id", None)
                })
            except Exception as exc:
                failed_exits.append(f"Trade {t.id}: {exc}")

    if failed_exits and not results:
        raise HTTPException(status_code=400, detail="; ".join(failed_exits))

    return {"status": "exits_initiated", "count": len(results), "trades": results, "errors": failed_exits}


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE AUDIT & HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/db/trades")
def get_db_trades(
    status: Optional[str] = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Retrieve trades from database with company details."""
    query = db.query(Trade, Company).outerjoin(Company, Trade.company_id == Company.id)
    if status:
        query = query.filter(Trade.ats_state == status)
    results = query.order_by(Trade.created_at.desc()).limit(limit).all()

    output = []
    for trade, company in results:
        output.append({
            "id": trade.id,
            "company_id": trade.company_id,
            "trading_symbol": company.trading_symbol if company else None,
            "company_name": company.company_name if company else None,
            "security_id": trade.security_id,
            "strategy_type": trade.strategy_type,
            "trade_date": str(trade.trade_date),
            "allocated_capital": trade.allocated_capital,
            "allocated_quantity": trade.allocated_quantity,
            "remaining_quantity": trade.remaining_quantity,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "stop_price": trade.stop_price,
            "target1_price": trade.target1_price,
            "target2_price": trade.target2_price,
            "realized_pnl": trade.realized_pnl,
            "exit_pct": trade.exit_pct,
            "exit_reason": trade.exit_reason,
            "ats_state": trade.ats_state,
            "trade_status": trade.trade_status,
            "sl_stage": trade.sl_stage,
            "created_at": str(trade.created_at),
            "executed_at": str(trade.executed_at) if trade.executed_at else None,
            "closed_at": str(trade.closed_at) if trade.closed_at else None,
        })
    return output


@router.get("/api/db/orders")
def get_db_orders(
    trade_id: Optional[str] = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Retrieve ATS orders from database."""
    query = db.query(AtsOrder)
    if trade_id:
        query = query.filter(AtsOrder.trade_id == trade_id)
    orders = query.order_by(AtsOrder.created_at.desc()).limit(limit).all()
    return orders


@router.get("/api/db/modifications")
def get_db_modifications(
    trade_order_id: Optional[str] = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Retrieve legacy order modifications audit log."""
    query = db.query(TradeOrderModification)
    if trade_order_id:
        query = query.filter(TradeOrderModification.trade_order_id == trade_order_id)
    return query.order_by(TradeOrderModification.created_at.desc()).limit(limit).all()


@router.get("/api/db/trade-events")
def get_db_trade_events(
    trade_id: Optional[str] = Query(None),
    limit: int = Query(200),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    """Retrieve append-only trade lifecycle audit events."""
    query = db.query(TradeEvent)
    if trade_id:
        query = query.filter(TradeEvent.trade_id == trade_id)
    return query.order_by(TradeEvent.created_at.desc()).limit(limit).all()
