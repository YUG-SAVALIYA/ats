"""
app.data.trade_repo
===================
Repository layer for Trade, AtsOrder, OrderAttempt, and TradeEvent entities.
"""

from typing import Optional, List
from sqlalchemy.orm import Session
from app.data.models import (
    Trade, AtsOrder, OrderAttempt, TradeEvent, AtsTradeState, OrderPurpose
)


class TradeRepository:
    """Encapsulates database operations for active trades and orders."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, trade_id: str) -> Optional[Trade]:
        return self.db.query(Trade).filter(Trade.id == trade_id).first()

    def get_by_id_for_update(self, trade_id: str) -> Optional[Trade]:
        return self.db.query(Trade).filter(Trade.id == trade_id).with_for_update().first()

    def get_trades_by_scope(
        self,
        account_ids: Optional[List[str]] = None,
        strategy_type: Optional[str] = None
    ) -> List[Trade]:
        q = self.db.query(Trade)
        if strategy_type:
            q = q.filter(Trade.strategy_type == strategy_type)
        if account_ids is not None:
            q = q.filter(Trade.dhan_account_id.in_(account_ids))
        return q.all()

    def get_open_trades_for_security(self, security_id: str) -> List[Trade]:
        return self.db.query(Trade).filter(
            Trade.security_id == str(security_id),
            Trade.ats_state.in_([AtsTradeState.OPEN, AtsTradeState.PARTIAL_EXIT, "OPEN", "PARTIAL_EXIT"])
        ).all()

    def get_active_trades(
        self,
        account_ids: Optional[List[str]] = None,
        strategy_type: Optional[str] = None
    ) -> List[Trade]:
        """Fetch active trades (ENTRY_PENDING, OPEN, PARTIAL_EXIT) scoped to accounts."""
        q = self.db.query(Trade).filter(
            Trade.ats_state.in_([
                AtsTradeState.ENTRY_PENDING,
                AtsTradeState.OPEN,
                AtsTradeState.PARTIAL_EXIT,
                "ENTRY_PENDING",
                "OPEN",
                "PARTIAL_EXIT",
            ])
        )
        if strategy_type:
            q = q.filter(Trade.strategy_type == strategy_type)
        if account_ids is not None:
            q = q.filter(Trade.dhan_account_id.in_(account_ids))
        return q.all()

    def get_order_by_purpose(
        self,
        trade_id: str,
        purpose: OrderPurpose
    ) -> Optional[AtsOrder]:
        return (
            self.db.query(AtsOrder)
            .filter(
                AtsOrder.trade_id == trade_id,
                AtsOrder.order_purpose == purpose
            )
            .first()
        )

    def list_orders(
        self,
        account_ids: Optional[List[str]] = None,
        strategy_type: Optional[str] = None,
        limit: int = 200
    ) -> List[AtsOrder]:
        """Tenant-scoped query for AtsOrder listing."""
        q = self.db.query(AtsOrder)
        if strategy_type:
            q = q.filter(AtsOrder.strategy_type == strategy_type)
        if account_ids is not None:
            q = q.filter(AtsOrder.dhan_account_id.in_(account_ids))
        return q.order_by(AtsOrder.created_at.desc()).limit(limit).all()


def get_trade_repo(db: Session) -> TradeRepository:
    return TradeRepository(db)
