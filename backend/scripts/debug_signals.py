import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SessionLocal
from models import Company
from services.candle_sync import get_daily_candles_from_db, get_weekly_candles_from_db
from services.strategy import calculate_rsi, calculate_supertrend

def run_debug():
    db = SessionLocal()
    companies = db.query(Company).filter(Company.is_active == True, Company.dhan_security_id != '').all()
    print(f"Total active companies: {len(companies)}")

    for c in companies:
        dc = get_daily_candles_from_db(c.id, limit=60)
        wc = get_weekly_candles_from_db(c.id, limit=30)
        
        if len(dc) < 22 or len(wc) < 15:
            continue
            
        # Debugging condition 5: Supertrend flip
        st = calculate_supertrend(dc, period=21, multiplier=1.5)
        if len(st) < 2: continue
        
        prev_dir = st[-2]
        curr_dir = st[-1]
        
        if prev_dir == -1 and curr_dir == 1:
            market_cap_cr = (c.market_cap or 0) / 1e7
            latest_candle = dc[-1]
            high = latest_candle["high"]
            low = latest_candle["low"]
            candle_range_pct = ((high - low) / low) * 100.0 if low > 0 else 0
            
            daily_closes = [cd["close"] for cd in dc]
            daily_rsi = calculate_rsi(daily_closes, 14)
            
            weekly_closes = [cd["close"] for cd in wc]
            weekly_rsi = calculate_rsi(weekly_closes, 14)
            
            print(f"ST FLIP FOUND: {c.trading_symbol} on {latest_candle['date']}")
            print(f"  -> Range: {candle_range_pct:.2f}% (Needs 3-8%)")
            print(f"  -> Daily RSI: {daily_rsi} (Needs 50-75)")
            print(f"  -> Weekly RSI: {weekly_rsi} (Needs 65-80)")
            print(f"  -> Market Cap: {market_cap_cr} (Needs > 8000)")
            print("---")

if __name__ == "__main__":
    run_debug()
