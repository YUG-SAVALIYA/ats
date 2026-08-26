"""
sell_50pct_order.py
===================
Step 2: Script to place a 50% PARTIAL EXIT MARKET SELL order (1 share) on Dhan.
Dynamically checks `Company.is_mtf` from database (MTF if is_mtf=True, CNC if is_mtf=False).

Usage:
  python scripts/sell_50pct_order.py [dhan_order_id or trade_id]
"""

import os
import sys
import uuid
import asyncio
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.data.database import SessionLocal
from app.data.models import Trade, Company, OrderPurpose, AtsTradeState, AtsOrder
from app.trading.execution import place_market_sell

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sell_50pct")


async def main():
    logger.info("=========================================================")
    logger.info("  [STEP 2/3] DHAN 50% PARTIAL EXIT MARKET SELL           ")
    logger.info("=========================================================")

    arg_id = sys.argv[1] if len(sys.argv) > 1 else None

    db = SessionLocal()
    try:
        trade = None
        if arg_id:
            ats_ord = db.query(AtsOrder).filter(AtsOrder.dhan_order_id == arg_id).first()
            if ats_ord and ats_ord.trade_id:
                trade = db.query(Trade).filter(Trade.id == ats_ord.trade_id).first()

            if not trade:
                trade = db.query(Trade).filter(Trade.id == arg_id).first()

        if not trade:
            trade = (
                db.query(Trade)
                .filter(Trade.ats_state.in_([
                    AtsTradeState.OPEN,
                    AtsTradeState.ENTRY_PENDING,
                ]))
                .order_by(Trade.created_at.desc())
                .first()
            )

        if not trade:
            logger.error("❌ No matching OPEN trade found in DB. Please run `python scripts/buy_order.py` first to open a real position.")
            return

        total_qty = trade.remaining_quantity or trade.allocated_quantity or 2
        sell_qty = max(1, total_qty // 2)
        remaining_qty = max(0, total_qty - sell_qty)

        security_id = str(trade.security_id or "14366")
        company = db.query(Company).filter(Company.dhan_security_id == security_id).first()
        symbol = company.trading_symbol if company else "IDEA"
        
        product_type = "MTF" if (company and company.is_mtf) else "CNC"

        logger.info(f"Target Trade ID            : {trade.id}")
        logger.info(f"Trading Symbol             : {symbol} (Sec ID: {security_id})")
        logger.info(f"Company is_mtf             : {getattr(company, 'is_mtf', False)}")
        logger.info(f"Product Type               : {product_type}")
        logger.info(f"Current DB Trade State     : {trade.ats_state}")
        logger.info(f"Total Position Qty         : {total_qty} shares")
        logger.info(f"50% Sell Quantity          : {sell_qty} share(s)")
        logger.info(f"Remaining Qty (after fill) : {remaining_qty} share(s)")

        logger.info(f"\nSending 50% PARTIAL EXIT MARKET SELL ({product_type}) to Dhan HQ API...")
        sell_order = await place_market_sell(
            trade_id=trade.id,
            security_id=security_id,
            qty=sell_qty,
            purpose=OrderPurpose.PARTIAL_EXIT,
            tag=f"P50_{trade.id[:6]}",
            product_type=product_type
        )

        dhan_order_id = getattr(sell_order, "dhan_order_id", "N/A")
        ord_id = getattr(sell_order, "id", "N/A")
        ord_status = getattr(sell_order, "status", "N/A")

        logger.info("\n=========================================================")
        if ord_status == "TRANSIT":
            logger.info(f" ✅ REAL 50% PARTIAL EXIT MARKET SELL ({product_type}) SUBMITTED TO DHAN!")
            logger.info(f"    Dhan Order ID : {dhan_order_id}")
            logger.info(f"    AtsOrder ID   : {ord_id}")
            logger.info(f"    Product Type  : {product_type}")
            logger.info(f"    Order Purpose : PARTIAL_EXIT")
            logger.info(f"    Order Status  : {ord_status}")
            logger.info(f"    Trade State   : EXIT_REQUESTED (Awaiting broker execution fill)")
            logger.info("=========================================================")
            logger.info(f"\n👉 NEXT STEP: Once filled, run step 3 to sell the remaining share:\n   python scripts/sell_order.py {trade.id}")
        else:
            logger.warning(f" ⚠️ Order returned with status: {ord_status}")
            logger.info(f"    Dhan Order ID : {dhan_order_id}")
            logger.info(f"    AtsOrder ID   : {ord_id}")
            logger.info(f"    Note: Duplicate protection blocked sending another order because order {ord_id} is already in state {ord_status}.")
            logger.info("=========================================================")

    except Exception as exc:
        logger.error(f"❌ 50% Partial Sell failed: {exc}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
