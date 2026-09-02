"""
scripts/sync_universe_candles.py
================================
Syncs daily candles up to 2026-09-01 for all universe companies (Market Cap >= 8,000 Cr).
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import time
import logging
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text
from database.database import SessionLocal
from database.models import Company
from market.candles import sync_candles_for_company

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_universe")

def sync_universe():
    db = SessionLocal()
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != "",
            Company.market_cap >= 8000
        ).all()
        comp_list = [(c.id, c.dhan_security_id, c.trading_symbol) for c in companies]
    finally:
        db.close()

    print(f"[*] Syncing daily candles for {len(comp_list)} companies (Market Cap >= 8,000 Cr)...")

    def _sync(item):
        cid, sec_id, sym = item
        try:
            res = sync_candles_for_company(cid, sec_id, "NSE_EQ")
            return sym, res.get("daily_inserted", 0), res.get("daily_updated", 0)
        except Exception as e:
            return sym, -1, str(e)

    total_inserted = 0
    total_updated = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_sync, item): item for item in comp_list}
        for f in as_completed(futures):
            sym, ins, upd = f.result()
            if ins > 0 or upd > 0:
                total_inserted += ins
                total_updated += upd

    print(f"[*] Sync complete: {total_inserted} inserted, {total_updated} updated.")

if __name__ == "__main__":
    sync_universe()
