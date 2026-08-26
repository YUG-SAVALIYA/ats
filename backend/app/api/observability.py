"""
app.api.observability
=====================
Production observability, subsystem health probes, and audit traceability endpoints.
"""

from typing import Dict, Any, List, Optional
import time
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.data.database import get_db, SessionLocal
from app.data.models import Trade, AtsOrder, OrderAttempt, TradeEvent, DhanAccount, AccountStatus
from app.api.auth_app import get_current_user, require_admin, get_tenant_scope, CurrentUser
from app.trading.risk import PreTradeSafetyValidator
from app.trading.cache import get_cache_manager
from app.broker.dhan_websocket import get_market_feed_manager
from app.workers.scheduler import scheduler

logger = logging.getLogger("ats.observability")

router = APIRouter(tags=["Observability & Health"])


class KillSwitchRequest(BaseModel):
    enabled: bool


@router.get("/health")
@router.get("/observability/health")
def get_system_health(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    Comprehensive multi-system health probe.
    Monitors: DB latency, Dhan WebSocket connection, Scheduler, Broker Accounts, Engine, and Kill Switch.
    """
    # 1. Database Health & Latency
    db_status = "HEALTHY"
    db_latency_ms = 0.0
    try:
        t0 = time.time()
        db.execute(text("SELECT 1")).scalar()
        db_latency_ms = round((time.time() - t0) * 1000, 2)
    except Exception as exc:
        db_status = f"DOWN ({exc})"

    # 2. Kill Switch Status
    kill_switch_active = False
    try:
        kill_switch_active = PreTradeSafetyValidator.is_kill_switch_active(db)
    except Exception:
        kill_switch_active = False

    # 3. Dhan WebSocket Feed Status
    ws_status = "DISCONNECTED"
    subscribed_count = 0
    last_tick_time = None
    try:
        ws_mgr = get_market_feed_manager()
        if ws_mgr:
            ws_status = "CONNECTED" if getattr(ws_mgr, "is_connected", False) else "DISCONNECTED"
            subscribed_count = len(getattr(ws_mgr, "subscribed_ids", []))
            last_tick_time = getattr(ws_mgr, "last_tick_timestamp", None)
    except Exception:
        pass

    # 4. APScheduler Health
    scheduler_running = scheduler.running if scheduler else False
    registered_jobs = []
    if scheduler_running:
        for job in scheduler.get_jobs():
            registered_jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": str(job.next_run_time) if job.next_run_time else None
            })

    # 5. Broker Accounts
    active_accounts_count = 0
    if db_status == "HEALTHY":
        try:
            active_accounts_count = db.query(DhanAccount).filter(DhanAccount.account_status == AccountStatus.ACTIVE).count()
        except Exception:
            pass

    # 6. Cache & Trade Engine Status
    cache_mgr = get_cache_manager()
    active_cached_trades = len(cache_mgr.get_all_active_trades()) if cache_mgr else 0

    # Determine Overall Health Status
    if db_status != "HEALTHY":
        overall_status = "DOWN"
    elif kill_switch_active:
        overall_status = "KILL_SWITCH_ACTIVE"
    elif active_accounts_count == 0:
        overall_status = "DEGRADED"
    else:
        overall_status = "HEALTHY"

    return {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kill_switch_active": kill_switch_active,
        "database": {
            "status": db_status,
            "latency_ms": db_latency_ms
        },
        "websocket": {
            "status": ws_status,
            "subscribed_symbols_count": subscribed_count,
            "last_tick_time": last_tick_time
        },
        "scheduler": {
            "running": scheduler_running,
            "jobs_count": len(registered_jobs),
            "jobs": registered_jobs
        },
        "broker": {
            "active_accounts": active_accounts_count
        },
        "trading_engine": {
            "active_cached_trades": active_cached_trades
        }
    }


@router.get("/engine/kill-switch")
def get_kill_switch_status(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Check current status of the emergency kill switch."""
    active = PreTradeSafetyValidator.is_kill_switch_active(db)
    return {
        "kill_switch_active": active,
        "status": "KILL_SWITCH_ACTIVE" if active else "NORMAL"
    }


@router.post("/engine/kill-switch")
def toggle_kill_switch(
    req: KillSwitchRequest,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin)
) -> Dict[str, Any]:
    """Emergency Kill Switch."""
    result = PreTradeSafetyValidator.set_kill_switch(req.enabled, db)
    status_str = "ACTIVE (Entries Blocked)" if result else "DISABLED (Normal Trading)"
    
    try:
        admin_email = getattr(admin, "sub", str(admin))
        db.add(TradeEvent(
            dhan_account_id=None,
            trade_id=None,
            event_type="KILL_SWITCH_TOGGLED",
            detail=f"Admin {admin_email} set Kill Switch to {status_str}",
            created_at=datetime.now(timezone.utc)
        ))
        db.commit()
    except Exception:
        pass

    logger.warning(f"[KILL_SWITCH] Admin set Kill Switch to: {status_str}")
    return {
        "status": "success",
        "kill_switch_active": result,
        "message": f"Emergency Kill Switch is now {status_str}"
    }


@router.get("/observability/audit")
def get_audit_trace_history(
    trade_id: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
    scope: dict = Depends(get_tenant_scope)
) -> List[Dict[str, Any]]:
    """Returns structured audit trace history."""
    query = db.query(TradeEvent)
    
    is_admin = scope.get("is_admin", False) if isinstance(scope, dict) else getattr(scope, "is_admin", False)
    if not is_admin:
        account_ids = scope.get("account_ids", []) if isinstance(scope, dict) else getattr(scope, "account_ids", None)
        if account_ids is not None:
            query = query.filter(TradeEvent.dhan_account_id.in_(account_ids))
        
    if trade_id:
        query = query.filter(TradeEvent.trade_id == trade_id)
        
    events = query.order_by(TradeEvent.created_at.desc()).limit(limit).all()
    
    result = []
    for ev in events:
        result.append({
            "id": ev.id,
            "dhan_account_id": ev.dhan_account_id,
            "trade_id": ev.trade_id,
            "event_type": ev.event_type,
            "detail": ev.detail,
            "price": ev.price,
            "quantity": ev.quantity,
            "created_at": ev.created_at.isoformat() if ev.created_at else None
        })
    return result
