"""
app.data.repositories
=====================
Aggregates all repository interfaces and factory functions for the data layer.
"""

from app.data.account_repo import AccountRepository, get_account_repo
from app.data.trade_repo import TradeRepository, get_trade_repo
from app.data.company_repo import CompanyRepository, get_company_repo

__all__ = [
    "AccountRepository",
    "get_account_repo",
    "TradeRepository",
    "get_trade_repo",
    "CompanyRepository",
    "get_company_repo",
]
