"""
app.api.portfolio
=================
Tenant-scoped portfolio summaries, funds, holdings, positions, and trades.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional

from app.data.database import SessionLocal
from app.data.models import DhanAccount, Trade, AtsOrder
from app.broker.dhan_portfolio import PortfolioService
from app.api.auth_app import get_tenant_scope, TenantScope

router = APIRouter(tags=["Portfolio"])


@router.get("/portfolio/summary")
def get_portfolio_summary(
    strategy_type: str = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    """
    Full portfolio summary, fully tenant-scoped.
    - USER: funds/holdings/positions for their own DhanAccount(s), trades/orders scoped to them.
    - ADMIN: all accounts aggregated.
    """
    try:
        db = SessionLocal()

        if scope.account_ids is None:
            accounts = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").all()
        else:
            accounts = db.query(DhanAccount).filter(DhanAccount.id.in_(scope.account_ids)).all()

        live_by_account = []
        for acc in accounts:
            try:
                svc = PortfolioService(dhan_account_id=acc.id)
                funds = svc.get_fund_limits()
                holdings = svc.get_holdings()
                positions = svc.get_positions()
                trades = svc.get_trades()
                live_by_account.append({
                    "dhan_account_id": acc.id,
                    "client_id": f"***{acc.client_id[-4:]}" if acc.client_id and len(acc.client_id) >= 4 else "***",
                    "funds": funds,
                    "holdings": holdings,
                    "positions": positions,
                    "trades": trades,
                })
            except Exception as acc_exc:
                live_by_account.append({
                    "dhan_account_id": acc.id,
                    "client_id": f"***{acc.client_id[-4:]}" if acc.client_id and len(acc.client_id) >= 4 else "***",
                    "error": str(acc_exc),
                    "funds": {},
                    "holdings": [],
                    "positions": [],
                    "trades": [],
                })

        q_trades = db.query(Trade)
        if strategy_type:
            q_trades = q_trades.filter(Trade.strategy_type == strategy_type)
        if scope.account_ids is not None:
            q_trades = q_trades.filter(Trade.dhan_account_id.in_(scope.account_ids))

        open_trades = q_trades.filter(Trade.ats_state.in_(["OPEN", "PARTIAL_EXIT", "ENTRY_PENDING"])).all()
        closed_trades = q_trades.filter(Trade.ats_state == "CLOSED").all()

        total_realized_pnl = sum(t.realized_pnl or 0.0 for t in closed_trades)

        q_orders = db.query(AtsOrder)
        if strategy_type:
            q_orders = q_orders.filter(AtsOrder.strategy_type == strategy_type)
        if scope.account_ids is not None:
            q_orders = q_orders.filter(AtsOrder.dhan_account_id.in_(scope.account_ids))
        recent_orders = q_orders.order_by(AtsOrder.created_at.desc()).limit(50).all()

        db.close()

        total_avail_balance = sum(
            acc.get("funds", {}).get("availabelBalance", acc.get("funds", {}).get("available_balance", 0.0)) or 0.0
            for acc in live_by_account
            if isinstance(acc.get("funds"), dict)
        )

        all_holdings = []
        for acc in live_by_account:
            for h in acc.get("holdings", []):
                all_holdings.append({**h, "dhan_account_id": acc["dhan_account_id"]})

        all_positions = []
        for acc in live_by_account:
            for p in acc.get("positions", []):
                all_positions.append({**p, "dhan_account_id": acc["dhan_account_id"]})

        all_broker_trades = []
        for acc in live_by_account:
            for t in acc.get("trades", []):
                all_broker_trades.append({**t, "dhan_account_id": acc["dhan_account_id"]})

        return {
            "scoped_to_user": scope.user_id is not None,
            "account_count": len(accounts),
            "accounts": live_by_account,
            "aggregate": {
                "available_balance": total_avail_balance,
                "open_trades_count": len(open_trades),
                "closed_trades_count": len(closed_trades),
                "total_realized_pnl": round(total_realized_pnl, 2),
                "holdings_count": len(all_holdings),
                "positions_count": len(all_positions),
            },
            "holdings": all_holdings,
            "positions": all_positions,
            "trades": all_broker_trades,
            "orders": [
                {
                    "id": o.id,
                    "trade_id": o.trade_id,
                    "dhan_account_id": o.dhan_account_id,
                    "strategy_type": o.strategy_type,
                    "dhan_order_id": o.dhan_order_id,
                    "correlation_id": o.correlation_id,
                    "order_purpose": o.order_purpose,
                    "transaction_type": o.transaction_type,
                    "security_id": o.security_id,
                    "quantity": o.quantity,
                    "price": o.price,
                    "order_type": o.order_type,
                    "product_type": o.product_type,
                    "status": o.status,
                    "fill_qty": o.fill_qty,
                    "fill_price": o.fill_price,
                    "placed_at": str(o.placed_at) if o.placed_at else None,
                    "filled_at": str(o.filled_at) if o.filled_at else None,
                    "created_at": str(o.created_at),
                }
                for o in recent_orders
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch portfolio summary: {exc}")


@router.get("/portfolio/funds")
def get_funds(
    dhan_account_id: Optional[str] = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    """Fetch live fund limits from Dhan for a specific account or default active account."""
    try:
        target_account_id = dhan_account_id
        if not target_account_id:
            db = SessionLocal()
            if scope.account_ids is not None:
                acc = db.query(DhanAccount).filter(DhanAccount.id.in_(scope.account_ids), DhanAccount.account_status == "ACTIVE").first()
            else:
                acc = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").first()
            db.close()
            if not acc:
                raise HTTPException(status_code=404, detail="No active Dhan account found.")
            target_account_id = acc.id

        if scope.account_ids is not None and target_account_id not in scope.account_ids:
            raise HTTPException(status_code=403, detail="Access to specified Dhan account is not permitted.")

        svc = PortfolioService(dhan_account_id=target_account_id)
        return svc.get_fund_limits()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch funds: {exc}")


@router.get("/portfolio/holdings")
def get_holdings(
    dhan_account_id: Optional[str] = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    """Fetch live holdings from Dhan."""
    try:
        target_account_id = dhan_account_id
        if not target_account_id:
            db = SessionLocal()
            if scope.account_ids is not None:
                acc = db.query(DhanAccount).filter(DhanAccount.id.in_(scope.account_ids), DhanAccount.account_status == "ACTIVE").first()
            else:
                acc = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").first()
            db.close()
            if not acc:
                raise HTTPException(status_code=404, detail="No active Dhan account found.")
            target_account_id = acc.id

        if scope.account_ids is not None and target_account_id not in scope.account_ids:
            raise HTTPException(status_code=403, detail="Access to specified Dhan account is not permitted.")

        svc = PortfolioService(dhan_account_id=target_account_id)
        return svc.get_holdings()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch holdings: {exc}")


@router.get("/portfolio/positions")
def get_positions(
    dhan_account_id: Optional[str] = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    """Fetch live positions from Dhan."""
    try:
        target_account_id = dhan_account_id
        if not target_account_id:
            db = SessionLocal()
            if scope.account_ids is not None:
                acc = db.query(DhanAccount).filter(DhanAccount.id.in_(scope.account_ids), DhanAccount.account_status == "ACTIVE").first()
            else:
                acc = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").first()
            db.close()
            if not acc:
                raise HTTPException(status_code=404, detail="No active Dhan account found.")
            target_account_id = acc.id

        if scope.account_ids is not None and target_account_id not in scope.account_ids:
            raise HTTPException(status_code=403, detail="Access to specified Dhan account is not permitted.")

        svc = PortfolioService(dhan_account_id=target_account_id)
        return svc.get_positions()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch positions: {exc}")


@router.get("/portfolio/trades")
def get_trades(
    dhan_account_id: Optional[str] = Query(None),
    scope: TenantScope = Depends(get_tenant_scope),
):
    """Fetch executed trade history from Dhan."""
    try:
        target_account_id = dhan_account_id
        if not target_account_id:
            db = SessionLocal()
            if scope.account_ids is not None:
                acc = db.query(DhanAccount).filter(DhanAccount.id.in_(scope.account_ids), DhanAccount.account_status == "ACTIVE").first()
            else:
                acc = db.query(DhanAccount).filter(DhanAccount.account_status == "ACTIVE").first()
            db.close()
            if not acc:
                raise HTTPException(status_code=404, detail="No active Dhan account found.")
            target_account_id = acc.id

        if scope.account_ids is not None and target_account_id not in scope.account_ids:
            raise HTTPException(status_code=403, detail="Access to specified Dhan account is not permitted.")

        svc = PortfolioService(dhan_account_id=target_account_id)
        return svc.get_trades()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch trades: {exc}")
