import pytest
import asyncio
from datetime import datetime, timezone
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from datetime import datetime
from app.data.database import SessionLocal, engine
from app.data.models import Base, User, DhanAccount, Company, Trade, AtsOrder, OrderAttempt, AtsTradeState, TradeEvent, DailyCandle, Signal
from app.trading.cache import get_cache_manager
from app.trading.trade_engine import init_trade_engine
from app.trading.execution import get_order_executor, place_market_sell

@pytest.fixture
def db_session():
    db = SessionLocal()
    yield db
    db.close()

def create_mock_account(db, email, dhan_id):
    user = User(email=f"{uuid.uuid4()}@a.com")
    db.add(user)
    db.commit()
    acc = DhanAccount(id=dhan_id, user_id=user.id, client_id=f"client_{dhan_id}")
    db.add(acc)
    db.commit()
    return acc

def create_mock_company(db, symbol, dhan_sec_id):
    comp = Company(id=str(uuid.uuid4()), trading_symbol=symbol, company_name=symbol, dhan_security_id=dhan_sec_id, exchange="NSE")
    db.add(comp)
    db.commit()
    return comp

def create_mock_trade(db, acc_id, comp_id, sec_id, state=AtsTradeState.OPEN, entry_price=100.0):
    t = Trade(
        id=str(uuid.uuid4()),
        dhan_account_id=acc_id,
        company_id=comp_id,
        security_id=sec_id,
        ats_state=state,
        entry_price=entry_price,
        allocated_quantity=10,
        remaining_quantity=10,
        stop_price=95.0,
        trade_date=datetime.now(timezone.utc).date()
    )
    db.add(t)
    db.commit()
    return t


def test_1_db_lock_contention(db_session):
    acc = create_mock_account(db_session, "a@a.com", "acc_A")
    comp = create_mock_company(db_session, "RELIANCE", "1001")
    t = create_mock_trade(db_session, acc.id, comp.id, "1001")
    
    # Place first exit order successfully
    order1 = AtsOrder(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        trade_id=t.id,
        order_purpose="FINAL_EXIT",
        transaction_type="SELL",
        security_id="1001",
        quantity=10,
        status="PENDING"
    )
    db_session.add(order1)
    db_session.commit()
    
    # Attempting to place a second exit order with same trade_id and purpose must fail constraint
    order2 = AtsOrder(
        id=str(uuid.uuid4()),
        dhan_account_id=acc.id,
        trade_id=t.id,
        order_purpose="FINAL_EXIT",
        transaction_type="SELL",
        security_id="1001",
        quantity=10,
        status="PENDING"
    )
    db_session.add(order2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_2_concurrent_exit_calls(db_session, monkeypatch):
    async def run_test():
        acc = create_mock_account(db_session, "c@c.com", "acc_C")
        comp = create_mock_company(db_session, "TCS", "1002")
        t = create_mock_trade(db_session, acc.id, comp.id, "1002")
        
        api_calls = []
        class MockClient:
            dhan_account_id = "acc_C"
            client_id = "client_acc_C"
            def execute_v2_post(self, url, payload):
                api_calls.append(payload)
                return {"status": "success", "data": {"orderId": "123"}}
                
        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda x: MockClient())
        
        # Fire two concurrent exits
        await asyncio.gather(
            place_market_sell(t.id, "1002", 10, "FINAL_EXIT"),
            place_market_sell(t.id, "1002", 10, "FINAL_EXIT")
        )
        
        assert len(api_calls) == 1
        
        db_session.refresh(t)
        assert t.ats_state == AtsTradeState.EXIT_REQUESTED
    asyncio.run(run_test())


