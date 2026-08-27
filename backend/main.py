"""
main.py — ATS FastAPI Application & Production Lifespan Engine
==============================================================
Central entry point configuring CORS, daily rotating file logging,
modular API routers, WebSocket feed processing, background worker tasks,
and static frontend SPA serving.
"""

from __future__ import annotations

import asyncio
import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Ensure backend root is in sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# ═══════════════════════════════════════════════════════════════════════════════
# DAILY ROTATING FILE LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

LOG_DIR = backend_dir / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "ats.log"

file_handler = TimedRotatingFileHandler(
    filename=str(LOG_FILE),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8"
)
file_handler.suffix = "%Y-%m-%d"
file_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
file_handler.setFormatter(file_formatter)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(file_formatter)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)

logger = logging.getLogger("ats.main")

# ═══════════════════════════════════════════════════════════════════════════════
# ATS IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════

from config import load_config
from database.database import init_database
from dhan.client import get_dhan_client, get_dhan_data_client
from dhan.websocket import init_market_feed_manager
from trading.orders import place_market_sell, get_order_executor
from trading.trade_manager import init_trade_engine
from workers.reconciler import init_reconciler, init_broker_reconciler
from workers.market_monitor import tick_health_loop, cache_auditor_loop
from workers.scheduler import start_scheduler, stop_scheduler

# API Routers
from api.auth import router as app_auth_router
from api.accounts import router as broker_auth_router
from api.signals import router as signals_router
from api.trades import router as trades_router
from api.portfolio import router as portfolio_router
from api.settings import router as settings_router


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Production lifespan manager initializing all subsystems cleanly."""
    logger.info("══════════════════════════════════════════════════════════════")
    logger.info("  ATS TRADING SYSTEM INITIALIZING (TARGET ARCHITECTURE)       ")
    logger.info("══════════════════════════════════════════════════════════════")

    # 1. Database Schema Initializer
    init_database()
    logger.info("[STARTUP] PostgreSQL database tables verified.")

    # 2. Config & Dhan Clients
    cfg = load_config()
    trade_client = get_dhan_client()
    data_client = get_dhan_data_client()
    logger.info(f"[STARTUP] Config loaded. Execution Client: ***{trade_client.client_id[-4:] if trade_client.client_id else 'NONE'}")

    # 3. Market Feed WebSocket Manager (uses DataAccount with active data subscription)
    ws_client = data_client if data_client.client_id else trade_client
    ws_manager = init_market_feed_manager(
        client_id=ws_client.client_id,
        get_token_fn=ws_client.auth_manager.get_valid_token
    )

    # 4. Central Trade Execution Engine
    trade_engine = init_trade_engine(
        place_market_sell_fn=place_market_sell,
        ws_manager=ws_manager
    )
    ws_manager.register_tick_callback(trade_engine.on_tick)

    # 5. Order & Broker Reconcilers
    order_executor = get_order_executor()
    order_reconciler = init_reconciler(
        confirm_fill_fn=order_executor.confirm_entry_fill,
        register_trade_fn=trade_engine.register_trade,
        subscribe_fn=ws_manager.subscribe
    )
    broker_reconciler = init_broker_reconciler(
        confirm_fill_fn=order_executor.confirm_entry_fill,
        ws_manager=ws_manager
    )

    # 6. Database Recovery & Startup Broker Reconciliation
    await trade_engine.recover_from_db()
    await broker_reconciler.reconcile_on_startup()

    # 7. Start Market Feed WebSocket if active trades exist
    active_sec_ids = trade_engine.get_active_security_ids()
    if active_sec_ids:
        logger.info(f"[STARTUP] Subscribing {len(active_sec_ids)} active securities to market feed: {active_sec_ids}")
        await ws_manager.subscribe(active_sec_ids)

    # 8. Start Background Async Tasks
    bg_tasks = [
        asyncio.create_task(order_reconciler.run()),
        asyncio.create_task(broker_reconciler.run()),
        asyncio.create_task(tick_health_loop(ws_manager=ws_manager)),
        asyncio.create_task(cache_auditor_loop(ws_manager=ws_manager)),
    ]

    # 9. Start APScheduler Background Cron Jobs
    start_scheduler()

    logger.info("══════════════════════════════════════════════════════════════")
    logger.info("  ATS TRADING SYSTEM READY AND RUNNING                         ")
    logger.info("══════════════════════════════════════════════════════════════")

    yield

    # Shutdown sequence
    logger.info("[SHUTDOWN] Stopping ATS systems...")
    stop_scheduler()
    await order_reconciler.stop()
    await broker_reconciler.stop()
    await ws_manager.stop()
    for t in bg_tasks:
        t.cancel()
    logger.info("[SHUTDOWN] ATS shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION SETUP
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="ATS Automated Trading System",
    description="High-frequency, resilient algorithmic trading terminal powered by Dhan HQ API v2.",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logger_middleware(request: Request, call_next):
    if not request.url.path.startswith("/assets"):
        logger.debug(f"[HTTP] {request.method} {request.url.path}")
    return await call_next(request)


# Include API Routers
app.include_router(app_auth_router)
app.include_router(broker_auth_router)
app.include_router(signals_router)
app.include_router(trades_router)
app.include_router(portfolio_router)
app.include_router(settings_router)


from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL EXCEPTION HANDLERS (STRICT STATUS CODES & STRUCTURED ERRORS)
# ═══════════════════════════════════════════════════════════════════════════════

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Ensure HTTP exceptions return proper status code and standard error payload."""
    logger.warning(f"[HTTP {exc.status_code}] {request.method} {request.url.path}: {exc.detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": "HTTPException",
            "detail": exc.detail,
            "status_code": exc.status_code,
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Ensure request schema validation failures return HTTP 422 with actionable detail."""
    err_msgs = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "Invalid value")
        err_msgs.append(f"{loc}: {msg}")
    detail_str = "; ".join(err_msgs) if err_msgs else str(exc.errors())
    logger.warning(f"[HTTP 422] Validation error on {request.method} {request.url.path}: {detail_str}")
    return JSONResponse(
        status_code=422,
        content={
            "status": "error",
            "error": "ValidationError",
            "detail": detail_str,
            "status_code": 422,
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch unhandled runtime exceptions and return HTTP 500 without masking."""
    logger.error(f"[HTTP 500] Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": "InternalServerError",
            "detail": str(exc) or "Internal server error occurred",
            "status_code": 500,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STATIC ASSETS & FRONTEND SPA CATCH-ALL
# ═══════════════════════════════════════════════════════════════════════════════

frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
assets_path = frontend_dist / "assets"

if assets_path.exists() and assets_path.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="static_assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serves the React frontend single-page application."""
    if full_path.startswith("api/"):
        raise HTTPException(
            status_code=404,
            detail=f"API endpoint '/{full_path}' not found"
        )

    index_html = frontend_dist / "index.html"
    if index_html.exists():
        return FileResponse(index_html)
    return {"message": "ATS API is running. Build the frontend to view the UI."}
