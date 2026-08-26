"""
app.api.stocks
==============
Stock master search, symbol lookup, and company logo endpoints.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from app.data.database import get_db
from app.data.repositories import get_company_repo
from app.data.models import Company

router = APIRouter(tags=["Stocks"])


@router.get("/stocks/search")
def search_companies(
    q: str = Query("", description="Symbol, security ID, or company name"),
    db: Session = Depends(get_db)
):
    """Search active companies by symbol, name, or Dhan security ID."""
    try:
        repo = get_company_repo(db)
        if q.strip():
            results = repo.search_companies(q.strip(), limit=50)
        else:
            results = repo.get_all_active_companies(limit=50)

        return [
            {
                "id": c.id,
                "security_id": c.dhan_security_id,
                "trading_symbol": c.trading_symbol,
                "company_name": c.company_name,
                "exchange": c.exchange,
                "market_cap": c.market_cap,
                "is_active": c.is_active,
                "img_url": c.img_url,
            }
            for c in results
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search failed: {exc}")


@router.get("/stocks/{symbol}")
def get_company_details(symbol: str, db: Session = Depends(get_db)):
    """Fetch company master record by symbol or security ID."""
    try:
        repo = get_company_repo(db)
        company = repo.get_by_symbol(symbol) or repo.get_by_security_id(symbol)
        if not company:
            raise HTTPException(status_code=404, detail=f"Company '{symbol}' not found.")
        return {
            "id": company.id,
            "security_id": company.dhan_security_id,
            "trading_symbol": company.trading_symbol,
            "company_name": company.company_name,
            "exchange": company.exchange,
            "isin": company.isin,
            "segment": company.segment,
            "market_cap": company.market_cap,
            "is_active": company.is_active,
            "img_url": company.img_url,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Company lookup failed: {exc}")


@router.get("/companies/images")
def get_company_images(db: Session = Depends(get_db)):
    """Returns mapping of trading_symbol to logo image URL."""
    try:
        companies = db.query(Company.trading_symbol, Company.img_url).filter(Company.img_url.isnot(None)).all()
        return {c[0]: c[1] for c in companies}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch company images: {exc}")
