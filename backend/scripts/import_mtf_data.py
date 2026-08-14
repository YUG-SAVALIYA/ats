"""
import_mtf_data.py
===================
Script to parse Dhan MTF CSV file, update ISIN & MTF availability in the `companies` DB table,
and generate accurate matching & market cap statistics.

CSV File:
  Dhan - Leverage & Margins for Trading on Scrips - Leverage for Intraday & Margin.csv
"""

import os
import sys
import csv
import logging

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import SessionLocal
from app.models import Company

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("import_mtf")


def main():
    logger.info("=========================================================")
    logger.info("  DHAN MTF CSV IMPORT & DB MATCHING ANALYSIS             ")
    logger.info("=========================================================")

    csv_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        "..",
        "Dhan - Leverage & Margins for Trading on Scrips - Leverage for Intraday & Margin.csv"
    ))

    if not os.path.exists(csv_path):
        logger.error(f"❌ CSV file not found at: {csv_path}")
        return

    db = SessionLocal()
    try:
        all_companies = db.query(Company).all()
        db_company_by_symbol = {c.trading_symbol.strip().upper(): c for c in all_companies if c.trading_symbol}
        db_company_by_isin = {c.isin.strip().upper(): c for c in all_companies if c.isin}

        total_db_companies = len(all_companies)
        logger.info(f"Loaded {total_db_companies} companies from PostgreSQL database.")

        csv_rows = []
        csv_total_scrips = 0
        csv_mtf_scrips = 0

        with open(csv_path, mode="r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = (row.get("Scrip Name") or "").strip().upper()
                isin = (row.get("ISIN No") or "").strip().upper()
                mtf_raw = (row.get("MTF") or "").strip()

                if not symbol and not isin:
                    continue

                csv_total_scrips += 1
                has_mtf = bool(mtf_raw and mtf_raw != "-" and mtf_raw != "0")
                if has_mtf:
                    csv_mtf_scrips += 1

                csv_rows.append({
                    "symbol": symbol,
                    "isin": isin,
                    "mtf_raw": mtf_raw,
                    "has_mtf": has_mtf
                })

        logger.info(f"Loaded {csv_total_scrips} scrips from Dhan CSV file.")
        logger.info(f"CSV Scrips with MTF available: {csv_mtf_scrips}")

        matched_companies = set()
        matched_mtf_companies = set()
        updated_count = 0

        for r in csv_rows:
            symbol = r["symbol"]
            isin = r["isin"]
            mtf_raw = r["mtf_raw"]
            has_mtf = r["has_mtf"]

            company = None
            if isin and isin in db_company_by_isin:
                company = db_company_by_isin[isin]
            elif symbol and symbol in db_company_by_symbol:
                company = db_company_by_symbol[symbol]

            if company:
                matched_companies.add(company.id)
                if has_mtf:
                    matched_mtf_companies.add(company.id)

                company.is_mtf = has_mtf
                company.mtf_leverage = mtf_raw if has_mtf else None
                if isin and not company.isin:
                    company.isin = isin
                    db_company_by_isin[isin] = company

                updated_count += 1

        db.commit()

        total_db = db.query(Company).count()
        total_mtf_db = db.query(Company).filter(Company.is_mtf == True).count()

        mcap_8000_total = db.query(Company).filter(Company.market_cap >= 8000).count()
        mcap_8000_mtf = db.query(Company).filter(
            Company.market_cap >= 8000,
            Company.is_mtf == True
        ).count()

        mcap_8000_non_mtf = mcap_8000_total - mcap_8000_mtf

        logger.info("\n=========================================================")
        logger.info("              📊 STATISTICAL SUMMARY RESULT              ")
        logger.info("=========================================================")
        logger.info(f" 1. Total Scrips in Dhan CSV File      : {csv_total_scrips}")
        logger.info(f" 2. Total Companies in Backend DB      : {total_db}")
        logger.info(f" 3. Companies SAME IN BOTH (Matched)  : {len(matched_companies)}")
        logger.info(f" 4. Companies in DB with MTF Available : {total_mtf_db}")
        logger.info(f" 5. Companies (M-Cap >= 8000 Cr) Total : {mcap_8000_total}")
        logger.info(f" 6. Companies (M-Cap >= 8000 Cr) + MTF : {mcap_8000_mtf}")
        logger.info(f" 7. Companies (M-Cap >= 8000 Cr) NO MTF: {mcap_8000_non_mtf}")
        logger.info("=========================================================\n")

    except Exception as exc:
        logger.error(f"❌ Error importing MTF data: {exc}", exc_info=True)
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
