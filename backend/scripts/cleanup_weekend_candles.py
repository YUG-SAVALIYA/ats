"""
cleanup_weekend_candles.py
==========================
Purges all Saturday and Sunday daily candles from PostgreSQL `daily_candles` table
and re-aggregates weekly candles natively across all active companies.

Usage:
  venv\Scripts\python.exe scripts/cleanup_weekend_candles.py
"""

import os
import sys
import logging
from sqlalchemy import text

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.data.database import SessionLocal
from app.data.models import Company
from app.data.weekly import aggregate_weekly_candles

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("cleanup_weekend_candles")


def main():
    logger.info("==================================================================")
    logger.info("      PURGING WEEKEND (SAT/SUN) DAILY CANDLES FROM POSTGRESQL     ")
    logger.info("==================================================================")

    db = SessionLocal()
    try:
        # Step 1: Count existing weekend daily candles
        count_before = db.execute(
            text("SELECT COUNT(*) FROM daily_candles WHERE EXTRACT(DOW FROM date) IN (0, 6)")
        ).scalar()

        logger.info(f" Weekend (Saturday/Sunday) daily candles found in DB: {count_before}")

        if count_before > 0:
            # Step 2: Delete weekend daily candles
            res = db.execute(
                text("DELETE FROM daily_candles WHERE EXTRACT(DOW FROM date) IN (0, 6)")
            )
            db.commit()
            logger.info(f" ✅ Successfully deleted {res.rowcount} weekend daily candles from `daily_candles` table!")
        else:
            logger.info(" ✅ Zero weekend daily candles found in DB. DB is clean!")

        # Verify count after deletion
        count_after = db.execute(
            text("SELECT COUNT(*) FROM daily_candles WHERE EXTRACT(DOW FROM date) IN (0, 6)")
        ).scalar()
        assert count_after == 0, f"Expected 0 weekend candles remaining, got {count_after}"
        logger.info(f" ✅ Post-Cleanup Verification: {count_after} weekend daily candles remain in DB.")

        # Step 3: Re-aggregate weekly candles for all active companies
        logger.info("\n--- Re-aggregating Weekly Candles from Valid Mon-Fri Daily Bars ---")
        companies = db.query(Company).filter(Company.is_active == True).all()
        logger.info(f" Re-aggregating weekly candles for {len(companies)} active companies...")

        updated_count = 0
        for comp in companies:
            try:
                res = aggregate_weekly_candles(company_id=comp.id)
                updated_count += res.get("processed_rows", 0)
            except Exception as exc:
                logger.warning(f" Could not aggregate weekly candles for {comp.trading_symbol}: {exc}")

        logger.info(f" ✅ Successfully updated/aggregated {updated_count} weekly candles across {len(companies)} companies!")

    except Exception as exc:
        db.rollback()
        logger.error(f" Error during weekend candle cleanup: {exc}")
        raise exc
    finally:
        db.close()

    logger.info("\n==================================================================")
    logger.info(" 🎉 WEEKEND CANDLE CLEANUP & RE-AGGREGATION COMPLETED SUCCESSFULLY! ")
    logger.info("==================================================================")


if __name__ == "__main__":
    main()