def test_3_different_accounts_same_tick(db_session, monkeypatch):
    async def run_test():
        acc_a = create_mock_account(db_session, "x@x.com", "acc_X")
        acc_b = create_mock_account(db_session, "y@y.com", "acc_Y")
        comp = create_mock_company(db_session, "INFY", "1003")
        
        tA = create_mock_trade(db_session, acc_a.id, comp.id, "1003", AtsTradeState.OPEN, 100.0)
        tB = create_mock_trade(db_session, acc_b.id, comp.id, "1003", AtsTradeState.OPEN, 100.0)
        
        api_calls = []
        class MockClient:
            def __init__(self, acc_id):
                self.dhan_account_id = acc_id
                self.client_id = f"client_{acc_id}"
            def execute_v2_post(self, url, payload):
                api_calls.append(self.dhan_account_id)
                return {"status": "success", "data": {"orderId": "123"}}
                
        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda x: MockClient(x))
        
        cache = get_cache_manager()
        cache.clear_cache()
        cache.add_trade(tA)
        cache.add_trade(tB)
        
        engine = init_trade_engine(place_market_sell)
        await engine.recover_from_db()
        
        await engine.on_tick("1003", 90.0)
        
        assert len(api_calls) == 2
        assert "acc_X" in api_calls
        assert "acc_Y" in api_calls
    asyncio.run(run_test())


def test_4_trade_order_mismatch(db_session, monkeypatch):
    async def run_test():
        acc_a = create_mock_account(db_session, "a@mismatch.com", "acc_A")
        comp = create_mock_company(db_session, "WIPRO", "1004")
        tA = create_mock_trade(db_session, acc_a.id, comp.id, "1004")
        
        api_calls = []
        class MockClient:
            dhan_account_id = "acc_B_MISMATCH"
            client_id = "client_acc_B"
            def execute_v2_post(self, url, payload):
                api_calls.append(payload)
                return {}
                
        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda x: MockClient())
        
        ord_ret = await place_market_sell(tA.id, "1004", 10, "FINAL_EXIT")
        
        assert len(api_calls) == 0
        assert ord_ret.status == "FAILED"
    asyncio.run(run_test())


def test_5_duplicate_entry(db_session, monkeypatch):
    async def run_test():
        acc = create_mock_account(db_session, "dup@dup.com", "acc_DUP")
        comp = create_mock_company(db_session, "ITC", "1005")
        
        api_calls = []
        class MockClient:
            dhan_account_id = "acc_DUP"
            client_id = "client_acc_DUP"
            def execute_v2_post(self, url, payload):
                api_calls.append(payload)
                return {"status": "success", "data": {"orderId": "123"}}
                
        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda x: MockClient())
        
        executor = get_order_executor()
        
        from datetime import date
        sig = Signal(id="SIGNAL_X", company_id=comp.id, date=date.today())
        db_session.add(sig)
        db_session.commit()

        def run_entry():
            return executor.place_entry_order(
                dhan_account_id="acc_DUP",
                security_id="1005",
                trading_symbol="ITC",
                company_id=comp.id,
                signal_id="SIGNAL_X",
                quantity=10,
                allocated_capital=1000.0
            )
            
        loop = asyncio.get_running_loop()
        res1, res2 = await asyncio.gather(
            loop.run_in_executor(None, run_entry),
            loop.run_in_executor(None, run_entry)
        )
        
        assert len(api_calls) == 1
        assert "duplicate" in [res1.get("status"), res2.get("status")]
        assert "placed" in [res1.get("status"), res2.get("status")]
        
        trades = db_session.query(Trade).filter(Trade.signal_id == "SIGNAL_X").all()
        assert len(trades) == 1
    asyncio.run(run_test())


def test_6_ambiguous_dhan_response(db_session, monkeypatch):
    async def run_test():
        acc = create_mock_account(db_session, "ambig@ambig.com", "acc_AMBIG")
        comp = create_mock_company(db_session, "HDFC", "1006")
        t = create_mock_trade(db_session, acc.id, comp.id, "1006", AtsTradeState.OPEN, 100.0)
        
        api_calls = []
        class MockClientTimeout:
            dhan_account_id = "acc_AMBIG"
            client_id = "client_acc_AMBIG"
            def execute_v2_post(self, url, payload):
                api_calls.append(payload)
                import requests
                raise requests.exceptions.Timeout("Timeout from Dhan!")
                
        import app.trading.execution as ex
        monkeypatch.setattr(ex, "get_account_context", lambda x: MockClientTimeout())
        
        ord_ret = await place_market_sell(t.id, "1006", 10, "FINAL_EXIT")
        
        assert len(api_calls) == 1
        assert ord_ret.status == "UNKNOWN"
        
        db_session.refresh(t)
        assert t.ats_state == AtsTradeState.EXIT_UNKNOWN
    asyncio.run(run_test())
