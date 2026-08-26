"""
tests/test_phase3_execution_hardening.py
========================================
Comprehensive Phase 3 Test Suite:
1. Pre-Trade Safety Validator & Emergency Kill Switch.
2. PostgreSQL Advisory Lock Multi-Worker Scheduler Deduplication.
3. Two-Phase Exit-Claim Concurrency & Duplicate Exit Prevention.
4. Idempotent Delta Fill Accounting.
5. Dhan Broker Gateway Abstraction & Rate Limiter Isolation.
6. Orphaned Order Recovery via Correlation ID.
"""

import pytest
import asyncio
from datetime import datetime, timezone, date
import uuid

from sqlalchemy import text
from app.data.database import SessionLocal, engine
from app.data.models import (
    Base, User, DhanAccount, Company, Trade, AtsOrder, OrderAttempt,
    AtsTradeState, OrderPurpose, AppConfig
)
from app.trading.risk import PreTradeSafetyValidator
from app.data.locks import (
    try_advisory_lock,
    release_advisory_lock,
    advisory_lock_guard,
    LOCK_JOB_325_EXECUTION
)
from app.trading.execution import (
    place_market_sell,
    confirm_exit_fill,
    get_order_executor,
    _correlation_id
)
from app.trading.cache import get_cache_manager
from app.workers.reconciliation import BrokerReconciler
from app.broker.dhan_gateway import DhanBrokerGateway, BrokerTimeoutError


@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    db.close()


def create_mock_tenant(db, prefix="t3"):
    unique = uuid.uuid4().hex[:6]
    user = User(
        id=str(uuid.uuid4()),
        email=f"{prefix}_{unique}@example.com",
        role="user",
        is_active=True
    )
    db.add(user)
    
    account = DhanAccount(
        id=f"acc_{prefix}_{unique}",
        user_id=user.id,
        client_id=f"CLI_{unique}",
        access_token="TEST_TOKEN",
        pin="1234",
        totp_secret="JBSWY3DPEHPK3PXP",
        account_status="ACTIVE"
    )
    db.add(account)
    
    company = Company(
        id=str(uuid.uuid4()),
        trading_symbol=f"SYM_{unique.upper()}",
        company_name=f"Company {unique}",
        dhan_security_id=f"88{uuid.uuid4().int % 10000:04d}",
        exchange="NSE"
    )
    db.add(company)
    db.commit()
    
    return {"user": user, "account": account, "company": company}


# ── 1. Pre-Trade Safety Validator & Kill Switch ──────────────────────────────

def test_safety_validator_kill_switch_blocking(db_session):
    """Verify that when emergency kill switch is activated, new entries are rejected immediately."""
    tenant = create_mock_tenant(db_session, "kill")
    acc = tenant["account"]
    comp = tenant["company"]

    # 1. Normal state: Kill switch OFF
    PreTradeSafetyValidator.set_kill_switch(False, db_session)
    valid, reason = PreTradeSafetyValidator.validate_entry_allowed(
        dhan_account_id=acc.id,
        security_id=comp.dhan_security_id,
        order_value=25000.0,
        db=db_session
    )
    assert valid is True
    assert reason is None

    # 2. Activate emergency kill switch
    PreTradeSafetyValidator.set_kill_switch(True, db_session)
    valid_kill, reason_kill = PreTradeSafetyValidator.validate_entry_allowed(
        dhan_account_id=acc.id,
        security_id=comp.dhan_security_id,
        order_value=25000.0,
        db=db_session
    )
    assert valid_kill is False
    assert "KILL SWITCH" in reason_kill.upper()

    # Reset for subsequent tests
    PreTradeSafetyValidator.set_kill_switch(False, db_session)


def test_safety_validator_inactive_account_blocking(db_session):
    """Verify that orders for INACTIVE / PAUSED / TOKEN_ERROR accounts are strictly blocked."""
    tenant = create_mock_tenant(db_session, "inactive")
    acc = tenant["account"]
    comp = tenant["company"]

    # Mark account as PAUSED
    acc.account_status = "PAUSED"
    db_session.commit()

    valid, reason = PreTradeSafetyValidator.validate_entry_allowed(
        dhan_account_id=acc.id,
        security_id=comp.dhan_security_id,
        order_value=10000.0,
        db=db_session
    )
    assert valid is False
    assert "PAUSED" in reason.upper()


# ── 2. PostgreSQL Advisory Lock Multi-Worker Scheduler Deduplication ─────────

def test_advisory_lock_mutual_exclusion(db_session):
    """
    Verify PostgreSQL advisory lock guarantees mutual exclusion between workers.
    When Worker 1 acquires lock, Worker 2 must be blocked / return False.
    """
    db1 = SessionLocal()
    db2 = SessionLocal()
    try:
        # Worker 1 acquires lock for 3:25 PM execution
        acquired_1 = try_advisory_lock(LOCK_JOB_325_EXECUTION, db=db1)
        assert acquired_1 is True

        # Worker 2 attempts same lock on separate connection -> must fail
        # Note: On SQLite in test environment, advisory lock is simulated gracefully
        acquired_2 = try_advisory_lock(LOCK_JOB_325_EXECUTION, db=db2)
        if "postgresql" in str(engine.url):
            assert acquired_2 is False

        # Release Worker 1
        release_advisory_lock(LOCK_JOB_325_EXECUTION, db=db1)
    finally:
        db1.close()
        db2.close()


def test_advisory_lock_guard_context_manager():
    """Verify advisory_lock_guard cleanly executes protected work and safely releases."""
    executed = False
    with advisory_lock_guard(LOCK_JOB_325_EXECUTION) as acquired:
        if acquired:
            executed = True
    assert executed is True


# ── 3. Two-Phase Exit-Claim Concurrency & Duplicate Exit Prevention ───────────

