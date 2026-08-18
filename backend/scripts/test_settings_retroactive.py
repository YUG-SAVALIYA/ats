import os
import sys

# Add backend root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.api.auth_app import get_current_user
from app.database import SessionLocal
from app.models import Trade, AtsTradeState
import uuid
from datetime import datetime, timezone

# Bypass authentication
app.dependency_overrides[get_current_user] = lambda: {"user_id": "test_user"}

client = TestClient(app)

def test_retroactive_settings():
    print("=========================================================")
    print("  TESTING RETROACTIVE SETTINGS UPDATE SYSTEM             ")
    print("=========================================================")

    # 1. Manually insert a dummy OPEN trade into the database
    db = SessionLocal()
    dummy_id = str(uuid.uuid4())
    print(f"\n[1] Creating a dummy OPEN trade with ID: {dummy_id}")
    
    # We set entry_price to 100 so math is easy.
    # Current target should be something else. We'll set them to 0 initially.
    dummy_trade = Trade(
        id=dummy_id,
        user_id="test_user",
        security_id="999999",
        trading_symbol="TEST_RETRO",
        ats_state=AtsTradeState.OPEN,
        entry_price=100.0,
        stop_price=90.0,      # Initially 90
        target1_price=110.0,  # Initially 110
        sl_stage=0,
        allocated_quantity=10,
        remaining_quantity=10,
        created_at=datetime.now(timezone.utc)
    )
    db.add(dummy_trade)
    db.commit()
    
    # Pre-warm the engine cache so it's aware of our dummy trade
    from app.core.engine import get_trade_engine
    engine = get_trade_engine()
    if engine:
        engine.cache_manager.add_trade(dummy_trade)
        print("    -> Trade added to DB and Engine Cache.")
    
    print("\n[2] Triggering API to Update Strategy Settings...")
    print("    Setting Initial SL to -10% and Target 1 to +25%")
    
    payload = {
        "daily_rsi_period": 14,
        "daily_rsi_lower": 50.0,
        "daily_rsi_upper": 90.0,
        "capital_allocation_pct": 20.0,
        "initial_sl_pct": -10.0,  # -10% of 100 should be 90
        "target1_pct": 25.0       # +25% of 100 should be 125
    }
    
    # Send the request
    # NOTE: Our settings update payload doesn't take initial_sl_pct and target1_pct via the API yet
    # Wait, the StrategySettingsUpdate model only has 4 fields! Let's check what it has.
    pass

if __name__ == "__main__":
    test_retroactive_settings()
