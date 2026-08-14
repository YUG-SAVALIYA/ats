"""
app/main.py — ATS FastAPI Application Entry Point
==================================================
Production-grade automated trading backend with:
1. PostgreSQL Database as single source of truth.
2. Production-safe TradeCacheManager.
3. Startup & 30s periodic Dhan BrokerReconciler (10/10 Reliability Architecture).
"""

import os
import asyncio
import glob
import uvicorn
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request

import sys
# Allow running `python main.py` directly from inside the app directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config
from app.services.dhan_client import get_dhan_client
from app.services.market_feed import init_market_feed_manager
from app.core.engine import init_trade_engine
from app.core.executor import place_market_sell, get_order_executor
from app.core.reconciler import init_reconciler
from app.core.broker_reconciler import init_broker_reconciler
from app.scheduler import start_scheduler, stop_scheduler
from app.api.router import router as api_router

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)


class DailyDateFileHandler(logging.FileHandler):
    """Writes to logs/YYYY-MM-DD.log. Retains 60 daily files."""
    def __init__(self, logs_dir: str, retention_days: int = 60):
        self.logs_dir = logs_dir
        self.retention_days = retention_days
        self.current_date_str = datetime.now().strftime("%Y-%m-%d")
        super().__init__(
            os.path.join(self.logs_dir, f"{self.current_date_str}.log"),
            mode="a", encoding="utf-8",
        )
        self._cleanup_old_logs()

    def emit(self, record):
        now_date = datetime.now().strftime("%Y-%m-%d")
        if now_date != self.current_date_str:
            self.current_date_str = now_date
            self.close()
            self.baseFilename = os.path.abspath(
                os.path.join(self.logs_dir, f"{now_date}.log")
            )
            self.stream = self._open()
            self._cleanup_old_logs()
        super().emit(record)

    def _cleanup_old_logs(self):
        try:
            log_files = sorted(glob.glob(os.path.join(self.logs_dir, "*.log")))
            for old_file in log_files[:-self.retention_days]:
                try:
                    os.remove(old_file)
                except Exception:
                    pass
        except Exception:
            pass


_file_handler = DailyDateFileHandler(LOGS_DIR, retention_days=60)
_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_file_handler.setFormatter(_formatter)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [_file_handler, _console_handler]

logger = logging.getLogger("ats.main")


