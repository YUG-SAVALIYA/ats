import pytest
from app.models import Trade, AtsTradeState

from app.core.state_machine import validate_state_transition

def test_valid_state_transitions():
    t = Trade(id="t1", ats_state="SIGNAL")
    
    # Valid transition to ENTRY_PENDING
    assert validate_state_transition(t, AtsTradeState.ENTRY_PENDING) == True
    
    # Valid transition to OPEN
    assert validate_state_transition(t, AtsTradeState.OPEN) == True
    
    # Valid transition to PARTIAL_EXIT
    assert validate_state_transition(t, AtsTradeState.PARTIAL_EXIT) == True
    
    # Valid transition to EXIT_REQUESTED
    assert validate_state_transition(t, AtsTradeState.EXIT_REQUESTED) == True
    
    # Crash recovery back to PARTIAL_EXIT
    assert validate_state_transition(t, AtsTradeState.PARTIAL_EXIT) == True
    
    # To CLOSED
    assert validate_state_transition(t, AtsTradeState.CLOSED) == True

def test_invalid_state_transition():
    t = Trade(id="t2", ats_state=AtsTradeState.OPEN)
    
    # Try illegal transition from OPEN -> ENTRY_PENDING
    assert validate_state_transition(t, AtsTradeState.ENTRY_PENDING) == False
    assert t.ats_state == AtsTradeState.OPEN  # State should not change

    # Try illegal transition from CLOSED -> OPEN
    t.ats_state = AtsTradeState.CLOSED
    assert validate_state_transition(t, AtsTradeState.OPEN) == False
        
def test_idempotency_of_cache():
    from app.core.cache_manager import get_cache_manager
    cache = get_cache_manager()
    cache._trades_by_id.clear()
    
    t = Trade(id="t3", security_id="111", ats_state=AtsTradeState.OPEN)
    cache.add_trade(t)
    assert len(cache._trades_by_id) == 1
    
    # Add again (idempotent overwrite)
    cache.update_trade(t)
    assert len(cache._trades_by_id) == 1
    
    cache.remove_trade("t3")
    assert len(cache._trades_by_id) == 0
