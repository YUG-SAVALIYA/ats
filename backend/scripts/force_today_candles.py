import sys
import os
import logging
from datetime import date
from typing import List, Dict, Any

# Ensure ATS is in Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Company, DailyCandle
from app.services.dhan_client import get_dhan_data_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("force_today")

def _upsert_daily_candles(db, company_id: str, candles: List[Dict]) -> int:
    """Upsert daily candles into DB."""
    existing = {
        r.date: r
        for r in db.query(DailyCandle).filter(DailyCandle.company_id == company_id).all()
    }
    
    new_count = 0
    for c in candles:
        dt_val = c["date"]
        
        if dt_val in existing:
            # Update
            row = existing[dt_val]
            row.open = c["open"]
            row.high = c["high"]
            row.low = c["low"]
            row.close = c["close"]
            row.volume = c.get("volume", 0)
        else:
            # Insert
            new_row = DailyCandle(
                company_id=company_id,
                date=dt_val,
                open=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c.get("volume", 0)
            )
            db.add(new_row)
            new_count += 1
            
    return new_count

def force_fetch_today():
    logger.info("Starting force fetch of today's candles using Live Marketfeed...")
    db = SessionLocal()
    client = get_dhan_data_client()
    today = date.today()
    
    try:
        companies = db.query(Company).filter(
            Company.is_active == True,
            Company.dhan_security_id != None,
            Company.dhan_security_id != ""
        ).all()
        
        sec_to_comp = {int(c.dhan_security_id): c.id for c in companies if c.dhan_security_id.isdigit()}
        sec_ids = list(sec_to_comp.keys())
        
        logger.info(f"Found {len(sec_ids)} active security IDs.")
        
        # Batch into groups of 500
        chunk_size = 500
        total_inserted = 0
        total_updated = 0
        
        for i in range(0, len(sec_ids), chunk_size):
            chunk = sec_ids[i:i+chunk_size]
            data = client.get_marketfeed_ohlc(chunk)
            
            for sec_id_str, ohlc in data.items():
                sec_id = int(sec_id_str)
                comp_id = sec_to_comp.get(sec_id)
                if not comp_id:
                    continue
                    
                inner_ohlc = ohlc.get("ohlc", {})
                
                c_data = [{
                    "date": today,
                    "open": float(inner_ohlc.get("open", 0)),
                    "high": float(inner_ohlc.get("high", 0)),
                    "low": float(inner_ohlc.get("low", 0)),
                    "close": float(inner_ohlc.get("close", 0)),
                    "volume": int(ohlc.get("volume", 0))
                }]
                
                # Only insert if close > 0
                if c_data[0]["close"] > 0:
                    inserted = _upsert_daily_candles(db, comp_id, c_data)
                    total_inserted += inserted
                    if inserted == 0:
                        total_updated += 1
            
            db.commit()
            logger.info(f"Processed chunk {i//chunk_size + 1}")
            
        logger.info(f"DONE! Inserted 17th candles for {total_inserted} companies. Updated for {total_updated}.")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    force_fetch_today()
