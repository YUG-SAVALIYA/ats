"""
app.api.db_views
================
Direct audit database query views with tenant isolation.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional

from app.data.database import SessionLocal
from app.data.models import Trade, AtsOrder, TradeOrder, Holding, Position, TradeEvent, DhanAccount
from app.api.auth_app import get_tenant_scope, TenantScope

router = APIRouter(tags=["Database Views"])


@router.get("/db/trades")
def get_db_trades(
    strategy_type: str = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    try:
        db = SessionLocal()
        q = db.query(Trade)
        if strategy_type:
            q = q.filter(Trade.strategy_type == strategy_type)
        if scope.account_ids is not None:
            q = q.filter(Trade.dhan_account_id.in_(scope.account_ids))
        records = q.order_by(Trade.created_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "company_id": r.company_id, "signal_id": r.signal_id,
                "dhan_account_id": r.dhan_account_id,
                "strategy_type": r.strategy_type,
                "trade_date": str(r.trade_date), "allocated_quantity": r.allocated_quantity,
                "entry_price": r.entry_price, "entry_value": r.entry_value,
                "target_pct": r.target_pct, "stoploss_pct": r.stoploss_pct,
                "exit_pct": r.exit_pct, "exit_price": r.exit_price,
                "exit_qty": r.exit_qty, "realized_pnl": r.realized_pnl,
                "exit_reason": r.exit_reason, "trade_status": r.trade_status,
                "ats_state": r.ats_state, "sl_stage": r.sl_stage or 0,
                "stop_price": r.stop_price,
                "created_at": str(r.created_at),
                "executed_at": str(r.executed_at) if r.executed_at else None,
                "closed_at": str(r.closed_at) if r.closed_at else None,
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch trades: {exc}")


@router.get("/db/orders")
def get_db_orders(
    strategy_type: str = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    try:
        db = SessionLocal()
        q = db.query(AtsOrder)
        if strategy_type:
            q = q.filter(AtsOrder.strategy_type == strategy_type)
        if scope.account_ids is not None:
            q = q.filter(AtsOrder.dhan_account_id.in_(scope.account_ids))
        records = q.order_by(AtsOrder.created_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "trade_id": r.trade_id, "dhan_account_id": r.dhan_account_id,
                "strategy_type": r.strategy_type,
                "dhan_order_id": r.dhan_order_id, "correlation_id": r.correlation_id,
                "order_purpose": r.order_purpose, "transaction_type": r.transaction_type,
                "security_id": r.security_id, "quantity": r.quantity,
                "price": r.price, "order_type": r.order_type,
                "product_type": r.product_type, "status": r.status,
                "fill_qty": r.fill_qty, "fill_price": r.fill_price,
                "last_error": r.last_error, "error_count": r.error_count,
                "placed_at": str(r.placed_at) if r.placed_at else None,
                "filled_at": str(r.filled_at) if r.filled_at else None,
                "created_at": str(r.created_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch orders: {exc}")


@router.get("/db/trade-orders")
def get_db_trade_orders(
    scope: TenantScope = Depends(get_tenant_scope),
):
    try:
        db = SessionLocal()
        q = db.query(TradeOrder)
        if scope.account_ids is not None:
            q = q.filter(TradeOrder.dhan_account_id.in_(scope.account_ids))
        records = q.order_by(TradeOrder.created_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "trade_id": r.trade_id, "company_id": r.company_id,
                "dhan_account_id": r.dhan_account_id,
                "dhan_order_id": r.dhan_order_id, "correlation_id": r.correlation_id,
                "order_status": r.order_status, "transaction_type": r.transaction_type,
                "exchange_segment": r.exchange_segment, "product_type": r.product_type,
                "order_type": r.order_type, "validity": r.validity,
                "trading_symbol": r.trading_symbol, "security_id": r.security_id,
                "quantity": r.quantity, "disclosed_quantity": r.disclosed_quantity,
                "price": r.price, "trigger_price": r.trigger_price,
                "executed_quantity": r.executed_quantity,
                "average_execution_price": r.average_execution_price,
                "target_price": r.target_price, "stop_loss_price": r.stop_loss_price,
                "trailing_jump": r.trailing_jump,
                "created_at": str(r.created_at),
                "executed_at": str(r.executed_at) if r.executed_at else None,
                "closed_at": str(r.closed_at) if r.closed_at else None,
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch trade orders: {exc}")


@router.get("/db/trade-events")
def get_db_trade_events(
    trade_id: Optional[str] = None,
    scope: TenantScope = Depends(get_tenant_scope),
):
    try:
        db = SessionLocal()
        q = db.query(TradeEvent)
        if trade_id:
            q = q.filter(TradeEvent.trade_id == trade_id)
        if scope.account_ids is not None:
            q = q.filter(TradeEvent.dhan_account_id.in_(scope.account_ids))
        records = q.order_by(TradeEvent.created_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "trade_id": r.trade_id, "dhan_account_id": r.dhan_account_id,
                "event_type": r.event_type, "detail": r.detail,
                "price": r.price, "quantity": r.quantity,
                "created_at": str(r.created_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch trade events: {exc}")


@router.get("/db/holdings")
def get_db_holdings(
    scope: TenantScope = Depends(get_tenant_scope),
):
    try:
        db = SessionLocal()
        q = db.query(Holding)
        if scope.account_ids is not None:
            client_ids = [row[0] for row in db.query(DhanAccount.client_id).filter(DhanAccount.id.in_(scope.account_ids)).all()]
            q = q.filter((Holding.dhan_account_id.in_(scope.account_ids)) | (Holding.dhan_client_id.in_(client_ids)))
        records = q.order_by(Holding.captured_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "dhan_client_id": r.dhan_client_id, "company_id": r.company_id,
                "exchange": r.exchange, "trading_symbol": r.trading_symbol,
                "security_id": r.security_id, "isin": r.isin,
                "total_qty": r.total_qty, "dp_qty": r.dp_qty, "t1_qty": r.t1_qty,
                "available_qty": r.available_qty, "collateral_qty": r.collateral_qty,
                "avg_cost_price": r.avg_cost_price, "last_traded_price": r.last_traded_price,
                "captured_at": str(r.captured_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch holdings: {exc}")


@router.get("/db/positions")
def get_db_positions(
    scope: TenantScope = Depends(get_tenant_scope),
):
    try:
        db = SessionLocal()
        q = db.query(Position)
        if scope.account_ids is not None:
            client_ids = [row[0] for row in db.query(DhanAccount.client_id).filter(DhanAccount.id.in_(scope.account_ids)).all()]
            q = q.filter((Position.dhan_account_id.in_(scope.account_ids)) | (Position.dhan_client_id.in_(client_ids)))
        records = q.order_by(Position.captured_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "dhan_client_id": r.dhan_client_id, "company_id": r.company_id,
                "trading_symbol": r.trading_symbol, "security_id": r.security_id,
                "position_type": r.position_type, "product_type": r.product_type,
                "buy_avg": r.buy_avg, "buy_qty": r.buy_qty,
                "sell_avg": r.sell_avg, "sell_qty": r.sell_qty,
                "net_qty": r.net_qty, "realized_profit": r.realized_profit,
                "unrealized_profit": r.unrealized_profit,
                "captured_at": str(r.captured_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch positions: {exc}")
