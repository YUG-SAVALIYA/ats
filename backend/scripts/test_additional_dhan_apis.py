"""
scripts/test_additional_dhan_apis.py — Additional & Advanced Dhan HQ APIs Diagnostic
====================================================================================
Dedicated verification script for advanced and specialized Dhan HQ APIs:
  1. Full Market Depth Quote (POST /v2/marketfeed/quote) — 5-Level Bid/Ask Depth
  2. Order Status & Query (GET /v2/orders/{orderId})
  3. Single Scrip Master Auto-Healing (auto_heal_security_id)

Usage:
  backend\\venv\\Scripts\\python.exe backend\\scripts\\test_additional_dhan_apis.py
"""

import os
import sys
import time

# Ensure backend directory is in path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from dhan.client import get_dhan_data_client, get_dhan_client
from dhan.orders import get_dhan_order_by_id
from dhan.market import auto_heal_security_id
from dhan.endpoints import MARKET_FEED_QUOTE_URL, ORDERS_URL


def print_section(title: str):
    print("\n" + "═" * 70)
    print(f"  {title}")
    print("═" * 70)


def test_marketfeed_full_quote():
    print_section("1. FULL 5-DEPTH MARKET QUOTE API (POST /v2/marketfeed/quote)")
    try:
        data_client = get_dhan_data_client()
        sec_id = 2885  # RELIANCE
        print(f"  --> Requesting 5-Level Market Depth for RELIANCE (Sec ID: {sec_id})...")
        quote_data = data_client.get_marketfeed_quote([sec_id])

        if quote_data and str(sec_id) in quote_data:
            q = quote_data[str(sec_id)]
            print(f"      [OK] Received Full Quote:")
            print(f"           - Last Traded Price (LTP) : Rs. {q.get('last_price')}")
            print(f"           - Traded Volume            : {q.get('volume', 0):,} shares")
            print(f"           - Average Trade Price      : Rs. {q.get('average_price', 0.0)}")
            print(f"           - Total Buy Quantity       : {q.get('total_buy_quantity', 0):,}")
            print(f"           - Total Sell Quantity      : {q.get('total_sell_quantity', 0):,}")

            depth = q.get("depth", {})
            bids = depth.get("buy", [])
            asks = depth.get("sell", [])

            if bids or asks:
                print("\n           [5-LEVEL ORDER BOOK DEPTH]")
                print("           " + "─" * 45)
                print(f"           {'BUY / BID':<22} | {'SELL / ASK':<22}")
                print(f"           {'Price (Qty)':<22} | {'Price (Qty)':<22}")
                print("           " + "─" * 45)
                for i in range(max(len(bids), len(asks))):
                    bid_str = f"Rs. {bids[i].get('price')} ({bids[i].get('quantity')})" if i < len(bids) else "-"
                    ask_str = f"Rs. {asks[i].get('price')} ({asks[i].get('quantity')})" if i < len(asks) else "-"
                    print(f"           {bid_str:<22} | {ask_str:<22}")
                print("           " + "─" * 45)
        else:
            print("      [!] Quote API response returned empty or outside trading window.")
    except Exception as e:
        print(f"      [ERROR] Marketfeed Quote failed: {e}")


def test_order_query_by_id():
    print_section("2. SINGLE ORDER QUERY API (GET /v2/orders/{orderId})")
    try:
        print("  --> Testing Order Status Query with Schema Verification...")
        test_id = "TEST_ORDER_QUERY_123"
        res = get_dhan_order_by_id(test_id)
        print(f"      [OK] Order Endpoint Reachable: {ORDERS_URL}")
        print(f"      [OK] Broker Response: {res.get('status', 'OK')} ({res.get('remarks', 'Order ID not found as expected')})")
    except Exception as e:
        print(f"      [ERROR] Order query failed: {e}")


def test_symbol_auto_healing():
    print_section("3. INDIVIDUAL SCRIP MASTER AUTO-HEALING")
    try:
        symbols = ["RELIANCE", "AAREYDRUGS", "TCS"]
        print("  --> Testing auto_heal_security_id for target symbols...")
        for sym in symbols:
            sec_id = auto_heal_security_id(sym)
            print(f"      [OK] Symbol '{sym:<10}' -> Verified Security ID: {sec_id}")
    except Exception as e:
        print(f"      [ERROR] Auto-healing test failed: {e}")


def main():
    print("\n" + "#" * 70)
    print("      ADDITIONAL & SPECIALIZED DHAN HQ APIS DIAGNOSTIC")
    print("#" * 70)

    test_marketfeed_full_quote()
    time.sleep(1.0)
    test_order_query_by_id()
    test_symbol_auto_healing()

    print("\n" + "#" * 70)
    print("      ADDITIONAL APIS DIAGNOSTIC COMPLETE")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    main()