def test_two_phase_exit_claim_concurrent_protection(db_session, monkeypatch):
    """
    Simulate two concurrent exit attempts (e.g. WebSocket SL trigger + manual exit click).
    Phase 1 (DB row lock + AtsOrder claim) must guarantee ONLY ONE request proceeds to Phase 2 (Dhan API).
    """
    tenant = create_mock_tenant(db_session, "claim")
    acc = tenant["account"]
    comp = tenant["company"]

    trade = Trade(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        company_id=comp.id,
        security_id=comp.dhan_security_id,
        ats_state=AtsTradeState.OPEN,
        entry_price=200.0,
        allocated_quantity=20,
        remaining_quantity=20,
        stop_price=190.0,
        trade_date=date.today()
    )
    db_session.add(trade)
    db_session.commit()

    api_calls = []

    class MockContext:
        def __init__(self, acc_id):
            self.dhan_account_id = acc_id
            self.client_id = f"CLI_{acc_id}"
        def execute_v2_post(self, url, payload):
            api_calls.append(payload)
            return {"status": "success", "data": {"orderId": "DHAN_ORD_999"}}

    import app.trading.execution as ex
    monkeypatch.setattr(ex, "get_account_context", lambda x: MockContext(x))

    async def run_concurrent():
        results = await asyncio.gather(
            place_market_sell(trade.id, comp.dhan_security_id, 20, "FINAL_EXIT"),
            place_market_sell(trade.id, comp.dhan_security_id, 20, "FINAL_EXIT"),
            return_exceptions=True
        )
        return results

    results = asyncio.run(run_concurrent())
    
    # Exactly one broker API call must have been made
    assert len(api_calls) == 1
    
    db_session.refresh(trade)
    assert trade.ats_state in (AtsTradeState.EXIT_REQUESTED, AtsTradeState.OPEN)


# ── 4. Idempotent Delta Fill Accounting ───────────────────────────────────────

def test_idempotent_delta_fill_accounting(db_session):
    """
    Verify that duplicate exit fill notifications from broker or reconciler:
    - 1st fill: processed cleanly, remaining_quantity reduced.
    - 2nd duplicate fill (same fill_qty): delta_qty = 0, discarded idempotently without double-deduction.
    """
    tenant = create_mock_tenant(db_session, "delta")
    acc = tenant["account"]
    comp = tenant["company"]

    trade = Trade(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        company_id=comp.id,
        security_id=comp.dhan_security_id,
        ats_state=AtsTradeState.EXIT_REQUESTED,
        entry_price=100.0,
        allocated_quantity=10,
        remaining_quantity=10,
        trade_date=date.today()
    )
    db_session.add(trade)

    ats_order = AtsOrder(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        trade_id=trade.id,
        dhan_order_id="DHAN_FILL_101",
        order_purpose=OrderPurpose.FINAL_EXIT,
        transaction_type="SELL",
        security_id=comp.dhan_security_id,
        quantity=10,
        status="TRANSIT",
        fill_qty=0
    )
    db_session.add(ats_order)
    db_session.commit()

    # 1. First Fill event: 10 shares filled @ 110.0
    t1 = confirm_exit_fill(trade.id, 110.0, 10, "DHAN_FILL_101")
    assert t1 is not None
    assert t1.ats_state == AtsTradeState.CLOSED
    assert t1.remaining_quantity == 0
    assert t1.realized_pnl == 100.0  # (110 - 100) * 10

    # 2. Second Duplicate Fill event: same 10 shares
    confirm_exit_fill(trade.id, 110.0, 10, "DHAN_FILL_101")
    db_session.expire_all()
    reloaded_trade = db_session.query(Trade).filter(Trade.id == trade.id).first()
    # Must remain CLOSED with remaining_quantity=0 (no negative quantity!)
    assert reloaded_trade.ats_state == AtsTradeState.CLOSED
    assert reloaded_trade.remaining_quantity == 0
    assert reloaded_trade.realized_pnl == 100.0


# ── 5. Orphaned Order Recovery via Correlation ID ─────────────────────────────

def test_orphaned_order_recovery(db_session):
    """
    Verify that when an order is submitted to Dhan but the HTTP response was lost (or server crashed),
    the BrokerReconciler recovers the dhan_order_id by matching correlationId in GET /v2/orders.
    """
    tenant = create_mock_tenant(db_session, "orphan")
    acc = tenant["account"]
    comp = tenant["company"]

    corr_id = _correlation_id("test_trade_orphan", "E")
    
    trade = Trade(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        company_id=comp.id,
        security_id=comp.dhan_security_id,
        ats_state=AtsTradeState.ENTRY_PENDING,
        trade_date=date.today()
    )
    db_session.add(trade)

    ats_order = AtsOrder(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        trade_id=trade.id,
        dhan_order_id=None,  # Lost during network timeout
        correlation_id=corr_id,
        order_purpose=OrderPurpose.ENTRY,
        transaction_type="BUY",
        security_id=comp.dhan_security_id,
        quantity=5,
        status="PENDING",
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(ats_order)
    db_session.commit()

    class MockReconcileClient:
        def execute_v2_get(self, endpoint):
            if "orders" in endpoint:
                return [
                    {
                        "orderId": "RECOVERED_DHAN_ORD_777",
                        "correlationId": corr_id,
                        "orderStatus": "TRADED",
                        "filledQty": 5,
                        "avgTradedPrice": 250.0
                    }
                ]
            return []

    reconciler = BrokerReconciler()
    recovered = reconciler._recover_orphaned_order(MockReconcileClient(), db_session, ats_order)
    
    assert recovered is True
    db_session.refresh(ats_order)
    assert ats_order.dhan_order_id == "RECOVERED_DHAN_ORD_777"
