"""
app.data.account_repo
=====================
Repository layer for DhanAccount, User, and legacy credential queries.
"""

from typing import Optional, List
from sqlalchemy.orm import Session
from app.data.models import DhanAccount, User, AccountStatus


class AccountRepository:
    """Encapsulates all database operations for Dhan accounts and Users."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, dhan_account_id: str) -> Optional[DhanAccount]:
        return self.db.query(DhanAccount).filter(DhanAccount.id == dhan_account_id).first()

    def get_by_client_id(self, client_id: str) -> Optional[DhanAccount]:
        return self.db.query(DhanAccount).filter(DhanAccount.client_id == str(client_id)).first()

    def get_all_active_accounts(self) -> List[DhanAccount]:
        return (
            self.db.query(DhanAccount)
            .filter(DhanAccount.account_status == AccountStatus.ACTIVE)
            .all()
        )

    get_active_accounts = get_all_active_accounts

    def get_accounts_for_user(self, user_id: str) -> List[DhanAccount]:
        return self.db.query(DhanAccount).filter(DhanAccount.user_id == user_id).all()

    def get_data_account(self) -> Optional[DhanAccount]:
        """Returns the designated market-data account."""
        acc = (
            self.db.query(DhanAccount)
            .filter(
                DhanAccount.is_data_account == True,
                DhanAccount.account_status == AccountStatus.ACTIVE
            )
            .first()
        )
        if acc:
            return acc
        active = self.get_all_active_accounts()
        return active[0] if active else None

    def get_user_by_email(self, email: str) -> Optional[User]:
        return self.db.query(User).filter(User.email == email.strip().lower()).first()


def get_account_repo(db: Session) -> AccountRepository:
    return AccountRepository(db)
