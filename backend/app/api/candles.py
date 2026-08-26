"""
app.api.candles
===============
Candle synchronization endpoints (ADMIN only).
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from app.data.database import SessionLocal
from app.data.models import Company
from app.data.candles import sync_all_active_companies, sync_candles_for_company
from app.api.auth_app import require_admin, CurrentUser

router = APIRouter(tags=["Candles"])


def assert_success(result: dict) -> dict:
    if isinstance(result, dict):
        status = result.get("status")
        if status in ("failure", "error", "rejected", "failed", "blocked"):
            raise HTTPException(status_code=400, detail=result.get("remarks") or result.get("message") or "Operation failed")
    return result


@router.post("/candles/sync")
def trigger_candle_sync(limit: int = Query(50), _: CurrentUser = Depends(require_admin)):
    """ADMIN only. Sync candle data for all active companies."""
    try:
        result = sync_all_active_companies(limit=limit)
        return assert_success({"status": "sync_complete", **result})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Candle sync failed: {exc}")


@router.post("/candles/sync/{symbol}")
def trigger_candle_sync_single(symbol: str, _: CurrentUser = Depends(require_admin)):
    """ADMIN only. Sync candle data for a single symbol."""
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
