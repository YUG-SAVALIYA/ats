"""
app.trading.state_machine
=========================
Centralized validation for ATS trade lifecycle state transitions.
"""

import logging
from typing import Optional
from app.data.models import Trade, AtsTradeState

logger = logging.getLogger("ats.state_machine")

ALLOWED_TRANSITIONS = {
    "SIGNAL": ["ENTRY_PENDING", "CANCELLED", "FAILED"],
    "ENTRY_PENDING": ["OPEN", "CANCELLED", "FAILED"],
    "OPEN": ["PARTIAL_EXIT", "EXIT_REQUESTED", "CLOSED"],
    "PARTIAL_EXIT": ["EXIT_REQUESTED", "CLOSED"],
    "EXIT_REQUESTED": ["OPEN", "PARTIAL_EXIT", "EXIT_UNKNOWN", "EXIT_FAILED", "CLOSED"],
    "EXIT_FAILED": ["EXIT_REQUESTED", "OPEN", "PARTIAL_EXIT", "CLOSED", "FAILED"],
    "EXIT_UNKNOWN": ["EXIT_REQUESTED", "OPEN", "PARTIAL_EXIT", "CLOSED", "FAILED"],
    "CLOSED": [],
    "CANCELLED": [],
    "FAILED": []
}


def validate_state_transition(trade: Trade, new_state: AtsTradeState) -> bool:
    """
    Centralized helper to validate and transition trade states.
    Returns True if valid, False if invalid (logs a critical error but doesn't crash).
    """
    old = getattr(trade, "ats_state", None)
    new_str = str(new_state.value if hasattr(new_state, "value") else new_state)
    old_str = str(old.value if hasattr(old, "value") else old) if old else None
    
    if old_str and old_str != new_str:
        if new_str not in ALLOWED_TRANSITIONS.get(old_str, []):
            logger.critical(f"[STATE_MACHINE] Invalid transition blocked: {old_str} -> {new_str} for Trade {trade.id}")
            return False
            
    trade.ats_state = new_state
    return True
