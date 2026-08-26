"""
app.api.trades
==============
Trade execution, manual entry, manual exit, and cancellation endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional

from app.data.database import SessionLocal
from app.data.models import Trade, Company, AtsTradeState
from app.trading.execution import get_order_executor, place_market_sell
from app.trading.cache import get_cache_manager
from app.api.auth_app import get_tenant_scope, TenantScope

router = APIRouter(tags=["Trades"])


def assert_success(result: dict) -> dict:
    if isinstance(result, dict):
        status = result.get("status")
        if status in ("failure", "error", "rejected", "failed", "blocked"):
            raise HTTPException(status_code=400, detail=result.get("remarks") or result.get("message") or "Operation failed")
    return result


class ManualExitRequest(BaseModel):
    quantity: int

class ManualExitBySecurityRequest(BaseModel):
    security_id: str
    quantity: int

class ManualEntryRequest(BaseModel):
    security_id: str
    trading_symbol: str
    quantity: int
    allocated_capital: float = 0.0
    product_type: str = "MTF"
    dhan_account_id: Optional[str] = None


@router.get("/trades/active")
def get_active_trades(scope: TenantScope = Depends(get_tenant_scope)):
    """
    Returns active trades (OPEN, PARTIAL_EXIT, ENTRY_PENDING, EXIT_REQUESTED)
    tenant-scoped by dhan_account_id.
    """
    try:
        cache = get_cache_manager()
        trades = cache.get_all_active_trades()
        snapshots = [
            {
                "trade_id": str(t.id),
                "dhan_account_id": str(t.dhan_account_id or ""),
                "security_id": str(t.security_id or ""),
                "ats_state": str(t.ats_state.value if hasattr(t.ats_state, "value") else t.ats_state),
                "sl_stage": t.sl_stage,
                "stop_price": t.stop_price,
                "entry_price": t.entry_price,
                "target1_price": t.target1_price,
                "target2_price": t.target2_price,
                "remaining_quantity": t.remaining_quantity,
                "partial_exit_completed": t.partial_exit_completed,
            }
            for t in trades
        ]

        if scope.account_ids is not None:
            snapshots = [t for t in snapshots if t.get("dhan_account_id") in scope.account_ids]

        return snapshots
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch active trades: {exc}")


@router.post("/trades/manual-entry")
def manual_trade_entry(
    payload: ManualEntryRequest,
    scope: TenantScope = Depends(get_tenant_scope),
):
    """
    Places a manual entry order.
    Requires explicit dhan_account_id and validates tenant ownership.
    """
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

        target_account_id = payload.dhan_account_id
        if target_account_id is None:
            raise HTTPException(
                status_code=400,
                detail="dhan_account_id is required. Specify which account should place this order."
            )

        if scope.account_ids is not None and target_account_id not in scope.account_ids:
            raise HTTPException(
                status_code=403,
                detail=f"Account {target_account_id} does not belong to the authenticated user."
            )

        executor = get_order_executor()
        result = executor.place_entry_order(
            dhan_account_id=target_account_id,
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


@router.post("/trades/{trade_id}/exit")
async def manual_trade_exit(
    trade_id: str,
    payload: ManualExitRequest,
    scope: TenantScope = Depends(get_tenant_scope),
):
    """
    Executes an atomic Two-Phase DB Exit-Claim + MARKET SELL order for a specific trade.
    """
    try:
        db = SessionLocal()
        trade = db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade:
            db.close()
            raise HTTPException(status_code=404, detail="Trade not found")

        if scope.account_ids is not None and trade.dhan_account_id not in scope.account_ids:
            db.close()
            raise HTTPException(
                status_code=403,
                detail=f"Trade {trade_id} does not belong to the authenticated user."
            )

        security_id = trade.security_id
        db.close()

        result = await place_market_sell(
            trade_id=trade_id,
            security_id=security_id,
            qty=payload.quantity,
            purpose="MANUAL_EXIT"
        )
        return {
            "status": "success",
            "message": "Manual exit order placed successfully",
            "ats_order_id": result.id,
            "dhan_order_id": result.dhan_order_id
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Manual exit failed: {exc}")


@router.post("/trades/exit-by-security")
async def manual_trade_exit_by_security(
    payload: ManualExitBySecurityRequest,
    scope: TenantScope = Depends(get_tenant_scope),
):
    """
    Executes a manual exit by security ID for the authenticated user's active trade.
    """
    try:
        db = SessionLocal()

        base_q = db.query(Trade).filter(
            Trade.security_id == payload.security_id,
            Trade.trade_status == "OPEN",
            Trade.ats_state.in_([AtsTradeState.OPEN, AtsTradeState.PARTIAL_EXIT])
        )
        if scope.account_ids is not None:
            base_q = base_q.filter(Trade.dhan_account_id.in_(scope.account_ids))
        trade = base_q.first()

        if not trade:
            company = db.query(Company).filter(Company.dhan_security_id == payload.security_id).first()
            if company:
                q2 = db.query(Trade).filter(
                    Trade.company_id == company.id,
                    Trade.trade_status == "OPEN",
                    Trade.ats_state.in_([AtsTradeState.OPEN, AtsTradeState.PARTIAL_EXIT])
                )
                if scope.account_ids is not None:
                    q2 = q2.filter(Trade.dhan_account_id.in_(scope.account_ids))
                trade = q2.first()

        if not trade:
            db.close()
            raise HTTPException(status_code=404, detail=f"No active ATS trade found for security {payload.security_id}")

        trade_id = trade.id
        db.close()

        result = await place_market_sell(
            trade_id=trade_id,
            security_id=payload.security_id,
            qty=payload.quantity,
            purpose="MANUAL_EXIT"
        )
        return {
            "status": "success",
            "message": "Manual exit order placed successfully",
            "ats_order_id": result.id,
            "dhan_order_id": result.dhan_order_id
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Manual exit failed: {exc}")


@router.post("/trades/{trade_id}/cancel")
def cancel_pending_trade_entry(
    trade_id: str,
    scope: TenantScope = Depends(get_tenant_scope),
):
    """Cancel a pending entry. Users may only cancel their own trades."""
    try:
        db = SessionLocal()
        trade = db.query(Trade).filter(Trade.id == trade_id).first()
        db.close()
        if trade and scope.account_ids is not None and trade.dhan_account_id not in scope.account_ids:
            raise HTTPException(
                status_code=403,
                detail=f"Trade {trade_id} does not belong to the authenticated user."
            )
        executor = get_order_executor()
        result = executor.cancel_pending_entry(trade_id, reason="OPERATOR_CANCEL")
        if result.get("status") in ("not_found",):
            raise HTTPException(status_code=404, detail=result["message"])
        return assert_success(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cancel failed: {exc}")
