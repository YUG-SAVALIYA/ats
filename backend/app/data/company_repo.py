"""
app.data.company_repo
=====================
Repository layer for Company master, Daily/Weekly Candles, and Active Subscriptions.
"""

from typing import Optional, List
from sqlalchemy.orm import Session
from app.data.models import Company, DailyCandle, WeeklyCandle, ActiveSubscription


class CompanyRepository:
    """Encapsulates database operations for Companies and Candles."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, company_id: str) -> Optional[Company]:
        return self.db.query(Company).filter(Company.id == company_id).first()

    def get_by_security_id(self, security_id: str) -> Optional[Company]:
        return self.db.query(Company).filter(Company.dhan_security_id == str(security_id)).first()

    def get_by_symbol(self, symbol: str) -> Optional[Company]:
        return (
            self.db.query(Company)
            .filter(Company.trading_symbol.ilike(symbol.strip()))
            .first()
        )

    def search_companies(self, query: str, limit: int = 50) -> List[Company]:
        q = f"%{query.strip()}%"
        return (
            self.db.query(Company)
            .filter(
                (Company.trading_symbol.ilike(q)) | (Company.company_name.ilike(q))
            )
            .limit(limit)
            .all()
        )

    def get_all_active_companies(self, limit: int = 4000) -> List[Company]:
        return (
            self.db.query(Company)
            .filter(Company.is_active == True)
            .limit(limit)
            .all()
        )

    def get_active_subscriptions(self) -> List[str]:
        subs = self.db.query(ActiveSubscription.security_id).all()
        return [s[0] for s in subs]


def get_company_repo(db: Session) -> CompanyRepository:
    return CompanyRepository(db)
