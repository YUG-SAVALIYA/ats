import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError
from app.data.models import Base, User, DhanAccount, Trade, AtsOrder, Company, Signal
import uuid
import datetime

# Use an in-memory SQLite database for fast isolation testing
engine = create_engine("sqlite:///:memory:")
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

def test_account_isolation(db_session):
    # 1. Create User
    user1 = User(email="test1@example.com")
    user2 = User(email="test2@example.com")
    db_session.add_all([user1, user2])
    db_session.commit()
    
    # 2. Create DhanAccounts for the Users
    acc1 = DhanAccount(user_id=user1.id, client_id="DHAN1")
    acc2 = DhanAccount(user_id=user2.id, client_id="DHAN2")
    db_session.add_all([acc1, acc2])
    db_session.commit()

    # 3. Verify accounts exist and are isolated
    assert acc1.id != acc2.id
    assert db_session.query(DhanAccount).count() == 2
    
    # Create shared company for trades
    company = Company(dhan_security_id="SEC1", trading_symbol="RELIANCE", company_name="Reliance")
    db_session.add(company)
    db_session.commit()

    # 4. Create Trades on specific accounts
    import datetime
    today = datetime.date.today()
    trade1 = Trade(dhan_account_id=acc1.id, company_id=company.id, trade_date=today)
    trade2 = Trade(dhan_account_id=acc2.id, company_id=company.id, trade_date=today)
    db_session.add_all([trade1, trade2])
    db_session.commit()

    # 5. Fetch trades and verify isolation
    acc1_trades = db_session.query(Trade).filter(Trade.dhan_account_id == acc1.id).all()
    assert len(acc1_trades) == 1
    assert acc1_trades[0].id == trade1.id
    
def test_ats_order_idempotency_constraint(db_session):
    # Create dependencies
    import datetime
    today = datetime.date.today()
    
    user = User(email="test_idem@example.com")
    db_session.add(user)
    db_session.commit()
    
    acc = DhanAccount(user_id=user.id, client_id="DHAN3")
    company = Company(dhan_security_id="SEC2", trading_symbol="INFY", company_name="Infosys")
    db_session.add_all([acc, company])
    db_session.commit()
    
    trade = Trade(dhan_account_id=acc.id, company_id=company.id, trade_date=today)
    db_session.add(trade)
    db_session.commit()
    
    # 1. Place a successful ENTRY order
    order1 = AtsOrder(
        dhan_account_id=acc.id,
        trade_id=trade.id,
        order_purpose="ENTRY",
        security_id="SEC2",
        quantity=100
    )
    db_session.add(order1)
    db_session.commit()
    
    # 2. Attempt to place a duplicate ENTRY order for the same trade
    order2 = AtsOrder(
        dhan_account_id=acc.id,
        trade_id=trade.id,
        order_purpose="ENTRY",  # Duplicate purpose
        security_id="SEC2",
        quantity=100
    )
    db_session.add(order2)
    
    # This must fail due to the UniqueConstraint on (trade_id, order_purpose)
    with pytest.raises(IntegrityError):
        db_session.commit()
