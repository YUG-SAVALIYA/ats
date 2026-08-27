# ATS System Architecture & Automated Schedules

The ATS (Automated Trading System) is a high-frequency, resilient quantitative swing trading and algorithmic execution engine integrated with Dhan HQ API v2.

---

## Timed Schedules (Indian Standard Time — Mon to Fri)

1. **09:00 IST**: **Pre-Market Token Refresh & Scrip Master Sync**
   - Automatically generates fresh 18-hour access tokens via TOTP for all configured broker accounts.
   - Downloads Dhan's official live `api-scrip-master.csv` and auto-updates any changed Security IDs in the database.
2. **15:20 IST**: **Pre-3:25 PM Fast Candle Sync**
   - Pre-syncs recent candles for actionable securities before the entry window.
3. **15:25 IST**: **3:25 PM Entry & Supertrend Exit Window**
   - Evaluates +3% breakout entry conditions on `PENDING` signals and places `MARKET BUY` orders.
   - Evaluates open trades and triggers exit if daily Supertrend turned RED.
4. **15:46 IST (3:46 PM)**: **Post-Market Candle Sync**
   - Fetches closing daily candles for all active stocks right after market close settlement.
5. **17:00 IST (5:00 PM)**: **Post-Market Signal Scan**
   - Scans all 2,980+ stocks across multi-timeframe strategy rules to generate next-day signals.
6. **22:00 IST**: **Nightly Self-Healing Candle Sync**
   - Reconciles and repairs any data gaps.
7. **Dec 31 23:50 IST**: **Annual Holiday Calendar Update**
   - Fetches next year's market holidays.
