"""
app/api/router.py — ATS Frontend-Compatible API Router
======================================================
All endpoints preserve the exact response shapes expected by the existing React frontend.
Now includes Cache Management API endpoints.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Any, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.dhan_client import get_dhan_client
from app.services.portfolio import PortfolioService
from app.services.strategy import get_strategy_engine, get_signals_from_db
from app.services.candle_sync import sync_all_active_companies, sync_candles_for_company
from app.database import SessionLocal
from app.models import (
    Company, Signal, Trade, TradeOrder, Holding, Position,
    TradeOrderModification, AtsOrder, AtsTradeState, TradeEvent,
)
from app.core.engine import get_trade_engine
from app.core.executor import get_order_executor
from app.core.cache_manager import get_cache_manager

from fastapi import APIRouter, HTTPException, Query, Depends
from app.api.auth_app import get_current_user

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])
portfolio_service = PortfolioService()


def assert_success(result: Any) -> Any:
    """Raises an HTTPException if the result dictionary indicates a failure."""
    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        if status in ("failed", "error", "failure", "cancel_failed", "invalid_state"):
            msg = result.get("error") or result.get("message") or result.get("remarks") or f"Operation failed with status: {status}"
            raise HTTPException(status_code=400, detail=str(msg))
    return result


class ToggleEngineRequest(BaseModel):
    enabled: bool

class RenewTokenRequest(BaseModel):
    totp: Optional[str] = None

class ManualEntryRequest(BaseModel):
    security_id: str = Field(..., description="Dhan security ID")
    trading_symbol: str = Field(..., description="NSE trading symbol")
    quantity: int = Field(..., gt=0, description="Number of shares to buy")
    allocated_capital: float = Field(..., gt=0, description="Capital allocated (INR)")
    product_type: str = Field("AUTO", description="MTF (primary) or CNC (fallback)")


@router.get("/auth/status")
def get_auth_status():
    try:
        client = get_dhan_client()
        cfg = client.config
        token = client.auth_manager.get_valid_token()
        return {
            "status": "connected" if (token and cfg.client_id) else "disconnected",
            "client_id": f"***{cfg.client_id[-4:]}" if len(cfg.client_id) > 4 else cfg.client_id,
            "token_active": bool(token and len(token) > 20),
            "totp_configured": bool(cfg.pin and cfg.totp_secret),
            "mode": "LIVE_MARKET_ORDERS_ONLY",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Auth status check failed: {exc}")

@router.post("/auth/renew")
def renew_auth_token(payload: Optional[RenewTokenRequest] = None):
    try:
        client = get_dhan_client()
        totp = payload.totp if payload else None
        success = client.auth_manager.refresh_token(manual_totp=totp)
        if success:
            return {"status": "success", "message": "Dhan Access Token renewed via TOTP."}
        raise HTTPException(status_code=400, detail="Failed to renew token. Verify PIN and TOTP secret.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Token renewal error: {exc}")


@router.get("/engine/status")
def get_engine_status():
    """Returns trading engine status, cache health, and active trade count."""
    try:
        strategy_engine = get_strategy_engine()
        trade_engine = get_trade_engine()
        cache_mgr = get_cache_manager()
        active_trades = trade_engine.get_all_snapshots() if trade_engine else []
        return {
            "enabled": strategy_engine.is_enabled(),
            "mode": "LIVE_MARKET_ORDERS_ONLY",
            "entry_gate": "MARKET BUY on signal",
            "trailing_sl": "6-STAGE ACTIVE",
            "cache_status": {
                "active_trade_count": cache_mgr.get_active_trade_count(),
                "active_securities_count": len(cache_mgr.get_active_security_ids()),
                "database_authoritative": True,
            },
            "details": {
                "status": "running",
                "active_signals": 0,
                "open_positions": len(active_trades),
                "last_tick": str(datetime.utcnow()),
            },
            "active_trades": active_trades,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Engine status error: {exc}")

@router.post("/engine/toggle")
def toggle_engine(payload: ToggleEngineRequest):
    try:
        engine = get_strategy_engine()
        new_state = engine.set_enabled(payload.enabled)
        return {
            "enabled": new_state,
            "message": f"Trading Engine {'ACTIVATED' if new_state else 'PAUSED'}",
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to toggle engine: {exc}")

@router.get("/engine/trades")
def get_active_trades():
    """Return in-memory snapshots of all actively monitored trades."""
    try:
        engine = get_trade_engine()
        if not engine:
            return []
        return engine.get_all_snapshots()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get active trades: {exc}")

@router.post("/engine/cache/rebuild")
def rebuild_cache_api():
    """Explicitly trigger in-memory cache rebuild from PostgreSQL DB."""
    try:
        engine = get_trade_engine()
        cache_mgr = get_cache_manager()
        ws_mgr = getattr(engine, "ws_manager", None)
        count = cache_mgr.rebuild_cache(ws_manager=ws_mgr)
        return {
            "status": "cache_rebuilt",
            "active_trades_loaded": count,
            "active_securities": list(cache_mgr.get_active_security_ids()),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cache rebuild failed: {exc}")

@router.post("/engine/broker-reconcile")
async def trigger_broker_reconciliation_api():
    """Manually trigger 3-way Dhan broker position & order reconciliation pass."""
    try:
        from app.core.broker_reconciler import get_broker_reconciler
        reconciler = get_broker_reconciler()
        res = await reconciler.reconcile_cycle(is_startup=False)
        return assert_success(res)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Broker reconciliation failed: {exc}")


@router.get("/signals")
def list_signals(status: str = Query(None)):
    try:
        return get_signals_from_db(status=status, limit=200)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list signals: {exc}")

@router.post("/engine/scan")
def trigger_signal_scan():
    try:
        engine = get_strategy_engine()
        new_signals = engine.scan_signals_from_db()
        return {"status": "scan_complete", "new_signals_found": len(new_signals)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Signal scan failed: {exc}")

@router.post("/engine/evaluate-325")
def trigger_325_evaluation():
    """Manually trigger the 3:25 PM entry condition check."""
    try:
        engine = get_strategy_engine()
        res = engine.evaluate_and_execute_325_entries()
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"3:25 PM entry evaluation failed: {exc}")

@router.post("/engine/evaluate-325-exits")
def trigger_325_exit_evaluation():
    """Manually trigger the 3:25 PM Supertrend RED exit check."""
    try:
        engine = get_strategy_engine()
        res = engine.evaluate_and_execute_325_exits()
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"3:25 PM exit evaluation failed: {exc}")

@router.post("/engine/monitor-ltp")
def trigger_ltp_monitoring():
    """No-op: LTP monitoring is WebSocket-driven."""
    engine = get_trade_engine()
    return {
        "status": "ws_driven",
        "message": "LTP monitoring is driven by Dhan WebSocket ticks, not REST polling.",
        "active_trades": engine.get_active_trade_count() if engine else 0,
    }


@router.post("/trades/manual-entry")
def manual_trade_entry(payload: ManualEntryRequest):
    try:
        db = SessionLocal()
        company = (
            db.query(Company)
            .filter(
                (Company.dhan_security_id == payload.security_id) |
                (Company.trading_symbol == payload.trading_symbol.upper())
            )
            .first()
        )
        db.close()

        if not company:
            raise HTTPException(
                status_code=404,
                detail=f"Company not found for security_id={payload.security_id} / symbol={payload.trading_symbol}",
            )

        executor = get_order_executor()
        result = executor.place_entry_order(
            security_id=payload.security_id,
            trading_symbol=payload.trading_symbol.upper(),
            company_id=company.id,
            signal_id=None,
            quantity=payload.quantity,
            allocated_capital=payload.allocated_capital,
            product_type=payload.product_type,
        )
        return assert_success(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Manual entry failed: {exc}")


@router.post("/trades/{trade_id}/cancel")
def cancel_pending_trade_entry(trade_id: str):
    try:
        executor = get_order_executor()
        result = executor.cancel_pending_entry(trade_id, reason="OPERATOR_CANCEL")
        if result.get("status") in ("not_found",):
            raise HTTPException(status_code=404, detail=result["message"])
        return assert_success(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cancel failed: {exc}")


@router.get("/portfolio/summary")
def get_portfolio_summary():
    try:
        summary = portfolio_service.get_full_broker_summary()

        db = SessionLocal()
        db_trades_raw = db.query(Trade).order_by(Trade.created_at.desc()).limit(200).all()
        db_orders_raw = db.query(AtsOrder).order_by(AtsOrder.created_at.desc()).limit(200).all()
        db.close()

        summary["db_trades"] = [
            {
                "id": r.id, "company_id": r.company_id, "signal_id": r.signal_id,
                "trade_date": str(r.trade_date), "allocated_quantity": r.allocated_quantity,
                "entry_price": r.entry_price, "entry_value": r.entry_value,
                "target_pct": r.target_pct, "stoploss_pct": r.stoploss_pct,
                "exit_pct": r.exit_pct, "exit_price": r.exit_price,
                "exit_qty": r.exit_qty, "realized_pnl": r.realized_pnl,
                "exit_reason": r.exit_reason, "trade_status": r.trade_status,
                "ats_state": r.ats_state, "sl_stage": r.sl_stage or 0,
                "stop_price": r.stop_price, "target1_price": r.target1_price,
                "target2_price": r.target2_price, "remaining_quantity": r.remaining_quantity,
                "partial_exit_completed": r.partial_exit_completed,
                "created_at": str(r.created_at),
                "executed_at": str(r.executed_at) if r.executed_at else None,
                "closed_at": str(r.closed_at) if r.closed_at else None,
            }
            for r in db_trades_raw
        ]
        summary["db_orders"] = [
            {
                "id": r.id, "trade_id": r.trade_id, "dhan_order_id": r.dhan_order_id,
                "security_id": r.security_id, "quantity": r.quantity,
                "price": r.price or 0.0,
                "target_price": None,
                "stop_loss_price": None,
                "trailing_jump": None,
                "order_status": r.status,
                "trade_status": "OPEN",
                "order_purpose": r.order_purpose,
                "order_type": r.order_type,
                "transaction_type": r.transaction_type,
                "fill_price": r.fill_price, "fill_qty": r.fill_qty,
                "placed_at": str(r.placed_at) if r.placed_at else None,
                "submitted_at": str(r.placed_at) if r.placed_at else None,
                "executed_at": str(r.filled_at) if r.filled_at else None,
                "closed_at": None,
            }
            for r in db_orders_raw
        ]
        summary["db_modifications"] = []
        summary["super_orders"] = summary.get("super_orders") or []

        return summary
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/portfolio/funds")
def get_funds():
    try:
        return portfolio_service.get_fund_limits()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/portfolio/holdings")
def get_holdings():
    try:
        return portfolio_service.get_holdings()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/portfolio/positions")
def get_positions():
    try:
        return portfolio_service.get_positions()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/portfolio/trades")
def get_trades():
    try:
        return portfolio_service.get_trades()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/stocks/search")
def search_companies(q: str = Query("", description="Symbol, security ID, or company name")):
    try:
        db = SessionLocal()
        query = db.query(Company).filter(Company.is_active == True)
        if q.strip():
            term = q.strip().upper()
            query = query.filter(
                (Company.trading_symbol.ilike(f"%{term}%")) |
                (Company.company_name.ilike(f"%{term}%")) |
                (Company.dhan_security_id == term)
            )
        results = query.limit(50).all()
        db.close()
        return [
            {
                "id": c.id, "security_id": c.dhan_security_id,
                "trading_symbol": c.trading_symbol, "company_name": c.company_name,
                "exchange": c.exchange, "market_cap": c.market_cap,
                "is_active": c.is_active, "img_url": c.img_url,
            }
            for c in results
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}")

@router.get("/stocks/{symbol}")
def get_company_details(symbol: str):
    try:
        db = SessionLocal()
        company = (
            db.query(Company)
            .filter(
                (Company.trading_symbol == symbol.upper()) |
                (Company.dhan_security_id == symbol)
            )
            .first()
        )
        db.close()
        if not company:
            raise HTTPException(status_code=404, detail=f"Company '{symbol}' not found.")
        return {
            "id": company.id, "security_id": company.dhan_security_id,
            "trading_symbol": company.trading_symbol, "company_name": company.company_name,
            "exchange": company.exchange, "isin": company.isin,
            "segment": company.segment, "market_cap": company.market_cap,
            "is_active": company.is_active, "img_url": company.img_url,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Company lookup failed: {exc}")

@router.get("/companies/images")
def get_company_images():
    try:
        db = SessionLocal()
        companies = db.query(Company).filter(Company.img_url.isnot(None)).all()
        db.close()
        return {c.trading_symbol: c.img_url for c in companies}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch company images: {exc}")


@router.post("/candles/sync")
def trigger_candle_sync(limit: int = Query(50)):
    try:
        result = sync_all_active_companies(limit=limit)
        return assert_success({"status": "sync_complete", **result})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Candle sync failed: {exc}")

@router.post("/candles/sync/{symbol}")
def trigger_candle_sync_single(symbol: str):
    try:
        db = SessionLocal()
        company = db.query(Company).filter(Company.trading_symbol == symbol.upper()).first()
        db.close()
        if not company:
            raise HTTPException(status_code=404, detail=f"Company '{symbol}' not found.")
        if not company.dhan_security_id:
            raise HTTPException(status_code=400, detail=f"Company '{symbol}' has no dhan_security_id.")
        result = sync_candles_for_company(
            company_id=company.id,
            security_id=company.dhan_security_id,
            exchange_segment="NSE_EQ",
        )
        return assert_success({"status": "sync_complete", "symbol": symbol, **result})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Candle sync failed: {exc}")


@router.get("/db/trades")
def get_db_trades():
    try:
        db = SessionLocal()
        records = db.query(Trade).order_by(Trade.created_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "id": r.id, "company_id": r.company_id, "signal_id": r.signal_id,
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
def get_db_orders():
    try:
        db = SessionLocal()
        ats_orders = db.query(AtsOrder).order_by(AtsOrder.created_at.desc()).limit(200).all()
        legacy_orders = db.query(TradeOrder).order_by(TradeOrder.created_at.desc()).limit(50).all()
        db.close()

        result = []
        for r in ats_orders:
            result.append({
                "id": r.id, "trade_id": r.trade_id, "dhan_order_id": r.dhan_order_id,
                "security_id": r.security_id, "quantity": r.quantity,
                "price": r.price or 0.0, "target_price": None,
                "stop_loss_price": None, "trailing_jump": None,
                "order_status": r.status, "trade_status": "OPEN",
                "order_purpose": r.order_purpose, "order_type": r.order_type,
                "transaction_type": r.transaction_type,
                "submitted_at": str(r.placed_at) if r.placed_at else None,
                "executed_at": str(r.filled_at) if r.filled_at else None,
                "closed_at": None, "source": "ats_orders",
            })
        for r in legacy_orders:
            result.append({
                "id": r.id, "trade_id": r.trade_id, "dhan_order_id": r.dhan_order_id,
                "security_id": r.security_id, "quantity": r.quantity,
                "price": r.price, "target_price": r.target_price,
                "stop_loss_price": r.stop_loss_price, "trailing_jump": r.trailing_jump,
                "order_status": r.order_status, "trade_status": r.trade_status,
                "submitted_at": str(r.submitted_at) if r.submitted_at else None,
                "executed_at": str(r.executed_at) if r.executed_at else None,
                "closed_at": str(r.closed_at) if r.closed_at else None,
                "source": "trade_orders_legacy",
            })
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch orders: {exc}")

@router.get("/db/modifications")
def get_db_modifications():
    try:
        db = SessionLocal()
        records = (
            db.query(TradeOrderModification)
            .order_by(TradeOrderModification.created_at.desc())
            .limit(200)
            .all()
        )
        db.close()
        return [
            {
                "id": r.id, "trade_order_id": r.trade_order_id,
                "old_sl_price": r.old_sl_price, "new_sl_price": r.new_sl_price,
                "reason": r.reason, "status": r.status,
                "created_at": str(r.created_at),
                "executed_at": str(r.executed_at) if r.executed_at else None,
                "error_message": r.error_message,
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch modifications: {exc}")

@router.get("/db/holdings")
def get_db_holdings():
    try:
        db = SessionLocal()
        records = db.query(Holding).order_by(Holding.captured_at.desc()).limit(500).all()
        db.close()
        return [
            {
                "trading_symbol": r.trading_symbol, "security_id": r.security_id,
                "total_qty": r.total_qty, "available_qty": r.available_qty,
                "avg_cost_price": r.avg_cost_price, "last_traded_price": r.last_traded_price,
                "captured_at": str(r.captured_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch holdings from DB: {exc}")

@router.get("/db/positions")
def get_db_positions():
    try:
        db = SessionLocal()
        records = db.query(Position).order_by(Position.captured_at.desc()).limit(200).all()
        db.close()
        return [
            {
                "trading_symbol": r.trading_symbol, "security_id": r.security_id,
                "product_type": r.product_type, "net_qty": r.net_qty,
                "buy_avg": r.buy_avg, "realized_profit": r.realized_profit,
                "unrealized_profit": r.unrealized_profit, "captured_at": str(r.captured_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch positions from DB: {exc}")

@router.get("/db/trade-events")
def get_trade_events(trade_id: str = Query(None, description="Filter by trade ID")):
    try:
        db = SessionLocal()
        q = db.query(TradeEvent).order_by(TradeEvent.created_at.desc())
        if trade_id:
            q = q.filter(TradeEvent.trade_id == trade_id)
        records = q.limit(500).all()
        db.close()
        return [
            {
                "id": r.id, "trade_id": r.trade_id, "event_type": r.event_type,
                "detail": r.detail, "price": r.price, "quantity": r.quantity,
                "created_at": str(r.created_at),
            }
            for r in records
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch trade events: {exc}")
