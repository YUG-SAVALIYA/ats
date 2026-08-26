"""
app.api.engine
==============
Trading engine status, toggle, scan, evaluation, cache, and reconciliation endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime

from app.trading.strategy import get_strategy_engine
from app.trading.trade_engine import get_trade_engine
from app.trading.cache import get_cache_manager
from app.workers.reconciliation import get_broker_reconciler
from app.api.auth_app import require_admin, get_tenant_scope, CurrentUser, TenantScope

router = APIRouter(tags=["Engine"])


def assert_success(result: dict) -> dict:
    if isinstance(result, dict):
        status = result.get("status")
        if status in ("failure", "error", "rejected", "failed", "blocked"):
            raise HTTPException(status_code=400, detail=result.get("remarks") or result.get("message") or "Operation failed")
    return result


class ToggleEngineRequest(BaseModel):
    enabled: bool


@router.get("/engine/status")
def get_engine_status(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Returns trading engine status, cache health, and active trade count."""
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
def toggle_engine(payload: ToggleEngineRequest, _: CurrentUser = Depends(require_admin)):
    """ADMIN only. Enable or disable the trading engine."""
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
def get_active_trades(scope: TenantScope = Depends(get_tenant_scope)):
    """Return in-memory snapshots of actively monitored trades, scoped to the requesting user's accounts."""
    try:
        engine = get_trade_engine()
        if not engine:
            return []
        snapshots = engine.get_all_snapshots()
        if scope.account_ids is not None:
            snapshots = [s for s in snapshots if s.get("dhan_account_id") in scope.account_ids]
        return snapshots
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to get active trades: {exc}")


@router.post("/engine/cache/rebuild")
def rebuild_cache_api(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Explicitly trigger in-memory cache rebuild from PostgreSQL DB."""
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
async def trigger_broker_reconciliation_api(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Manually trigger 3-way Dhan broker position & order reconciliation pass."""
    try:
        reconciler = get_broker_reconciler()
        res = await reconciler.reconcile_cycle(is_startup=False)
        return assert_success(res)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Broker reconciliation failed: {exc}")


@router.post("/engine/scan")
def trigger_signal_scan(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Trigger signal scan across all active companies."""
    try:
        engine = get_strategy_engine()
        new_signals = engine.scan_signals_from_db()
        return {"status": "scan_complete", "new_signals_found": len(new_signals)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Signal scan failed: {exc}")


@router.post("/engine/evaluate-325")
def trigger_325_evaluation(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Manually trigger the 3:25 PM entry condition check."""
    try:
        engine = get_strategy_engine()
        res = engine.evaluate_and_execute_325_entries()
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"3:25 PM entry evaluation failed: {exc}")


@router.post("/engine/evaluate-325-exits")
def trigger_325_exit_evaluation(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Manually trigger the 3:25 PM Supertrend RED exit check."""
    try:
        engine = get_strategy_engine()
        res = engine.evaluate_and_execute_325_exits()
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"3:25 PM exit evaluation failed: {exc}")


@router.post("/engine/monitor-ltp")
def trigger_ltp_monitoring(_: CurrentUser = Depends(require_admin)):
    """ADMIN only. Returns WebSocket monitoring status."""
    engine = get_trade_engine()
    return {
        "status": "ws_driven",
        "description": "LTP monitoring is fully event-driven via Dhan WebSocket feed.",
        "active_monitored_trades": len(engine.get_all_snapshots()) if engine else 0,
    }
