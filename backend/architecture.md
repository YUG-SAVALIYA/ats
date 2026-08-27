# ATS System Architecture & Target Directory Structure

The ATS (Automated Trading System) is a high-frequency, resilient quantitative swing trading and algorithmic execution engine integrated with Dhan HQ API v2.

---

## Directory Architecture

```text
ATS/
│
├── backend/
│   │
│   ├── api/
│   │   ├── auth.py              # Master password setup & JWT security endpoints
│   │   ├── accounts.py          # Broker Dhan authentication & token renewal
│   │   ├── signals.py           # Signal listing & evaluation endpoints
│   │   ├── trades.py            # Manual trade controls, order queries, event logs
│   │   ├── portfolio.py         # Broker funds, holdings, positions & search endpoints
│   │   └── settings.py          # Dynamic strategy & monthly RSI parameter endpoints
│   │
│   ├── trading/
│   │   ├── strategies.py        # Technical indicators (RSI, Supertrend, ROC) & Strategy rules
│   │   ├── signals.py           # Signal generation, reference price, 3:25 PM qualification
│   │   ├── orders.py            # Order placement orchestration (Two-Phase Exit Claim, Fills)
│   │   ├── trades.py            # Trade state machine (ALLOWED_TRANSITIONS) & Cache manager
│   │   ├── risk.py              # Trailing stop-loss 6-stage math & position sizing
│   │   └── trade_manager.py     # TradeEngine: WebSocket tick processing & trade execution
│   │
│   ├── dhan/
│   │   ├── client.py            # DhanClient facade (TradeAccount & DataAccount singletons)
│   │   ├── auth.py              # DhanAuthManager (TOTP generation, RenewToken, Fernet AES)
│   │   ├── orders.py            # Direct Dhan v2 Orders API client
│   │   ├── portfolio.py         # Direct Dhan v2 Funds, Holdings, Positions, Trades API
│   │   ├── market.py            # Direct Dhan v2 Marketfeed OHLC/LTP & Historical Charts API
│   │   └── websocket.py         # Dhan WebSocket feed v2 client & reconnection loop
│   │
│   ├── market/
│   │   ├── candles.py           # Daily candle sync (5-day self-healing, full sync, batch sync)
│   │   ├── weekly.py            # PostgreSQL native weekly candle aggregation & filters
│   │   ├── monthly.py           # Monthly candle aggregation & queries
│   │   └── calendar.py          # Indian market trading days & holiday schedule
│   │
│   ├── database/
│   │   ├── database.py          # PostgreSQL engine, sessionmaker, Base, get_db dependency
│   │   ├── models.py            # SQLAlchemy models, enums, table schemas
│   │   └── repositories.py      # Authoritative database query & persistence helpers
│   │
│   ├── workers/
│   │   ├── scheduler.py         # APScheduler cron jobs (3:20, 3:25, 3:40, 17:00, 22:00, Dec 31)
│   │   ├── market_monitor.py    # Background tick health & cache consistency auditor loops
│   │   └── reconciler.py        # 5s pending fill reconciler & 30s 3-way broker reconciler
│   │
│   ├── tests/                   # Automated unit test suites
│   ├── docs/                    # Architecture documentation
│   ├── scripts/                 # Utility scripts
│   ├── data/                    # CSV datasets
│   ├── config.py                # Dynamic configuration & Fernet AES credential loader
│   └── main.py                  # FastAPI application entry point & lifespan
│
├── frontend/
│   └── src/
│       ├── pages/               # React page components (Terminal, Signals, Logs, LockScreen)
│       ├── components/          # Reusable UI widgets, stats cards, tables, badges
│       ├── services/            # API client layer (api.ts)
│       ├── hooks/               # Custom React hooks (useEngineStatus)
│       └── types/               # TypeScript interfaces
```

---

## Scheduled Operations (Indian Financial Market Time)

1. **15:20 IST**: Pre-3:25 PM Fast Candle Sync for actionable securities.
2. **15:25 IST**: 3:25 PM Entry condition evaluation (+3% breakout trigger) and Supertrend RED exit check.
3. **15:40 IST**: Post-Market Candle Sync for all active stocks.
4. **17:00 IST (5:00 PM)**: Post-Market Signal Scan across all 2,980+ stocks.
5. **22:00 IST**: Full nightly reconciliation candle sync.
6. **Dec 31 23:50 IST**: Annual holiday schedule update.
