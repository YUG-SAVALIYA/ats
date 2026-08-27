import sys
from pathlib import Path
import pytest

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from database.models import Trade, AtsTradeState
from trading.trades import validate_state_transition, get_cache_manager


def test_state_machine_valid_transitions():
    trade = Trade(id="test-1", ats_state="SIGNAL")
    assert validate_state_transition(trade, AtsTradeState.ENTRY_PENDING) is True
    assert trade.ats_state == AtsTradeState.ENTRY_PENDING

    assert validate_state_transition(trade, AtsTradeState.OPEN) is True
    assert trade.ats_state == AtsTradeState.OPEN

    assert validate_state_transition(trade, AtsTradeState.PARTIAL_EXIT) is True
    assert trade.ats_state == AtsTradeState.PARTIAL_EXIT

    assert validate_state_transition(trade, AtsTradeState.EXIT_REQUESTED) is True
    assert trade.ats_state == AtsTradeState.EXIT_REQUESTED

    assert validate_state_transition(trade, AtsTradeState.CLOSED) is True
    assert trade.ats_state == AtsTradeState.CLOSED


def test_state_machine_invalid_transitions():
    trade = Trade(id="test-2", ats_state="CLOSED")
    assert validate_state_transition(trade, AtsTradeState.OPEN) is False

    trade2 = Trade(id="test-3", ats_state="SIGNAL")
    assert validate_state_transition(trade2, AtsTradeState.CLOSED) is False


def test_cache_manager_operations():
    cache_mgr = get_cache_manager()
    cache_mgr.clear_cache()

    trade = Trade(
        id="trade-101",
        security_id="1333",
        ats_state=AtsTradeState.OPEN,
        remaining_quantity=10,
        stop_price=100.0,
        target1_price=120.0
    )

    cache_mgr.add_trade(trade)
    assert cache_mgr.get_active_trade_count() == 1
    assert "1333" in cache_mgr.get_active_security_ids()

    retrieved = cache_mgr.get_trade("trade-101")
    assert retrieved is not None
    assert retrieved.id == "trade-101"

    trades_for_sec = cache_mgr.get_trades_for_security("1333")
    assert len(trades_for_sec) == 1

    cache_mgr.remove_trade("trade-101")
    assert cache_mgr.get_active_trade_count() == 0
    assert len(cache_mgr.get_trades_for_security("1333")) == 0
