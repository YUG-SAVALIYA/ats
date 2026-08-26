"""
cancel_order.py
===============
Standalone script to cancel a pending order on Dhan using DELETE /v2/orders/{order-id}.

Usage:
  python scripts/cancel_order.py <dhan_order_id>
"""

import os
import sys
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.data.database import SessionLocal
from app.data.models import AtsOrder
from app.trading.execution import get_order_executor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("cancel_order")


def main():
    logger.info("=========================================================")
    logger.info("           DHAN CANCEL ORDER API (DELETE)                ")
    logger.info("=========================================================")

    dhan_order_id = sys.argv[1] if len(sys.argv) > 1 else None

    db = SessionLocal()
    try:
        if not dhan_order_id:
            latest_order = (
                db.query(AtsOrder)
                .filter(AtsOrder.dhan_order_id.isnot(None))
                .order_by(AtsOrder.created_at.desc())
                .first()
            )
            if latest_order:
                dhan_order_id = latest_order.dhan_order_id

        if not dhan_order_id:
            logger.error("❌ No Dhan Order ID provided or found in DB.")
            logger.info("Usage: python scripts/cancel_order.py <dhan_order_id>")
            return

        logger.info(f"Target Dhan Order ID : {dhan_order_id}")
        logger.info(f"Endpoint             : DELETE https://api.dhan.co/v2/orders/{dhan_order_id}")

        executor = get_order_executor()

        logger.info("\nSending DELETE request to Dhan HQ API...")
        cancel_res = executor.cancel_order(dhan_order_id)

        success = cancel_res.get("success")
        order_status = cancel_res.get("order_status")

        if success:
            logger.info("\n=========================================================")
            logger.info(" ✅ CANCEL ORDER REQUEST ACCEPTED SUCCESSFULLY!")
            logger.info(f"    Dhan Order ID : {dhan_order_id}")
            logger.info(f"    Order Status  : {order_status}")
            logger.info("=========================================================")
        else:
            logger.warning(
                f"\n⚠️ Dhan response for Order ID {dhan_order_id}: {order_status} "
                f"(Order may already be executed/traded or invalid)"
            )

    except Exception as exc:
        logger.error(f"❌ Cancel Order failed: {exc}", exc_info=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
