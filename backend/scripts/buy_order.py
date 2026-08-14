"""
buy_order.py
============
Step 1: Standalone script to place a real MARKET BUY entry order on Dhan for 2 shares of IDEA (Security ID: 14366).
Dynamically checks `Company.is_mtf` from database (MTF if is_mtf=True, CNC if is_mtf=False).

Usage:
  python scripts/buy_order.py [quantity]
"""

import os
import sys
import uuid
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models import Company
from app.core.executor import get_order_executor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("buy_order")


def main():
    logger.info("=========================================================")
    logger.info("  [STEP 1/3] DHAN MARKET BUY ORDER PLACEMENT             ")
    logger.info("=========================================================")

    qty = 2  # Strictly 2 shares of IDEA

    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.trading_symbol == "IDEA").first()
        if not company:
            company = db.query(Company).filter(Company.dhan_security_id == "14366").first()
        if not company:
            company = Company(
                id=str(uuid.uuid4()),
                dhan_security_id="14366",
                trading_symbol="IDEA",
                company_name="Vodafone Idea Limited",
                exchange="NSE",
                is_mtf=True,
                is_active=True
            )
            db.add(company)
            db.commit()
            db.refresh(company)

        product_type = "MTF" if company.is_mtf else "CNC"

        logger.info(f"Target Instrument : {company.trading_symbol} (Security ID: {company.dhan_security_id})")
        logger.info(f"Quantity          : {qty} shares")
        logger.info(f"Company is_mtf    : {company.is_mtf}")
        logger.info(f"Selected Product  : {product_type}")

        executor = get_order_executor()

        logger.info(f"\nSending MARKET BUY ({product_type}) Order to Dhan HQ API...")
        buy_res = executor.place_entry_order(
            security_id=str(company.dhan_security_id),
            trading_symbol=company.trading_symbol,
            company_id=company.id,
            signal_id=None,
            quantity=qty,
            allocated_capital=10.0 * qty,
            product_type=product_type,
        )

        status = buy_res.get("status")
        dhan_order_id = buy_res.get("dhan_order_id")
        trade_id = buy_res.get("trade_id")

        if status == "placed" and dhan_order_id:
            logger.info("\n=========================================================")
            logger.info(f" ✅ REAL MARKET BUY ({product_type}) ORDER PLACED ON DHAN SUCCESSFULLY!")
            logger.info(f"    Dhan Order ID : {dhan_order_id}")
            logger.info(f"    ATS Trade ID  : {trade_id}")
            logger.info(f"    Product Type  : {product_type}")
            logger.info("=========================================================")
            logger.info(f"\n👉 NEXT STEP: Run step 2 to sell 50% (1 share):\n   python scripts/sell_50pct_order.py")
        else:
            err = buy_res.get("error", "Unknown error")
            logger.error(f"❌ Dhan Rejected MARKET BUY: {err}")

    except Exception as exc:
        logger.error(f"❌ Market Buy failed: {exc}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
