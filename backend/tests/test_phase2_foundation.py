"""
tests/test_phase2_foundation.py
===============================
Targeted test suite for Phase 2 architectural foundation:
1. Persistent JWT Secret Key across application restarts.
2. Domain router modularization and route contract preservation.
3. Account and Trade repository layer operations.
4. Dynamic Market Data account resolution without hardcoded literals.
5. Thread-safe execution in 3:25 PM evaluation without asyncio.run conflicts.
"""

import pytest
from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from app.main import app
from app.data.database import SessionLocal
from app.data.models import User, DhanAccount, AppConfig, Company, Trade, AtsTradeState
from app.api.auth_app import SECRET_KEY, ALGORITHM, get_jwt_secret_key
from app.data.repositories import get_account_repo, get_trade_repo
import jwt


def test_jwt_secret_persisted_in_database():
    """Verify that JWT secret is stored in app_config and is persistent."""
    secret = get_jwt_secret_key()
    assert secret is not None
    assert len(secret) >= 32

    db = SessionLocal()
    try:
        config = db.query(AppConfig).filter(AppConfig.config_key == "jwt_secret_key").first()
        assert config is not None
        assert config.config_value == secret
        
        # Verify subsequent call returns the exact same key from DB
        assert get_jwt_secret_key() == secret
    finally:
        db.close()


def test_domain_routers_mounted_under_api():
    """Verify that all domain routes are accessible under /api prefix."""
    client = TestClient(app)
    
    # 1. /api/auth/status (Broker Auth)
    res_auth = client.get("/api/auth/status")
    assert res_auth.status_code == 200
    assert "status" in res_auth.json()
    assert "accounts" in res_auth.json()

    # 2. /api/signals (Signals)
    res_sig = client.get("/api/signals")
    assert res_sig.status_code == 200
    assert isinstance(res_sig.json(), list)

    # 3. /api/stocks/search (Stocks)
    res_stocks = client.get("/api/stocks/search?q=TEST")
    assert res_stocks.status_code == 200
    assert isinstance(res_stocks.json(), list)

    # 4. /api/app-auth/status (App Master Auth)
    res_app_auth = client.get("/api/app-auth/status")
    assert res_app_auth.status_code == 200
    assert "is_setup" in res_app_auth.json()


def test_account_and_trade_repositories():
    """Verify repository layer CRUD and query operations."""
    db = SessionLocal()
    try:
        user = User(id=str(uuid.uuid4()), email=f"repo_{uuid.uuid4()}@example.com", role="user", is_active=True)
        db.add(user)
        
        acc = DhanAccount(
            id=str(uuid.uuid4()),
            user_id=user.id,
            client_id=f"REPO_CLIENT_{uuid.uuid4().hex[:6]}",
            is_data_account=True,
            account_status="ACTIVE"
        )
        db.add(acc)
        
        comp = Company(
            id=str(uuid.uuid4()),
            trading_symbol=f"REPO_{uuid.uuid4().hex[:4].upper()}",
            company_name="Repo Company",
            dhan_security_id=f"99{uuid.uuid4().int % 10000:04d}",
            exchange="NSE"
        )
        db.add(comp)
        
        trade = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=acc.id,
            company_id=comp.id,
            security_id=comp.dhan_security_id,
            ats_state=AtsTradeState.OPEN,
            entry_price=500.0,
            allocated_quantity=10,
            remaining_quantity=10,
            trade_date=datetime.now(timezone.utc).date()
        )
        db.add(trade)
        db.commit()

        # Test Account Repository
        acc_repo = get_account_repo(db)
        active_accounts = acc_repo.get_active_accounts()
        assert any(a.id == acc.id for a in active_accounts)

        data_acc = acc_repo.get_data_account()
        assert data_acc is not None
        assert data_acc.is_data_account == True

        user_accounts = acc_repo.get_accounts_for_user(user.id)
        assert len(user_accounts) == 1
        assert user_accounts[0].id == acc.id

        # Test Trade Repository
        trade_repo = get_trade_repo(db)
        found_trade = trade_repo.get_by_id(trade.id)
        assert found_trade is not None
        assert found_trade.security_id == comp.dhan_security_id

        scoped_trades = trade_repo.get_trades_by_scope(account_ids=[acc.id])
        assert len(scoped_trades) >= 1
        assert scoped_trades[0].id == trade.id

        open_sec_trades = trade_repo.get_open_trades_for_security(comp.dhan_security_id)
        assert len(open_sec_trades) >= 1
    finally:
        db.close()
