"""
scripts/scan_today_live.py
==========================
1. Loads historical daily candles up to 2026-08-28 from DB.
2. Fetches today's (2026-08-31) daily OHLC from Dhan marketfeed snapshot.
3. Appends today's bar and runs full Multi-Timeframe Supertrend + Monthly RSI scan.
4. Reports all stocks triggering signals on today's market close.
"""

import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import logging
from datetime import date, datetime, timedelta
from collections import defaultdict
from sqlalchemy import text

from database.database import SessionLocal
from database.models import Company, Signal
from dhan.market import get_live_ohlc
from market.weekly import filter_completed_weekly_candles
from trading.strategies import evaluate_stock_signal, evaluate_monthly_rsi_signal
from trading.signals import save_signal_to_db

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("scan_today_live")

def scan_today():
    today = date.today()
    today_str = str(today)
    print("=" * 120)
    print(f"  SCANNING TODAY'S MARKET CLOSE ({today_str}) FOR NEW SIGNALS")
    print("=" * 120)
    
    db = SessionLocal()
    try:
        # 1. Fetch eligible companies >= 8,000 Cr
        comp_rows = db.execute(text(
            "SELECT id, trading_symbol, dhan_security_id, market_cap "
            "FROM companies WHERE is_active = true AND dhan_security_id != '' AND market_cap >= 8000"
        )).fetchall()
        
        comp_map = {
            r[0]: {
                "id": r[0],
                "symbol": r[1],
                "sec_id": r[2],
                "mcap": float(r[3] or 0)
            }
            for r in comp_rows
        }
        
        print(f"[*] Target Universe: {len(comp_map)} Companies with Market Cap >= 8,000 Cr.")
        
        # 2. Fetch historical daily candles (last 730 days)
        cutoff_daily = today - timedelta(days=730)
        daily_rows = db.execute(
            text(
                "SELECT d.company_id, d.date, d.open, d.high, d.low, d.close, d.volume "
                "FROM daily_candles d "
                "JOIN companies c ON d.company_id = c.id "
                "WHERE c.is_active = true AND c.market_cap >= 8000 AND d.date >= :cutoff "
                "ORDER BY d.company_id, d.date ASC"
            ),
            {"cutoff": cutoff_daily}
        ).fetchall()
        
        daily_by_comp = defaultdict(list)
        for r in daily_rows:
            daily_by_comp[r[0]].append({
                "date": r[1],
                "open": float(r[2]),
                "high": float(r[3]),
                "low": float(r[4]),
                "close": float(r[5]),
                "volume": int(r[6] or 0)
            })
            
        print(f"[*] Loaded historical candles for {len(daily_by_comp)} companies.")
        
        # 3. Batch fetch today's live OHLC snapshot from Dhan
        sec_ids = [int(c["sec_id"]) for c in comp_map.values() if c["sec_id"].isdigit()]
        print(f"[*] Fetching today's OHLC market snapshot from Dhan for {len(sec_ids)} securities...")
        
        snapshot = {}
        import time
        # Batch in chunks of 500 with rate limit delay
        for i in range(0, len(sec_ids), 500):
            chunk = sec_ids[i:i+500]
            try:
                res = get_live_ohlc(chunk)
                if isinstance(res, dict):
                    snapshot.update(res)
            except Exception as e:
                logger.warning(f"Error fetching live snapshot chunk: {e}")
            time.sleep(1.0)
                
        print(f"[*] Received today's market snapshot for {len(snapshot)} securities.")
        
        new_signals = []
        
        for cid, comp in comp_map.items():
            daily = list(daily_by_comp.get(cid, []))
            symbol = comp["symbol"]
            sec_id = comp["sec_id"]
            mcap = comp["mcap"]
            
            # Check if today's candle is already in daily, else append from snapshot
            has_today = any(c["date"] == today for c in daily)
            if not has_today and sec_id in snapshot:
                snap_data = snapshot[sec_id]
                ohlc = snap_data.get("ohlc", {})
                if ohlc and ohlc.get("close", 0) > 0:
                    daily.append({
                        "date": today,
                        "open": float(ohlc.get("open", 0)),
                        "high": float(ohlc.get("high", 0)),
                        "low": float(ohlc.get("low", 0)),
                        "close": float(ohlc.get("close", 0)),
                        "volume": 0
                    })
                    
            if len(daily) < 22:
                continue
                
            # Aggregate weekly candles
            weekly_map = {}
            for r in daily:
                mon = r["date"] - timedelta(days=r["date"].weekday())
                if mon not in weekly_map:
                    weekly_map[mon] = {"date": mon, "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
                else:
                    weekly_map[mon]["high"] = max(weekly_map[mon]["high"], r["high"])
                    weekly_map[mon]["low"] = min(weekly_map[mon]["low"], r["low"])
                    weekly_map[mon]["close"] = r["close"]
                    weekly_map[mon]["volume"] += r["volume"]
            weekly = sorted(weekly_map.values(), key=lambda x: x["date"])
            completed_weekly = filter_completed_weekly_candles(weekly, current_date=daily[-1]["date"])
            
            # 1. Evaluate Supertrend Strategy on today's candle
            if len(completed_weekly) >= 15:
                try:
                    sig = evaluate_stock_signal(
                        symbol=symbol,
                        security_id=sec_id,
                        exchange_segment="NSE_EQ",
                        daily_candles=daily,
                        weekly_candles=completed_weekly,
                        market_cap_cr=mcap,
                        current_date=today
                    )
                    if sig and str(sig.get("signal_date")) == today_str:
                        save_signal_to_db(cid, sig, strategy_type="SUPERTREND")
                        new_signals.append(sig)
                        print(f"  🟢 [NEW SUPERTREND SIGNAL] {symbol} | Close: {sig['signal_close']} | Daily RSI: {sig['daily_rsi']:.1f} | Wkly RSI: {sig['weekly_rsi']:.1f} | Range: {sig['candle_range']:.1f}%")
                except Exception as exc:
                    pass
                    
            # 2. Evaluate Monthly RSI Strategy on today's candle
            try:
                monthly_map = {}
                for r in daily:
                    month_key = r["date"].strftime("%Y-%m")
                    if month_key not in monthly_map:
                        monthly_map[month_key] = {"date": r["date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r["volume"]}
                    else:
                        monthly_map[month_key]["high"] = max(monthly_map[month_key]["high"], r["high"])
                        monthly_map[month_key]["low"] = min(monthly_map[month_key]["low"], r["low"])
                        monthly_map[month_key]["close"] = r["close"]
                        monthly_map[month_key]["volume"] += r["volume"]
                monthly_candles = sorted(monthly_map.values(), key=lambda x: x["date"])
                
                if len(monthly_candles) >= 14:
                    m_sig = evaluate_monthly_rsi_signal(
                        symbol=symbol,
                        security_id=sec_id,
                        exchange_segment="NSE_EQ",
                        daily_candles=daily,
                        monthly_candles=monthly_candles
                    )
                    if m_sig and str(m_sig.get("signal_date")) == today_str:
                        save_signal_to_db(cid, m_sig, strategy_type="MONTHLY_RSI")
                        new_signals.append(m_sig)
                        print(f"  🟢 [NEW MONTHLY RSI SIGNAL] {symbol} | Close: {m_sig['signal_close']}")
            except Exception as exc:
                pass
                
        print("\n" + "=" * 120)
        print(f"  RESULTS SUMMARY FOR TODAY ({today_str}):")
        print("=" * 120)
        if not new_signals:
            print("\n  [i] No new signals triggered on today's candle.")
            print("      All 621 stocks were evaluated against Supertrend Green Flip + RSI filters.")
        else:
            print(f"\n  Found {len(new_signals)} Fresh Signal(s) on Today's Candle:\n")
            header = f"{'Symbol':<15} | {'Strategy':<14} | {'Close (Rs)':<12} | {'High (Rs)':<12} | {'Daily RSI':<10} | {'Wkly RSI':<10} | {'Range %':<8}"
            print(header)
            print("-" * 120)
            for s in new_signals:
                sym = s.get("symbol", "N/A")
                strat = s.get("strategy_type", "SUPERTREND")
                c = f"{s.get('signal_close', 0.0):.2f}"
                h = f"{s.get('signal_high', 0.0):.2f}"
                dr = f"{s.get('daily_rsi', 0.0):.1f}"
                wr = f"{s.get('weekly_rsi', 0.0):.1f}"
                rng = f"{s.get('candle_range', 0.0):.1f}%"
                print(f"{sym:<15} | {strat:<14} | {c:<12} | {h:<12} | {dr:<10} | {wr:<10} | {rng:<8}")
        print("=" * 120)
        
    finally:
        db.close()

if __name__ == "__main__":
    scan_today()