from app.services.dhan_client import get_dhan_client, get_dhan_data_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    trade_client = get_dhan_client()
    data_client = get_dhan_data_client()

    executor = get_order_executor()

    ws_manager = init_market_feed_manager(
        client_id=data_client.client_id or "",
        get_token_fn=data_client.auth_manager.get_valid_token,
    )

    engine = init_trade_engine(place_market_sell_fn=place_market_sell, ws_manager=ws_manager)
    ws_manager.register_tick_callback(engine.on_tick)

    # 1. Startup Cache Recovery: Rebuild cache from PostgreSQL DB
    recovered_count = await engine.recover_from_db()
    active_ids = engine.get_active_security_ids()

    # 2. Broker Reconciliation on Startup (Sync live broker positions & orders)
    broker_reconciler = init_broker_reconciler(
        confirm_fill_fn=executor.confirm_entry_fill,
        ws_manager=ws_manager
    )
    await broker_reconciler.reconcile_on_startup()

    # Refresh active security IDs after broker reconciliation pass
    active_ids = set(engine.get_active_security_ids())
    
    # Strictly synchronize the ActiveSubscription DB table with the actual active trades
    from app.database import SessionLocal
    from app.models import ActiveSubscription
    db = SessionLocal()
    try:
        # Get all currently saved subscriptions in DB
        db_subs = db.query(ActiveSubscription).all()
        db_sec_ids = {sub.security_id for sub in db_subs}
        
        # Remove old/zombie subscriptions from DB that are no longer active
        for old_id in db_sec_ids - active_ids:
            db.query(ActiveSubscription).filter_by(security_id=old_id).delete()
            logger.info(f"[STARTUP] Removed stale/closed stock {old_id} from ActiveSubscription DB.")
            
        # Add any missing active subscriptions to DB
        for missing_id in active_ids - db_sec_ids:
            db.add(ActiveSubscription(security_id=missing_id))
            
        db.commit()
    except Exception as e:
        logger.error(f"[STARTUP] Error syncing ActiveSubscription DB: {e}")
        db.rollback()
    finally:
        db.close()

    if active_ids:
        logger.info(f"[STARTUP] Found {len(active_ids)} active trades. Starting websocket feed...")
        await ws_manager.subscribe(list(active_ids))
    else:
        logger.info("[STARTUP] No active trades found. WebSocket feed will remain idle until needed.")

    logger.info(f"[STARTUP] TradeEngine started — {recovered_count} active trade(s) recovered and broker-reconciled.")

    # 3. Pending order fill reconciler (polls Dhan every 5s for ENTRY_PENDING)
    reconciler = init_reconciler(
        confirm_fill_fn=executor.confirm_entry_fill,
        register_trade_fn=engine.register_trade,
        subscribe_fn=ws_manager.subscribe,
    )
    reconciler_task = asyncio.create_task(reconciler.run())

    # 4. Start 30s Periodic Broker Reconciliation Task
    broker_task = asyncio.create_task(broker_reconciler.run())

    # 5. Cache Consistency Auditor Background Task
    async def _auditor_loop():
        from app.core.cache_manager import get_cache_manager
        cache_manager = get_cache_manager()
        while True:
            try:
                await asyncio.sleep(60.0)
                audit_result = await cache_manager.audit_consistency(ws_manager=ws_manager)
                if audit_result.get("status") == "REPAIRED":
                    logger.info("[CACHE_AUDIT] Synchronizing WebSocket and ActiveSubscription table after repair.")
                    active_ids = set(engine.get_active_security_ids())
                    
                    from app.database import SessionLocal
                    from app.models import ActiveSubscription
                    db = SessionLocal()
                    try:
                        db_subs = db.query(ActiveSubscription).all()
                        db_sec_ids = {sub.security_id for sub in db_subs}
                        
                        for old_id in db_sec_ids - active_ids:
                            db.query(ActiveSubscription).filter_by(security_id=old_id).delete()
                            await ws_manager.unsubscribe([old_id])
                            
                        for missing_id in active_ids - db_sec_ids:
                            db.add(ActiveSubscription(security_id=missing_id))
                            await ws_manager.subscribe([missing_id])
                            
                        db.commit()
                    except Exception as e:
                        logger.error(f"[CACHE_AUDIT] Error syncing ActiveSubscription DB: {e}")
                        db.rollback()
                    finally:
                        db.close()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[CACHE_AUDIT] Loop error: {e}")
                await asyncio.sleep(60.0)
                
    auditor_task = asyncio.create_task(_auditor_loop())

    # 6. Tick Health Monitor
    tick_health_task = asyncio.create_task(engine.tick_health_loop())

    # 7. APScheduler
    start_scheduler()

    logger.info("[STARTUP] ATS backend 10/10 architecture online (DB + Cache Recovery + Broker Sync).")

    yield

    await reconciler.stop()
    await broker_reconciler.stop()
    reconciler_task.cancel()
    broker_task.cancel()
    auditor_task.cancel()
    tick_health_task.cancel()
    try:
        await reconciler_task
        await broker_task
        await auditor_task
        await tick_health_task
    except asyncio.CancelledError:
        pass

    await ws_manager.stop()
    stop_scheduler()
    logger.info("[SHUTDOWN] ATS backend stopped cleanly")


app = FastAPI(
    title="ATS — Automated Trading System (Dhan Live)",
    description=(
        "Production-grade 10/10 automated trading backend. "
        "Dhan is used for MARKET order execution and WebSocket LTP data. "
        "Includes PostgreSQL DB single source of truth, TradeCacheManager, and BrokerReconciler."
    ),
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"[HTTP] {request.method} {request.url.path}")
    response = await call_next(request)
    logger.info(f"[HTTP] {request.method} {request.url.path} → {response.status_code}")
    return response


from app.api.auth_app import router as auth_router
app.include_router(auth_router)
app.include_router(api_router)


@app.get("/")
def root():
    return {
        "platform": "ATS — Automated Trading System",
        "status": "online",
        "architecture_rating": "10/10",
        "mode": "LIVE_MARKET_ORDERS_ONLY",
        "version": "2.1.0",
        "docs": "/docs",
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8005))
    host = os.getenv("HOST", "localhost")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)
