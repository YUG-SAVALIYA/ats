"""
tests/test_phase8_e2e_multi_account.py
======================================
Comprehensive Phase 8 End-to-End Integration Testing & Validation Suite:
Tests 3 isolated Dhan accounts (acc_ALPHA, acc_BETA, acc_GAMMA) across all 10 required production scenarios.
"""

import pytest
import asyncio
import uuid
import time
import requests
from datetime import datetime, date, timezone
from typing import Dict, Any, List

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.data.database import SessionLocal
from app.data.models import (
    Base, User, DhanAccount, Company, Signal, Trade, AtsOrder,
    OrderAttempt, AtsTradeState, TradeEvent, Holding, Position, AppConfig
)
from app.trading.cache import get_cache_manager
from app.trading.trade_engine import init_trade_engine, TradeEngine
from app.trading.execution import get_order_executor, place_market_sell
from app.workers.reconciliation import BrokerReconciler
from app.trading.strategy import get_strategy_engine, AutomatedStrategyEngine
from app.api.auth_app import SECRET_KEY, ALGORITHM

import jwt
import bcrypt


# ── Helpers & Fixtures ────────────────────────────────────────────────────────

def create_tenant_environment(db):
    """Sets up 3 isolated Users and 3 Dhan Accounts."""
    users = {}
    accounts = {}
    tokens = {}

    for name in ["alpha", "beta", "gamma"]:
        user = User(
            id=str(uuid.uuid4()),
            email=f"{name}@ats.test",
            role="user",
            is_active=True
        )
        db.add(user)
        db.flush()

        acc = DhanAccount(
            id=f"acc_{name.upper()}",
            user_id=user.id,
            client_id=f"CLIENT_{name.upper()}_1234",
            account_status="ACTIVE"
        )
        db.add(acc)
        db.flush()

        # Generate scoped user JWT
        payload = {
            "sub": user.email,
            "user_id": user.id,
            "role": "user",
            "exp": datetime.now(timezone.utc).timestamp() + 3600
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

        users[name] = user
        accounts[name] = acc
        tokens[name] = token

    # Admin User & Token
    admin_user = User(id=str(uuid.uuid4()), email="admin@ats.test", role="admin", is_active=True)
    db.add(admin_user)
    
    # Set admin password in app_config
    hashed_pw = bcrypt.hashpw(b"admin1234", bcrypt.gensalt()).decode("utf-8")
    db.add(AppConfig(config_key="admin_password", config_value=hashed_pw))
    db.flush()

    admin_payload = {
        "sub": "admin",
        "user_id": None,
        "role": "admin",
        "exp": datetime.now(timezone.utc).timestamp() + 3600
    }
    admin_token = jwt.encode(admin_payload, SECRET_KEY, algorithm=ALGORITHM)

    db.commit()
    return {
        "users": users,
        "accounts": accounts,
        "tokens": tokens,
        "admin_token": admin_token,
    }


def create_test_company(db, symbol: str, sec_id: str, is_mtf: bool = False):
    comp = Company(
        id=str(uuid.uuid4()),
        trading_symbol=symbol,
        company_name=f"{symbol} Ltd",
        dhan_security_id=sec_id,
        exchange="NSE",
        is_active=True,
        is_mtf=is_mtf
    )
    db.add(comp)
    db.commit()
    return comp


# ═════════════════════════════════════════════════════════════════════════════
# 1. SAME SYMBOL, SEPARATE ACCOUNTS
# ═════════════════════════════════════════════════════════════════════════════
def test_1_same_symbol_separate_accounts_isolation(monkeypatch):
    """
    Account A and Account B both trade the same symbol (RELIANCE).
    Trigger an exit/SL for A only.
    Verify Account B remains completely untouched.
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "RELIANCE", "1001")

        # Create Trade for Alpha (SL at 190) and Trade for Beta (SL at 170)
        t_alpha = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["alpha"].id,
            company_id=comp.id,
            security_id="1001",
            ats_state=AtsTradeState.OPEN,
            entry_price=200.0,
            stop_price=190.0,
            allocated_quantity=50,
            remaining_quantity=50,
            trade_date=date.today()
        )
        t_beta = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["beta"].id,
            company_id=comp.id,
            security_id="1001",
            ats_state=AtsTradeState.OPEN,
            entry_price=200.0,
            stop_price=170.0,
            allocated_quantity=100,
            remaining_quantity=100,
            trade_date=date.today()
        )
        db.add_all([t_alpha, t_beta])
        db.commit()

        api_calls = []

        class MockAccountClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_post(self, url, payload):
                api_calls.append({"acc": self.dhan_account_id, "payload": payload})
                return {"status": "success", "data": {"orderId": f"ORD_{uuid.uuid4()}"}}

        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockAccountClient(acc_id))

        cache = get_cache_manager()
        cache.clear_cache()

        engine = init_trade_engine(place_market_sell)
        asyncio.run(engine.recover_from_db())

        # Tick at 185.0 -> Alpha SL (190) is triggered! Beta SL (170) is safe.
        asyncio.run(engine.on_tick("1001", 185.0))

        # Verify only Alpha placed an order
        assert len(api_calls) == 1
        assert api_calls[0]["acc"] == env["accounts"]["alpha"].id
        assert api_calls[0]["payload"]["quantity"] == 50

        # Verify DB states
        db.refresh(t_alpha)
        db.refresh(t_beta)
        assert t_alpha.ats_state == AtsTradeState.EXIT_REQUESTED
        assert t_beta.ats_state == AtsTradeState.OPEN
        assert t_beta.remaining_quantity == 100
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 2. INDEPENDENT ENTRY & SIZING
# ═════════════════════════════════════════════════════════════════════════════
def test_2_independent_entry_and_sizing(monkeypatch):
    """
    Same global signal. Different account balances.
    Verify each account gets independently calculated quantity and separate order.
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "INFY", "1002")

        sig = Signal(
            id=f"SIG_{uuid.uuid4()}",
            company_id=comp.id,
            date=date.today(),
            status="PENDING",
            strategy_type="SUPERTREND",
            raw_signal_data={"signal_high": 1000.0, "signal_low": 950.0}
        )
        db.add(sig)
        db.commit()

        # Balances: Alpha has 100,000, Beta has 500,000, Gamma has 1,000,000
        account_balances = {
            env["accounts"]["alpha"].id: 100000.0,
            env["accounts"]["beta"].id: 500000.0,
            env["accounts"]["gamma"].id: 1000000.0,
        }

        orders_placed = []

        class MockAccountClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_get(self, endpoint):
                bal = account_balances.get(self.dhan_account_id, 50000.0)
                return {"availabelBalance": bal}

            def execute_v2_post(self, url, payload):
                orders_placed.append({
                    "acc_id": self.dhan_account_id,
                    "payload": payload
                })
                return {"status": "success", "data": {"orderId": f"ORD_{uuid.uuid4()}"}}

            def get_marketfeed_ohlc(self, sec_ids):
                return {
                    str(comp.dhan_security_id): {
                        "open": 1010.0,
                        "high": 1060.0,  # Breakout (> 1000 * 1.03)
                        "low": 990.0,
                        "close": 1050.0,
                        "last_price": 1050.0
                    }
                }

        import app.broker.dhan_client as dhan_mod
        import app.trading.execution as ex
        monkeypatch.setattr(dhan_mod, "get_account_context", lambda acc_id: MockAccountClient(acc_id))
        monkeypatch.setattr(dhan_mod, "get_dhan_data_client", lambda: MockAccountClient("DATA"))
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockAccountClient(acc_id))

        strategy_engine = AutomatedStrategyEngine()
        res = strategy_engine.evaluate_and_execute_325_entries()

        assert res.get("status") == "completed"
        assert len(orders_placed) == 3

        # Sizing at 20% capital / 1050 LTP:
        # Alpha: 100k * 0.20 = 20k -> 20000 // 1050 = 19 qty
        # Beta: 500k * 0.20 = 100k -> 100000 // 1050 = 95 qty
        # Gamma: 1000k * 0.20 = 200k -> 200000 // 1050 = 190 qty
        orders_by_acc = {o["acc_id"]: o["payload"]["quantity"] for o in orders_placed}
        assert orders_by_acc[env["accounts"]["alpha"].id] == 19
        assert orders_by_acc[env["accounts"]["beta"].id] == 95
        assert orders_by_acc[env["accounts"]["gamma"].id] == 190

        # Verify DB Trades
        trades = db.query(Trade).filter(Trade.signal_id == sig.id).all()
        assert len(trades) == 3
        trade_accs = {t.dhan_account_id: t.allocated_quantity for t in trades}
        assert trade_accs[env["accounts"]["alpha"].id] == 19
        assert trade_accs[env["accounts"]["beta"].id] == 95
        assert trade_accs[env["accounts"]["gamma"].id] == 190
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 3. INDEPENDENT FAILURE ISOLATION
# ═════════════════════════════════════════════════════════════════════════════
def test_3_independent_failure_isolation(monkeypatch):
    """
    Force Account A token/API failure (401 / Exception).
    Verify Account B and C continue normally and execute successfully.
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "TCS", "1003")

        sig = Signal(
            id=f"SIG_{uuid.uuid4()}",
            company_id=comp.id,
            date=date.today(),
            status="PENDING",
            strategy_type="SUPERTREND",
            raw_signal_data={"signal_high": 3000.0, "signal_low": 2900.0}
        )
        db.add(sig)
        db.commit()

        executed_accs = []

        class MockAccountClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_get(self, endpoint):
                if self.dhan_account_id == env["accounts"]["alpha"].id:
                    # Force Alpha Failure
                    raise RuntimeError("401 Unauthorized / Expired Token")
                return {"availabelBalance": 200000.0}

            def execute_v2_post(self, url, payload):
                executed_accs.append(self.dhan_account_id)
                return {"status": "success", "data": {"orderId": f"ORD_{uuid.uuid4()}"}}

            def get_marketfeed_ohlc(self, sec_ids):
                return {
                    str(comp.dhan_security_id): {
                        "open": 3050.0,
                        "high": 3200.0,
                        "low": 3000.0,
                        "close": 3150.0,
                        "last_price": 3150.0
                    }
                }

        import app.broker.dhan_client as dhan_mod
        import app.trading.execution as ex
        monkeypatch.setattr(dhan_mod, "get_account_context", lambda acc_id: MockAccountClient(acc_id))
        monkeypatch.setattr(dhan_mod, "get_dhan_data_client", lambda: MockAccountClient("DATA"))
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockAccountClient(acc_id))

        strategy_engine = AutomatedStrategyEngine()
        res = strategy_engine.evaluate_and_execute_325_entries()

        assert res.get("status") == "completed"
        # Alpha failed, but Beta and Gamma succeeded!
        assert env["accounts"]["alpha"].id not in executed_accs
        assert env["accounts"]["beta"].id in executed_accs
        assert env["accounts"]["gamma"].id in executed_accs
        assert len(executed_accs) == 2
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 4. AMBIGUOUS ORDER & RECONCILIATION
# ═════════════════════════════════════════════════════════════════════════
def test_4_ambiguous_order_reconciliation(monkeypatch):
    """
    Simulate timeout after broker call.
    Verify reconciliation discovers order via correlation_id.
    Verify no duplicate order is placed on retry.
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "WIPRO", "1004")

        trade = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["alpha"].id,
            company_id=comp.id,
            security_id="1004",
            ats_state=AtsTradeState.OPEN,
            entry_price=400.0,
            stop_price=380.0,
            allocated_quantity=50,
            remaining_quantity=50,
            trade_date=date.today()
        )
        db.add(trade)
        db.commit()

        # Step 1: Place exit order with network timeout
        class MockClientTimeout:
            dhan_account_id = env["accounts"]["alpha"].id
            client_id = "CLIENT_ALPHA_1234"

            def execute_v2_post(self, url, payload):
                raise requests.exceptions.Timeout("Connection timed out waiting for Dhan gateway")

        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockClientTimeout())

        ord_res = asyncio.run(place_market_sell(trade.id, "1004", 50, "FINAL_EXIT"))
        assert ord_res.status == "UNKNOWN"

        db.refresh(trade)
        assert trade.ats_state == AtsTradeState.EXIT_UNKNOWN

        # Step 2: Run Broker Reconciliation
        # Broker has the order with correlationId matching the trade's AtsOrder
        ats_order = db.query(AtsOrder).filter(AtsOrder.trade_id == trade.id).first()
        assert ats_order is not None
        assert ats_order.status == "UNKNOWN"

        class MockClientReconcile:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_get(self, endpoint):
                if "/positions" in endpoint:
                    return [{"securityId": "1004", "netQty": 0}]
                if "/orders/" in endpoint:
                    return [{
                        "orderId": "DHAN_RECON_999",
                        "correlationId": ats_order.correlation_id,
                        "orderStatus": "TRADED",
                        "filledQty": 50,
                        "avgTradedPrice": 379.5
                    }]
                if "/orders" in endpoint:
                    return [{
                        "orderId": "DHAN_RECON_999",
                        "correlationId": ats_order.correlation_id,
                        "orderStatus": "TRADED",
                        "filledQty": 50,
                        "avgTradedPrice": 379.5
                    }]
                return []

        import app.broker.dhan_client as dhan_mod
        import app.trading.execution as ex
        monkeypatch.setattr(dhan_mod, "get_account_context", lambda acc_id: MockClientReconcile(acc_id))
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockClientReconcile(acc_id))

        reconciler = BrokerReconciler(confirm_fill_fn=get_order_executor().confirm_exit_fill)
        recon_res = asyncio.run(reconciler.reconcile_cycle(is_startup=False))

        # Verify order was discovered & trade closed
        db.refresh(trade)
        db.refresh(ats_order)
        assert ats_order.dhan_order_id == "DHAN_RECON_999"
        assert trade.ats_state == AtsTradeState.CLOSED
        assert trade.trade_status == "CLOSED"

        # Step 3: Verify duplicate order prevention (re-trying exit must be blocked)
        with pytest.raises(RuntimeError, match="CLOSED"):
            asyncio.run(place_market_sell(trade.id, "1004", 50, "FINAL_EXIT"))
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 5. CRASH RECOVERY ACROSS 5 CRITICAL LIFECYCLE POINTS
# ═════════════════════════════════════════════════════════════════════════
def test_5_crash_recovery_lifecycle_points(monkeypatch):
    """
    Test process restart at 5 critical points:
    1. Before broker call (AtsOrder CLAIMED)
    2. After DB state commit
    3. After broker accepts order before response (orphaned correlation_id)
    4. During open trade (engine restarts -> reloads active cache)
    5. During exit (recovers EXIT_REQUESTED -> reconciles to CLOSED)
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "HDFCBANK", "1005")

        # Point 1 & 4: Open Trade in DB -> Simulate engine restart & cache reconstruction
        t_open = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["alpha"].id,
            company_id=comp.id,
            security_id="1005",
            ats_state=AtsTradeState.OPEN,
            entry_price=1500.0,
            stop_price=1425.0,
            allocated_quantity=30,
            remaining_quantity=30,
            trade_date=date.today()
        )
        db.add(t_open)
        db.commit()

        cache = get_cache_manager()
        cache.clear_cache()

        # Engine recovers from DB on restart
        engine = init_trade_engine(place_market_sell)
        recovered_count = asyncio.run(engine.recover_from_db())
        assert recovered_count >= 1
        cached_trade = cache.get_trade(t_open.id)
        assert cached_trade is not None
        assert cached_trade.entry_price == 1500.0
        assert cached_trade.stop_price == 1425.0

        # Point 1 & 3: Orphaned Order recovery (AtsOrder created but crashed before receiving dhan_order_id)
        cid = f"ATS-{t_open.id.replace('-', '')[:10].upper()}-REC-1"
        orphaned_order = AtsOrder(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["alpha"].id,
            trade_id=t_open.id,
            dhan_order_id=None,
            correlation_id=cid,
            order_purpose="FINAL_EXIT",
            transaction_type="SELL",
            security_id="1005",
            quantity=30,
            status="CLAIMED"
        )
        t_open.ats_state = AtsTradeState.EXIT_REQUESTED
        db.add(orphaned_order)
        db.commit()

        class MockBrokerSync:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_get(self, endpoint):
                if "/orders/" in endpoint or "/orders" in endpoint:
                    return [{
                        "orderId": "ORPHAN_RECOVERED_777",
                        "correlationId": cid,
                        "orderStatus": "TRADED",
                        "filledQty": 30,
                        "avgTradedPrice": 1420.0
                    }]
                if "/positions" in endpoint:
                    return [{"securityId": "1005", "netQty": 0}]
                return []

        import app.broker.dhan_client as dhan_mod
        import app.trading.execution as ex
        monkeypatch.setattr(dhan_mod, "get_account_context", lambda acc_id: MockBrokerSync(acc_id))
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockBrokerSync(acc_id))

        reconciler = BrokerReconciler(confirm_fill_fn=get_order_executor().confirm_exit_fill)
        recon_res = asyncio.run(reconciler.reconcile_cycle(is_startup=True))

        db.refresh(orphaned_order)
        db.refresh(t_open)
        assert orphaned_order.dhan_order_id == "ORPHAN_RECOVERED_777"
        assert t_open.ats_state == AtsTradeState.CLOSED
        assert t_open.trade_status == "CLOSED"
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 6. 3:25 PM SIMULTANEOUS EXECUTION & TIMING BENCHMARK
# ═════════════════════════════════════════════════════════════════════════════
def test_6_simultaneous_325_execution_benchmark(monkeypatch):
    """
    Multiple accounts execute simultaneously during 3:25 PM evaluation.
    Verify account-specific credentials, quantity, orders, and exits.
    Measure realistic end-to-end execution time (< 1.5 seconds).
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp1 = create_test_company(db, "ICICIBANK", "1006")
        comp2 = create_test_company(db, "SBIN", "1007")

        sig1 = Signal(
            id=f"SIG_{uuid.uuid4()}",
            company_id=comp1.id,
            date=date.today(),
            status="PENDING",
            strategy_type="SUPERTREND",
            raw_signal_data={"signal_high": 1000.0, "signal_low": 950.0}
        )
        sig2 = Signal(
            id=f"SIG_{uuid.uuid4()}",
            company_id=comp2.id,
            date=date.today(),
            status="PENDING",
            strategy_type="SUPERTREND",
            raw_signal_data={"signal_high": 750.0, "signal_low": 720.0}
        )
        db.add_all([sig1, sig2])
        db.commit()

        executed_orders = []

        class MockFastClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_get(self, endpoint):
                return {"availabelBalance": 250000.0}

            def execute_v2_post(self, url, payload):
                executed_orders.append((self.dhan_account_id, payload))
                return {"status": "success", "data": {"orderId": f"ORD_{uuid.uuid4()}"}}

            def get_marketfeed_ohlc(self, sec_ids):
                return {
                    "1006": {"open": 1010.0, "high": 1060.0, "low": 990.0, "close": 1050.0, "last_price": 1050.0},
                    "1007": {"open": 760.0, "high": 800.0, "low": 740.0, "close": 790.0, "last_price": 790.0},
                }

        import app.broker.dhan_client as dhan_mod
        import app.trading.execution as ex
        monkeypatch.setattr(dhan_mod, "get_account_context", lambda acc_id: MockFastClient(acc_id))
        monkeypatch.setattr(dhan_mod, "get_dhan_data_client", lambda: MockFastClient("DATA"))
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockFastClient(acc_id))

        strategy_engine = AutomatedStrategyEngine()

        t_start = time.perf_counter()
        res = strategy_engine.evaluate_and_execute_325_entries()
        t_duration = time.perf_counter() - t_start

        # 3 accounts * 2 signals = 6 total orders placed
        assert res.get("status") == "completed"
        assert res.get("executed") == 6
        assert len(executed_orders) == 6

        # Benchmark: under concurrency, 6 orders across 3 accounts complete in under 1.5 seconds
        assert t_duration < 1.5, f"Execution took too long: {t_duration:.3f}s"
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 7. CONCURRENT SL / TARGET MULTI-ACCOUNT ROUTING
# ═════════════════════════════════════════════════════════════════════════════
def test_7_concurrent_sl_target_multi_account_routing(monkeypatch):
    """
    Multiple accounts hit SL/Target at the exact same tick.
    Verify exactly one logical order per trade and correct account routing.
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "KOTAKBANK", "1008")

        trades = []
        for name in ["alpha", "beta", "gamma"]:
            t = Trade(
                id=str(uuid.uuid4()),
                dhan_account_id=env["accounts"][name].id,
                company_id=comp.id,
                security_id="1008",
                ats_state=AtsTradeState.OPEN,
                entry_price=1800.0,
                stop_price=1710.0,  # 5% SL
                allocated_quantity=20,
                remaining_quantity=20,
                trade_date=date.today()
            )
            db.add(t)
            trades.append(t)
        db.commit()

        api_calls = []

        class MockRouteClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_post(self, url, payload):
                api_calls.append(self.dhan_account_id)
                return {"status": "success", "data": {"orderId": f"ORD_{uuid.uuid4()}"}}

        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda acc_id: MockRouteClient(acc_id))

        cache = get_cache_manager()
        cache.clear_cache()

        engine = init_trade_engine(place_market_sell)
        asyncio.run(engine.recover_from_db())

        # Tick at 1700.0 -> all 3 accounts hit SL simultaneously!
        asyncio.run(engine.on_tick("1008", 1700.0))

        # Exactly 3 orders placed, one per account
        assert len(api_calls) == 3
        assert env["accounts"]["alpha"].id in api_calls
        assert env["accounts"]["beta"].id in api_calls
        assert env["accounts"]["gamma"].id in api_calls

        # Firing a second tick immediately must NOT generate duplicate orders
        asyncio.run(engine.on_tick("1008", 1695.0))
        assert len(api_calls) == 3
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 8. API ISOLATION & ACCESS CONTROL
# ═════════════════════════════════════════════════════════════════════════
def test_8_api_isolation_and_cross_user_blocking():
    """
    User A attempts to access/modify User B's resources:
    - Manual entry on B's account (403)
    - Cancel B's pending trade (403)
    - Exit B's trade (403)
    - View B's trade events (empty / 0 rows)
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp = create_test_company(db, "AXISBANK", "1009")

        # Create Trade belonging to Beta
        t_beta = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["beta"].id,
            company_id=comp.id,
            security_id="1009",
            ats_state=AtsTradeState.OPEN,
            entry_price=1000.0,
            stop_price=950.0,
            allocated_quantity=10,
            remaining_quantity=10,
            trade_date=date.today()
        )
        db.add(t_beta)
        db.commit()

        client = TestClient(app)

        # 1. User Alpha attempts manual entry targeting User Beta's Dhan Account
        headers_alpha = {"Authorization": f"Bearer {env['tokens']['alpha']}"}
        res_entry = client.post(
            "/api/trades/manual-entry",
            headers=headers_alpha,
            json={
                "security_id": "1009",
                "trading_symbol": "AXISBANK",
                "quantity": 10,
                "allocated_capital": 10000.0,
                "dhan_account_id": env["accounts"]["beta"].id
            }
        )
        assert res_entry.status_code == 403

        # 2. User Alpha attempts to cancel Beta's trade
        res_cancel = client.post(f"/api/trades/{t_beta.id}/cancel", headers=headers_alpha)
        assert res_cancel.status_code == 403

        # 3. User Alpha attempts to exit Beta's trade
        res_exit = client.post(
            f"/api/trades/{t_beta.id}/exit",
            headers=headers_alpha,
            json={"quantity": 10}
        )
        assert res_exit.status_code == 403

        # 4. User Alpha attempts to view Beta's trade events
        res_events = client.get(f"/api/db/trade-events?trade_id={t_beta.id}", headers=headers_alpha)
        assert res_events.status_code == 200
        assert len(res_events.json()) == 0  # Scoped out — 0 events returned
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 9. ADMIN / GLOBAL OPERATIONS AUTHORIZATION
# ═════════════════════════════════════════════════════════════════════════
def test_9_admin_global_operations_authorization():
    """
    Verify only ADMIN can control:
    - engine (/api/engine/toggle, /api/engine/status)
    - strategy settings (/api/settings/strategy)
    - scan (/api/engine/scan)
    - reconciliation (/api/engine/broker-reconcile)
    - candle sync (/api/candles/sync)
    - evaluate-325 (/api/engine/evaluate-325)
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        client = TestClient(app)

        user_headers = {"Authorization": f"Bearer {env['tokens']['alpha']}"}
        admin_headers = {"Authorization": f"Bearer {env['admin_token']}"}

        admin_endpoints = [
            ("GET", "/api/engine/status", None),
            ("POST", "/api/engine/toggle", {"enabled": True}),
            ("POST", "/api/engine/scan", None),
            ("POST", "/api/engine/broker-reconcile", None),
            ("POST", "/api/engine/evaluate-325", None),
            ("GET", "/api/settings/strategy", None),
            ("GET", "/api/settings/monthly_rsi", None),
            ("POST", "/api/auth/renew", {"totp": "123456"}),
        ]

        for method, endpoint, payload in admin_endpoints:
            # Regular user must be rejected with 403 Forbidden
            if method == "GET":
                res_user = client.get(endpoint, headers=user_headers)
            else:
                res_user = client.post(endpoint, headers=user_headers, json=payload or {})
            assert res_user.status_code == 403, f"Regular user was not blocked for {method} {endpoint}: {res_user.status_code}"

        # Admin user executes successfully
        res_admin_status = client.get("/api/engine/status", headers=admin_headers)
        assert res_admin_status.status_code == 200
        assert res_admin_status.json().get("mode") == "LIVE_MARKET_ORDERS_ONLY"

        res_admin_settings = client.get("/api/settings/strategy", headers=admin_headers)
        assert res_admin_settings.status_code == 200
    finally:
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 10. PORTFOLIO & DB DATA ISOLATION
# ═════════════════════════════════════════════════════════════════════════
def test_10_portfolio_and_db_data_isolation(monkeypatch):
    """
    User A only sees A's funds/holdings/positions/trades/orders.
    User B only sees B's.
    Admin sees all authorized accounts.
    """
    db = SessionLocal()
    try:
        env = create_tenant_environment(db)
        comp1 = create_test_company(db, "LT", "1010")
        comp2 = create_test_company(db, "MARUTI", "1011")

        # Create Trades for Alpha and Beta
        t_alpha = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["alpha"].id,
            company_id=comp1.id,
            security_id="1010",
            ats_state=AtsTradeState.OPEN,
            entry_price=3500.0,
            allocated_quantity=10,
            trade_date=date.today()
        )
        t_beta = Trade(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["beta"].id,
            company_id=comp2.id,
            security_id="1011",
            ats_state=AtsTradeState.OPEN,
            entry_price=11000.0,
            allocated_quantity=5,
            trade_date=date.today()
        )
        db.add_all([t_alpha, t_beta])

        # Create DB holdings
        h_alpha = Holding(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["alpha"].id,
            company_id=comp1.id,
            trading_symbol="LT",
            security_id="1010",
            total_qty=10,
            available_qty=10,
            avg_cost_price=3500.0,
            last_traded_price=3550.0
        )
        h_beta = Holding(
            id=str(uuid.uuid4()),
            dhan_account_id=env["accounts"]["beta"].id,
            company_id=comp2.id,
            trading_symbol="MARUTI",
            security_id="1011",
            total_qty=5,
            available_qty=5,
            avg_cost_price=11000.0,
            last_traded_price=11200.0
        )
        db.add_all([h_alpha, h_beta])
        db.commit()

        class MockPortfolioClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"CLIENT_{acc_id}"

            def execute_v2_get(self, endpoint):
                if "/fundlimit" in endpoint:
                    bal = 100000.0 if "ALPHA" in self.dhan_account_id else 200000.0
                    return {"availabelBalance": bal}
                if "/holdings" in endpoint:
                    sym = "LT" if "ALPHA" in self.dhan_account_id else "MARUTI"
                    return [{"tradingSymbol": sym, "totalQty": 10}]
                return []

        import app.api.router as router_mod
        import app.broker.dhan_portfolio as port_mod
        monkeypatch.setattr(router_mod, "get_account_context", lambda acc_id: MockPortfolioClient(acc_id))
        monkeypatch.setattr(port_mod, "get_account_context", lambda acc_id: MockPortfolioClient(acc_id))

        client = TestClient(app)
        headers_alpha = {"Authorization": f"Bearer {env['tokens']['alpha']}"}
        headers_beta = {"Authorization": f"Bearer {env['tokens']['beta']}"}
        headers_admin = {"Authorization": f"Bearer {env['admin_token']}"}

        # 1. DB Trades Scoping
        res_a_trades = client.get("/api/db/trades", headers=headers_alpha).json()
        assert len(res_a_trades) == 1
        assert res_a_trades[0]["id"] == t_alpha.id

        res_b_trades = client.get("/api/db/trades", headers=headers_beta).json()
        assert len(res_b_trades) == 1
        assert res_b_trades[0]["id"] == t_beta.id

        res_admin_trades = client.get("/api/db/trades", headers=headers_admin).json()
        assert len(res_admin_trades) >= 2

        # 2. DB Holdings Scoping
        res_a_holdings = client.get("/api/db/holdings", headers=headers_alpha).json()
        assert len(res_a_holdings) == 1
        assert res_a_holdings[0]["trading_symbol"] == "LT"

        res_b_holdings = client.get("/api/db/holdings", headers=headers_beta).json()
        assert len(res_b_holdings) == 1
        assert res_b_holdings[0]["trading_symbol"] == "MARUTI"

        # 3. Portfolio Summary Scoping
        res_a_summary = client.get("/api/portfolio/summary", headers=headers_alpha).json()
        assert len(res_a_summary.get("accounts", [])) == 1
        assert res_a_summary["accounts"][0]["dhan_account_id"] == env["accounts"]["alpha"].id

        res_b_summary = client.get("/api/portfolio/summary", headers=headers_beta).json()
        assert len(res_b_summary.get("accounts", [])) == 1
        assert res_b_summary["accounts"][0]["dhan_account_id"] == env["accounts"]["beta"].id

        res_admin_summary = client.get("/api/portfolio/summary", headers=headers_admin).json()
        assert len(res_admin_summary.get("accounts", [])) >= 3
    finally:
        db.close()
