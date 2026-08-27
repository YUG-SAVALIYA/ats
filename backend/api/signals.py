"""
api/signals.py — Signal Management & Evaluation Endpoints
=========================================================
Endpoints for querying signals, triggering manual scans, and evaluating 3:25 PM triggers:
- GET  /api/signals
- POST /api/engine/scan
- POST /api/engine/evaluate-325
- POST /api/engine/evaluate-325-exits
"""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException

from api.auth import get_current_user
from trading.signals import get_signals_from_db, scan_signals_from_db
from workers.scheduler import evaluate_and_execute_325_entries, evaluate_and_execute_325_exits

logger = logging.getLogger("ats.api.signals")

router = APIRouter(tags=["Signals"])


@router.get("/api/signals")
def list_signals(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, EXECUTED, REJECTED, EXPIRED"),
    strategy_type: Optional[str] = Query(None, description="Filter by strategy: SUPERTREND, MONTHLY_RSI"),
    limit: int = Query(100, description="Max signals to return"),
    current_user: str = Depends(get_current_user),
):
    """Retrieve scanned trading signals from the database."""
    return get_signals_from_db(status=status, strategy_type=strategy_type, limit=limit)


@router.post("/api/engine/scan")
def trigger_signal_scan(current_user: str = Depends(get_current_user)):
    """Manually trigger a full signal scan across all active securities."""
    try:
        signals = scan_signals_from_db()
        return {
            "status": "success",
            "signals_found": len(signals),
            "signals": signals
        }
    except Exception as exc:
        logger.error(f"[SIGNALS] Signal scan failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Signal scan failed: {str(exc)}")


@router.post("/api/engine/evaluate-325")
def trigger_325_entries(current_user: str = Depends(get_current_user)):
    """Manually trigger 3:25 PM signal qualification and entry execution."""
    try:
        return evaluate_and_execute_325_entries()
    except Exception as exc:
        logger.error(f"[SIGNALS] 3:25 PM entry evaluation failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"3:25 PM entry evaluation failed: {str(exc)}")


@router.post("/api/engine/evaluate-325-exits")
def trigger_325_exits(current_user: str = Depends(get_current_user)):
    """Manually trigger 3:25 PM Supertrend RED exit evaluations for open trades."""
    try:
        return evaluate_and_execute_325_exits()
    except Exception as exc:
        logger.error(f"[SIGNALS] 3:25 PM exit evaluation failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"3:25 PM exit evaluation failed: {str(exc)}")
