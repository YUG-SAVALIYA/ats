"""
app.trading.risk
================
Centralized Pre-Trade Risk Validator, Circuit Breakers, and Emergency Kill Switch.
"""

import os
import logging
from typing import Tuple, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.data.models import AppConfig, DhanAccount, AccountStatus, Trade, AtsOrder, AtsTradeState
from app.data.database import SessionLocal

logger = logging.getLogger("ats.risk")


class PreTradeSafetyValidator:
    """Centralized safety and risk checks before placing broker orders."""

    @staticmethod
    def is_kill_switch_active(db: Optional[Session] = None) -> bool:
        """
        Check if the emergency kill switch is tripped.
        Environment variable KILL_SWITCH=true overrides DB.
        """
        if os.getenv("KILL_SWITCH", "false").strip().lower() in ("true", "1", "yes"):
            return True

        local_db = db or SessionLocal()
        try:
            cfg = local_db.query(AppConfig).filter(AppConfig.config_key == "kill_switch").first()
            if cfg and cfg.config_value:
                return cfg.config_value.strip().lower() in ("true", "1", "yes")
            return False
        except Exception as exc:
            logger.warning(f"[SAFETY] Could not read kill switch from DB: {exc}")
            return False
        finally:
            if not db:
                try:
                    local_db.close()
                except Exception:
                    pass

    @staticmethod
    def set_kill_switch(enabled: bool, db: Optional[Session] = None) -> bool:
        """Toggle emergency kill switch in PostgreSQL app_config."""
        local_db = db or SessionLocal()
        try:
            cfg = local_db.query(AppConfig).filter(AppConfig.config_key == "kill_switch").first()
            val_str = "true" if enabled else "false"
            if cfg:
                cfg.config_value = val_str
            else:
                local_db.add(AppConfig(config_key="kill_switch", config_value=val_str))
            local_db.commit()
            logger.warning(f"[SAFETY] Emergency Kill Switch set to {val_str.upper()}")
            return enabled
        except Exception as exc:
            logger.error(f"[SAFETY] Failed to set kill switch in DB: {exc}")
            return enabled
        finally:
            if not db:
                try:
                    local_db.close()
                except Exception:
                    pass

    @classmethod
    def validate_entry_allowed(
        cls,
        dhan_account_id: str,
        security_id: str,
        order_value: float = 0.0,
        db: Optional[Session] = None
    ) -> Tuple[bool, Optional[str]]:
        """Compatibility helper for pre-entry safety validation."""
        local_db = db or SessionLocal()
        try:
            valid, reason = cls.validate_pre_entry(
                db=local_db,
                dhan_account_id=dhan_account_id,
                security_id=security_id,
                quantity=1,
                allocated_capital=order_value
            )
            return valid, reason if not valid else None
        finally:
            if not db:
                local_db.close()

    @classmethod
    def validate_pre_entry(
        cls,
        db: Session,
        dhan_account_id: str,
        security_id: str,
        quantity: int,
        allocated_capital: float = 0.0
    ) -> Tuple[bool, str]:
        """
        Pre-trade entry safety validation:
        1. Check Kill Switch
        2. Check Account Status
        3. Check Max Daily Orders
        4. Check Max Daily Loss
        5. Check Valid Quantity
        """
        if cls.is_kill_switch_active(db):
            logger.error("[SAFETY] Entry blocked: Emergency Kill Switch is ACTIVE.")
            return False, "Emergency Kill Switch is active. All new entries are blocked."

        acc = db.query(DhanAccount).filter(DhanAccount.id == dhan_account_id).first()
        if not acc:
            return False, f"Account {dhan_account_id} not found."
        if acc.account_status != AccountStatus.ACTIVE:
            return False, f"Account {dhan_account_id} status is {acc.account_status}. Orders disabled."

        if quantity <= 0:
            return False, f"Invalid order quantity: {quantity}"

        max_orders = int(os.getenv("MAX_ORDERS_PER_DAY", "50"))
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        daily_orders_count = (
            db.query(AtsOrder)
            .filter(
                AtsOrder.dhan_account_id == dhan_account_id,
                AtsOrder.created_at >= today_start
            )
            .count()
        )
        if daily_orders_count >= max_orders:
            logger.error(f"[SAFETY] Entry blocked for {dhan_account_id}: Daily orders limit ({max_orders}) reached.")
            return False, f"Maximum daily orders limit ({max_orders}) reached."

        max_loss = float(os.getenv("MAX_DAILY_LOSS", "25000.0"))
        today_trades = (
            db.query(Trade)
            .filter(
                Trade.dhan_account_id == dhan_account_id,
                Trade.trade_date == today_start.date(),
                Trade.trade_status == "CLOSED"
            )
            .all()
        )
        daily_realized_loss = sum(t.realized_pnl for t in today_trades if t.realized_pnl and t.realized_pnl < 0)
        if abs(daily_realized_loss) >= max_loss:
            logger.error(f"[SAFETY] Entry blocked for {dhan_account_id}: Daily realized loss ₹{abs(daily_realized_loss):.2f} exceeds limit ₹{max_loss:.2f}.")
            return False, f"Daily realized loss limit (₹{max_loss:.2f}) reached."

        return True, "Safety checks passed."

    @classmethod
    def validate_pre_exit(
        cls,
        trade: Trade,
        qty: int
    ) -> Tuple[bool, str]:
        """
        Pre-trade exit validation:
        Exits are always permitted even if Kill Switch is active.
        Verifies remaining quantity and active states.
        """
        if qty <= 0:
            return False, f"Invalid exit quantity: {qty}"

        if trade.ats_state in (AtsTradeState.CLOSED, AtsTradeState.CANCELLED, AtsTradeState.FAILED):
            return False, f"Cannot exit trade in terminal state {trade.ats_state}"

        remaining = trade.remaining_quantity or trade.allocated_quantity or 0
        if remaining <= 0:
            return False, f"Trade has zero remaining quantity ({remaining})"

        return True, "Exit safety checks passed."
