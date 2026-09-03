"""
api/portfolio.py — Portfolio, Stock Search & Candle Sync Endpoints
==================================================================
Provides REST endpoints for live broker portfolio data, company searches,
and manual candle syncing:
- GET  /api/portfolio/summary
- GET  /api/portfolio/funds
- GET  /api/portfolio/holdings
- GET  /api/portfolio/positions
- GET  /api/portfolio/trades
- GET  /api/db/holdings
- GET  /api/db/positions
- GET  /api/stocks/search
- GET  /api/stocks/{symbol}
- GET  /api/companies/images
- POST /api/candles/sync
- POST /api/candles/sync/{symbol}
"""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.auth import get_current_user
from database.database import get_db, SessionLocal
from database.models import Company, Holding, Position, Portfolio
from dhan.portfolio import PortfolioService
from market.candles import (
    sync_candles_for_company,
    sync_all_active_companies,
    get_daily_candles_from_db,
    get_weekly_candles_from_db,
    get_monthly_candles_from_db,
)

logger = logging.getLogger("ats.api.portfolio")

router = APIRouter(tags=["Portfolio & Stocks"])
_portfolio_service = PortfolioService()


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE BROKER PORTFOLIO ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/portfolio/summary")
def get_portfolio_summary(current_user: str = Depends(get_current_user)):
    """Full live broker account snapshot."""
    try:
        return _portfolio_service.get_full_broker_summary()
    except Exception as exc:
        logger.error(f"[PORTFOLIO] Failed to get portfolio summary: {exc}")
        raise HTTPException(status_code=502, detail=f"Dhan Broker API Error: {str(exc)}")


@router.get("/api/portfolio/funds")
def get_portfolio_funds(current_user: str = Depends(get_current_user)):
    """Live fund limits and margin balances from Dhan."""
    try:
        return _portfolio_service.get_fund_limits()
    except Exception as exc:
        logger.error(f"[PORTFOLIO] Failed to get fund limits: {exc}")
        raise HTTPException(status_code=502, detail=f"Dhan Broker API Error: {str(exc)}")


@router.get("/api/portfolio/holdings")
def get_portfolio_holdings(current_user: str = Depends(get_current_user)):
    """Live holdings from Dhan."""
    try:
        return _portfolio_service.get_holdings()
    except Exception as exc:
        logger.error(f"[PORTFOLIO] Failed to get holdings: {exc}")
        raise HTTPException(status_code=502, detail=f"Dhan Broker API Error: {str(exc)}")


@router.get("/api/portfolio/positions")
def get_portfolio_positions(current_user: str = Depends(get_current_user)):
    """Live open and closed intraday/CNC positions from Dhan."""
    try:
        return _portfolio_service.get_positions()
    except Exception as exc:
        logger.error(f"[PORTFOLIO] Failed to get positions: {exc}")
        raise HTTPException(status_code=502, detail=f"Dhan Broker API Error: {str(exc)}")


@router.get("/api/portfolio/trades")
def get_portfolio_trades(current_user: str = Depends(get_current_user)):
    """Executed trade history from Dhan."""
    try:
        return _portfolio_service.get_trades()
    except Exception as exc:
        logger.error(f"[PORTFOLIO] Failed to get trades: {exc}")
        raise HTTPException(status_code=502, detail=f"Dhan Broker API Error: {str(exc)}")


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE PORTFOLIO SNAPSHOTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/db/holdings")
def get_db_holdings(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Database holdings records."""
    return db.query(Holding).order_by(Holding.captured_at.desc()).all()


@router.get("/api/db/positions")
def get_db_positions(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Database positions records."""
    return db.query(Position).order_by(Position.captured_at.desc()).all()


# ═══════════════════════════════════════════════════════════════════════════════
# STOCK SEARCH & COMPANY DETAILS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/stocks/search")
def search_stocks(
    q: str = Query("", description="Search term for symbol or company name"),
    limit: int = Query(20, description="Max results"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """Search tracked companies by symbol or name."""
    if not q:
        companies = db.query(Company).filter(Company.is_active == True).limit(limit).all()
    else:
        term = f"%{q.upper()}%"
        companies = (
            db.query(Company)
            .filter(
                Company.is_active == True,
                (Company.trading_symbol.ilike(term)) | (Company.company_name.ilike(term))
            )
            .limit(limit)
            .all()
        )
    return companies


@router.get("/api/stocks/{symbol}")
def get_stock_detail(symbol: str, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Get company information and recent candles by trading symbol."""
    company = db.query(Company).filter(Company.trading_symbol == symbol.upper()).first()
    if not company:
        raise HTTPException(status_code=404, detail=f"Stock {symbol} not found")

    daily = get_daily_candles_from_db(company.id, limit=60)
    weekly = get_weekly_candles_from_db(company.id, limit=30)
    monthly = get_monthly_candles_from_db(company.id, limit=12)

    return {
        "company": company,
        "daily_candles": daily,
        "weekly_candles": weekly,
        "monthly_candles": monthly
    }


@router.get("/api/companies/images")
def get_company_images(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Retrieve map of trading_symbol to company logo URL."""
    rows = db.query(Company.trading_symbol, Company.img_url).filter(Company.img_url != None).all()
    return {symbol: url for symbol, url in rows}


# ═══════════════════════════════════════════════════════════════════════════════
# CANDLE SYNC ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/api/candles/sync")
def trigger_full_candle_sync(current_user: str = Depends(get_current_user)):
    """Manually trigger candle synchronization for all active companies."""
    summary = sync_all_active_companies(limit=4000)
    return summary


@router.post("/api/candles/sync/{symbol}")
def trigger_symbol_candle_sync(symbol: str, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    """Manually trigger candle sync for a specific stock."""
    company = db.query(Company).filter(Company.trading_symbol == symbol.upper()).first()
    if not company or not company.dhan_security_id:
        raise HTTPException(status_code=404, detail=f"Stock {symbol} not found or missing security ID")

    result = sync_candles_for_company(
        company_id=company.id,
        security_id=company.dhan_security_id,
        exchange_segment=company.segment or "NSE_EQ",
        force_full=True,
        symbol=company.trading_symbol
    )
    return result
