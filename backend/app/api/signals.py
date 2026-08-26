"""
app.api.signals
===============
Strategy signals feed and details endpoints.
"""

from fastapi import APIRouter, HTTPException, Query
from app.trading.strategy import get_signals_from_db
from app.data.database import SessionLocal
from app.data.models import Signal

router = APIRouter(tags=["Signals"])


@router.get("/signals")
def list_signals(status: str = Query(None), strategy_type: str = Query(None)):
    """Read-only signal list. Accessible to any authenticated user."""
    try:
        return get_signals_from_db(status=status, strategy_type=strategy_type, limit=200)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list signals: {exc}")


@router.get("/signals/{signal_id}")
def get_signal_details(signal_id: str):
    """Fetch details of a single signal."""
    try:
        db = SessionLocal()
        sig = db.query(Signal).filter(Signal.id == signal_id).first()
        db.close()
        if not sig:
            raise HTTPException(status_code=404, detail="Signal not found")
        return {
            "id": sig.id,
            "company_id": sig.company_id,
            "strategy_type": sig.strategy_type,
            "date": str(sig.date),
            "status": sig.status,
            "rejection_reason": sig.rejection_reason,
            "expiry_reason": sig.expiry_reason,
            "raw_signal_data": sig.raw_signal_data,
            "created_at": str(sig.created_at),
            "execution_date": str(sig.execution_date) if sig.execution_date else None,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch signal: {exc}")
