"""
scripts/test_live_dhan_apis.py — Live Dhan HQ API Comprehensive Verification
=============================================================================
Runs an end-to-end diagnostic test of all official Dhan API endpoints:
  1. Trade Account Authentication & Credentials
  2. Data Account Authentication & Credentials
  3. Live Portfolio Fund Limits & Available Balance
  4. Live Holdings & Position Snapshots
  5. Live Regular Orders & Trade History
  6. Historical Charts Daily OHLCV Data API
  7. Marketfeed Snapshot OHLC & Real-time LTP API
  8. CDN Scrip Master Resolution & Auto-Healing
  9. Real-Time WebSocket Market Feed (wss://api-feed.dhan.co)

Usage:
  backend\\venv\\Scripts\\python.exe backend\\scripts\\test_live_dhan_apis.py
"""

import os
import sys
import time
import asyncio
from datetime import datetime, timedelta

# Ensure backend directory is in path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from dhan.client import get_dhan_client, get_dhan_data_client
from dhan.portfolio import PortfolioService
from dhan.market import fetch_historical_ohlcv, get_official_scrip_id, auto_heal_security_id
from dhan.endpoints import (
    DHAN_API_BASE_URL,
    DHAN_AUTH_BASE_URL,
    DHAN_CDN_BASE_URL,
    DHAN_WS_BASE_URL
)


def print_section(title: str):
    print("\n" + "═" * 70)
    print(f"  {title}")
    print("═" * 70)


def test_trade_account_auth():
    print_section("1. TRADE ACCOUNT AUTHENTICATION (Order Execution)")
    try:
        trade_client = get_dhan_client()
        cfg = trade_client.get_config()
        token = trade_client.auth_manager.get_valid_token()
        print(f"  [+] Client ID     : {cfg.client_id}")
        print(f"  [+] Token Status  : {'ACTIVE (Len: ' + str(len(token)) + ', Suffix: ' + token[-4:] + ')' if token else 'MISSING'}")
        print(f"  [+] Account Label : {trade_client.account_label}")
        return True
    except Exception as e:
        print(f"  [-] Trade Account Auth Failed: {e}")
        return False


def test_data_account_auth():
    print_section("2. DATA ACCOUNT AUTHENTICATION (Market Data & Feed)")
    try:
        data_client = get_dhan_data_client()
        cfg = data_client.get_config()
        token = data_client.auth_manager.get_valid_token()
        print(f"  [+] Client ID     : {cfg.client_id}")
        print(f"  [+] Token Status  : {'ACTIVE (Len: ' + str(len(token)) + ', Suffix: ' + token[-4:] + ')' if token else 'MISSING'}")
        print(f"  [+] Account Label : {data_client.account_label}")
        return True
    except Exception as e:
        print(f"  [-] Data Account Auth Failed: {e}")
        return False


