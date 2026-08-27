"""
database/repositories.py — Centralized Database Access & Query Repositories
===========================================================================
Clean data access helpers for companies, signals, trades, active subscriptions,
events, and credentials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, date
from typing import Optional, List, Set, Dict, Any
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import (
    Company, Signal, Trade, AtsOrder, TradeEvent,
    ActiveSubscription, Holding, Position, Portfolio
)

logger = logging.getLogger("ats.repositories")


def log_trade_event(
    db: Session,
    trade_id: str,
    event_type: str,
    detail: str = "",
    price: Optional[float] = None,
    quantity: Optional[int] = None,
) -> None:
    """Safely records an event to the trade_events audit log."""
    try:
        db.add(TradeEvent(
            trade_id=trade_id,
            event_type=event_type,
            detail=detail,
            price=price,
            quantity=quantity,
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    except Exception as exc:
        logger.warning(f"[REPO] Event log failed ({event_type}) for {trade_id}: {exc}")
        try:
            db.rollback()
        except Exception:
            pass


def get_company_by_id(db: Session, company_id: str) -> Optional[Company]:
    """Finds a company by primary key UUID."""
    return db.query(Company).filter(Company.id == company_id).first()


def get_company_by_security_or_symbol(
    db: Session, security_id: Optional[str] = None, symbol: Optional[str] = None
) -> Optional[Company]:
    """Finds a company matching security ID or symbol."""
    if security_id:
        comp = db.query(Company).filter(Company.dhan_security_id == str(security_id)).first()
        if comp:
            return comp
    if symbol:
        return db.query(Company).filter(Company.trading_symbol == symbol.upper()).first()
    return None


def get_active_companies(db: Session, limit: int = 4000) -> List[Company]:
    """Retrieves all active companies with a valid Dhan security ID."""
    return (
        db.query(Company)
        .filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != "",
        )
        .limit(limit)
        .all()
    )


def sync_active_subscriptions_in_db(active_sec_ids: Set[str]) -> None:
    """Strictly synchronizes active_subscriptions table with the provided active security IDs."""
    db = SessionLocal()
    try:
        db_subs = db.query(ActiveSubscription).all()
        db_sec_ids = {sub.security_id for sub in db_subs}

        # Remove stale subscriptions
        for old_id in db_sec_ids - active_sec_ids:
            db.query(ActiveSubscription).filter_by(security_id=old_id).delete()
            logger.info(f"[REPO] Removed stale security {old_id} from ActiveSubscription DB.")

        # Add missing subscriptions
        for missing_id in active_sec_ids - db_sec_ids:
            db.add(ActiveSubscription(security_id=missing_id))

        db.commit()
    except Exception as e:
        logger.error(f"[REPO] Error syncing ActiveSubscription table: {e}")
        db.rollback()
    finally:
        db.close()
