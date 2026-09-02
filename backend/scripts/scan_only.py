import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from datetime import date
from trading.signals import scan_signals_from_db, get_signals_from_db

def main():
    print("Executing full multi-timeframe signal scan across all stocks in database...")
    new_sigs = scan_signals_from_db()
    
    today_str = str(date.today())
    all_sigs = get_signals_from_db(limit=100)
    
    print("\n" + "=" * 120)
    print(f"  ATS STRATEGY SIGNAL REPORT — {today_str}")
    print("=" * 120)
    
    actionable = [s for s in all_sigs if s.get("signal_date") == today_str or s.get("status") == "PENDING"]
    
    if not actionable:
        print("\n  [i] No stocks matched the entry criteria for today.")
        print("      Conditions required:")
        print("      1. Supertrend Strategy: Daily Supertrend (10, 1.5) Green Flip + Weekly RSI (14) > 60 + Daily RSI (14) > 60 + Candle Range <= 7% + Market Cap >= 8,000 Cr")
        print("      2. Monthly RSI Strategy: Monthly RSI (14) Cross Above 60 + Today Close > Yesterday High")
    else:
        print(f"\n  Found {len(actionable)} Signal(s):\n")
        header = f"{'Symbol':<15} | {'Strategy':<14} | {'Date':<10} | {'Close (Rs)':<10} | {'High (Rs)':<10} | {'Daily RSI':<10} | {'Wkly RSI':<10} | {'Status':<10}"
        print(header)
        print("-" * 120)
        for s in actionable:
            sym = s.get("trading_symbol", "N/A")
            strat = s.get("strategy_type", "SUPERTREND")
            d = s.get("signal_date", "N/A")
            c = f"{s.get('signal_close', 0.0):.2f}" if s.get('signal_close') else "N/A"
            h = f"{s.get('signal_high', 0.0):.2f}" if s.get('signal_high') else "N/A"
            dr = f"{s.get('daily_rsi', 0.0):.2f}" if s.get('daily_rsi') else "N/A"
            wr = f"{s.get('weekly_rsi', 0.0):.2f}" if s.get('weekly_rsi') else "N/A"
            st = s.get("status", "PENDING")
            print(f"{sym:<15} | {strat:<14} | {d:<10} | {c:<10} | {h:<10} | {dr:<10} | {wr:<10} | {st:<10}")
            
    print("\n" + "=" * 120)

if __name__ == "__main__":
    main()