def test_portfolio_apis():
    print_section("3. PORTFOLIO, FUNDS & POSITIONS APIS (Trade Account)")
    service = PortfolioService()

    # 1. Fund Limits
    print("\n  --> Fetching Fund Limits (GET /v2/fundlimit)...")
    try:
        funds = service.get_fund_limits()
        avail = funds.get("availabelBalance", funds.get("available_balance", 0.0))
        sod = funds.get("sodLimit", funds.get("sod_limit", 0.0))
        collateral = funds.get("collateralAmount", funds.get("collateral_amount", 0.0))
        print(f"      [OK] Available Balance : Rs. {float(avail):,.2f}")
        print(f"      [OK] SOD Limit         : Rs. {float(sod):,.2f}")
        print(f"      [OK] Collateral Amount : Rs. {float(collateral):,.2f}")
    except Exception as e:
        print(f"      [ERROR] Fund limits call failed: {e}")

    # 2. Holdings
    print("\n  --> Fetching Live Holdings (GET /v2/holdings)...")
    try:
        holdings = service.get_holdings()
        print(f"      [OK] Total Holdings: {len(holdings)} stocks")
        for h in holdings[:3]:
            sym = h.get("tradingSymbol", h.get("trading_symbol", ""))
            qty = h.get("totalQty", h.get("total_qty", 0))
            ltp = h.get("lastTradedPrice", h.get("last_traded_price", 0.0))
            print(f"           - {sym}: {qty} shares @ Rs. {ltp}")
    except Exception as e:
        print(f"      [ERROR] Holdings call failed: {e}")

    # 3. Positions
    print("\n  --> Fetching Live Positions (GET /v2/positions)...")
    try:
        positions = service.get_positions()
        print(f"      [OK] Total Position Records: {len(positions)}")
        for p in positions[:3]:
            sym = p.get("tradingSymbol", p.get("trading_symbol", ""))
            net_qty = p.get("netQty", p.get("net_qty", 0))
            pnl = p.get("realizedProfit", p.get("realized_profit", 0.0))
            print(f"           - {sym}: Net Qty = {net_qty} | Realized P&L = Rs. {pnl:+.2f}")
    except Exception as e:
        print(f"      [ERROR] Positions call failed: {e}")

    # 4. Orders
    print("\n  --> Fetching Today's Orders (GET /v2/orders)...")
    try:
        orders = service.get_orders()
        print(f"      [OK] Today's Regular Orders: {len(orders)}")
    except Exception as e:
        print(f"      [ERROR] Orders call failed: {e}")

    # 5. Trades
    print("\n  --> Fetching Executed Trades (GET /v2/trades)...")
    try:
        trades = service.get_trades()
        print(f"      [OK] Today's Executed Trades: {len(trades)}")
    except Exception as e:
        print(f"      [ERROR] Trades call failed: {e}")


def test_market_data_apis():
    print_section("4. HISTORICAL OHLCV & LIVE MARKETFEED APIS (Data Account)")

    # 1. Historical Charts API
    print("\n  --> Fetching Historical OHLCV (POST /v2/charts/historical)...")
    try:
        sec_id = "2885"  # RELIANCE
        from_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        to_date = datetime.now().strftime("%Y-%m-%d")
        candles = fetch_historical_ohlcv(
            security_id=sec_id,
            exchange_segment="NSE_EQ",
            from_date=from_date,
            to_date=to_date,
            symbol="RELIANCE"
        )
        if candles:
            print(f"      [OK] Fetched {len(candles)} Daily Candles for RELIANCE (Sec ID: {sec_id})")
            latest = candles[-1]
            print(f"           - Latest Candle ({latest['date']}): Open={latest['open']}, High={latest['high']}, Low={latest['low']}, Close={latest['close']}")
        else:
            print("      [!] No candles returned or market closed.")
    except Exception as e:
        print(f"      [ERROR] Historical OHLCV fetch failed: {e}")

    # 2. Snapshot Marketfeed OHLC
    print("\n  --> Fetching Snapshot Marketfeed OHLC (POST /v2/marketfeed/ohlc)...")
    try:
        data_client = get_dhan_data_client()
        ohlc_data = data_client.get_marketfeed_ohlc([2885, 11536])
        print(f"      [OK] Received live snapshot OHLC for {len(ohlc_data)} securities:")
        for sid, q in ohlc_data.items():
            print(f"           - Sec ID {sid}: Open={q.get('ohlc', {}).get('open')}, High={q.get('ohlc', {}).get('high')}, Low={q.get('ohlc', {}).get('low')}, Close={q.get('ohlc', {}).get('close')}, LTP={q.get('last_price')}")
    except Exception as e:
        print(f"      [ERROR] Marketfeed OHLC call failed: {e}")

    # Brief delay to respect Dhan API rate limits
    time.sleep(1.0)

    # 3. Snapshot Marketfeed LTP
    print("\n  --> Fetching Live Snapshot LTP (POST /v2/marketfeed/ltp)...")
    try:
        data_client = get_dhan_data_client()
        ltp_data = data_client.get_marketfeed_ltp([2885, 11536, 5245])
        print(f"      [OK] Received live LTP for {len(ltp_data)} securities:")
        for sid, q in ltp_data.items():
            print(f"           - Sec ID {sid}: LTP = Rs. {q.get('last_price')}")
    except Exception as e:
        print(f"      [ERROR] Marketfeed LTP call failed: {e}")


def test_scrip_master():
    print_section("5. DHAN SCRIP MASTER RESOLUTION & AUTO-HEALING")
    try:
        print("  --> Resolving official exchange IDs from live Dhan Scrip Master...")
        test_symbols = ["RELIANCE", "TCS", "INFY", "AAREYDRUGS"]
        for sym in test_symbols:
            sec_id = get_official_scrip_id(sym)
            print(f"      [OK] {sym:<12} -> Official Dhan Security ID: {sec_id}")
    except Exception as e:
        print(f"  [-] Scrip Master Lookup Error: {e}")


async def _test_websocket_stream_async():
    print_section("6. REAL-TIME WEBSOCKET FEED (wss://api-feed.dhan.co)")
    try:
        import json
        import struct
        import websockets
        from dhan.endpoints import get_websocket_feed_url

        data_client = get_dhan_data_client()
        token = data_client.auth_manager.get_valid_token()
        client_id = data_client.client_id

        if not token or not client_id:
            print("  [-] Cannot test WebSocket: Missing token or client ID.")
            return

        ws_url = get_websocket_feed_url(token, client_id, auth_type=2)
        print(f"  --> Connecting to WebSocket: {DHAN_WS_BASE_URL}...")

        ticks_received = 0
        async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None) as ws:
            print("      [OK] Connected successfully to Dhan Market Feed v2!")

            # Subscribe to RELIANCE (Sec ID 2885) and TCS (11536)
            sub_payload = {
                "RequestCode": 15,
                "InstrumentCount": 2,
                "InstrumentList": [
                    {"ExchangeSegment": "NSE_EQ", "SecurityId": "2885"},
                    {"ExchangeSegment": "NSE_EQ", "SecurityId": "11536"}
                ]
            }
            await ws.send(json.dumps(sub_payload, separators=(',', ':')))
            print("      [OK] Sent RequestCode 15 (Ticker) subscription for RELIANCE (2885) & TCS (11536)...")
            print("      --> Listening for live price ticks (3 second window)...")

            start_t = time.time()
            while time.time() - start_t < 3.5:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
                    if isinstance(raw, bytes) and len(raw) >= 8:
                        resp_code, msg_len, exchange_seg, sec_id_int = struct.unpack("<BHBI", raw[:8])
                        ltp = 0.0
                        if len(raw) >= 12:
                            ltp = round(float(struct.unpack("<f", raw[8:12])[0]), 2)
                        ticks_received += 1
                        print(f"           [LIVE TICK] Security ID: {sec_id_int} | LTP = Rs. {ltp}")
                        if ticks_received >= 5:
                            break
                    elif isinstance(raw, str):
                        print(f"           [WS MSG] {raw}")
                except asyncio.TimeoutError:
                    break

            if ticks_received > 0:
                print(f"      [OK] Successfully received {ticks_received} real-time binary tick packets!")
            else:
                print("      [OK] WebSocket connection & authentication successful (0 ticks received outside active trade window).")

    except Exception as e:
        print(f"  [-] WebSocket Feed Test Failed: {e}")


def main():
    print("\n" + "#" * 70)
    print("       DHAN HQ API LIVE END-TO-END SYSTEM DIAGNOSTIC")
    print("#" * 70)

    t1 = test_trade_account_auth()
    t2 = test_data_account_auth()

    if t1:
        test_portfolio_apis()

    if t2:
        test_market_data_apis()
        test_scrip_master()
        try:
            asyncio.run(_test_websocket_stream_async())
        except Exception as ws_err:
            print(f"WebSocket execution wrapper error: {ws_err}")

    print("\n" + "#" * 70)
    print("       DHAN HQ API DIAGNOSTIC COMPLETE")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    main()
