"""
ATS Schema Migration Script
=============================
Run this script ONCE to:
1. Add new columns to the `trades` table (ATS execution fields).
2. Create the new `ats_orders` table.
3. Create the new `trade_events` table.

Usage::

    cd c:/Users/Yug/Desktop/ATS/backend
    python scripts/run_migration.py

Safe to re-run — uses IF NOT EXISTS / IF NOT EXISTS for all operations.
"""

import os
import sys

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import engine
from sqlalchemy import text

DDL_STATEMENTS = [
    # ── 1. Add new columns to `trades` table ─────────────────────────────
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS ats_state VARCHAR(32) DEFAULT 'OPEN';",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS security_id VARCHAR(64);",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS remaining_quantity INTEGER;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS target1_price FLOAT;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS target2_price FLOAT;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS stop_price FLOAT;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS breakeven_price FLOAT;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS partial_exit_completed BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS gap_detected BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS gap_pct FLOAT;",

    # ── 2. Backfill ats_state for existing open trades ───────────────────
    """
    UPDATE trades
    SET ats_state = 'OPEN'
    WHERE ats_state IS NULL
      AND trade_status = 'OPEN';
    """,
    """
    UPDATE trades
    SET ats_state = 'CLOSED'
    WHERE ats_state IS NULL
      AND trade_status IN ('CLOSED', 'EXIT_PENDING', 'EXIT_IN_PROGRESS');
    """,

    # ── 3. Create `ats_orders` table ─────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS ats_orders (
        id              VARCHAR(36)  PRIMARY KEY,
        trade_id        VARCHAR(36)  NOT NULL REFERENCES trades(id),
        dhan_order_id   VARCHAR(64),
        correlation_id  VARCHAR(64),
        order_purpose   VARCHAR(32)  NOT NULL DEFAULT 'ENTRY',
        transaction_type VARCHAR(8)  NOT NULL DEFAULT 'BUY',
        security_id     VARCHAR(64)  NOT NULL,
        quantity        INTEGER      NOT NULL,
        price           FLOAT,
        order_type      VARCHAR(16)  NOT NULL DEFAULT 'LIMIT',
        product_type    VARCHAR(16)  NOT NULL DEFAULT 'MTF',
        exchange_segment VARCHAR(16) NOT NULL DEFAULT 'NSE_EQ',
        status          VARCHAR(32)  NOT NULL DEFAULT 'PENDING',
        fill_price      FLOAT,
        fill_qty        INTEGER      NOT NULL DEFAULT 0,
        error_count     INTEGER      NOT NULL DEFAULT 0,
        last_error      TEXT,
        placed_at       TIMESTAMPTZ,
        filled_at       TIMESTAMPTZ,
        created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """,

    # Indexes for ats_orders
    "CREATE INDEX IF NOT EXISTS idx_ats_orders_trade_id ON ats_orders(trade_id);",
    "CREATE INDEX IF NOT EXISTS idx_ats_orders_dhan_order_id ON ats_orders(dhan_order_id);",
    "CREATE INDEX IF NOT EXISTS idx_ats_orders_status ON ats_orders(status);",

    # ── 4. Create `trade_events` table ───────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trade_events (
        id          SERIAL       PRIMARY KEY,
        trade_id    VARCHAR(36)  NOT NULL REFERENCES trades(id),
        event_type  VARCHAR(64)  NOT NULL,
        detail      TEXT,
        price       FLOAT,
        quantity    INTEGER,
        created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );
    """,

    # Indexes for trade_events
    "CREATE INDEX IF NOT EXISTS idx_trade_events_trade_id ON trade_events(trade_id);",
    "CREATE INDEX IF NOT EXISTS idx_trade_events_event_type ON trade_events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_trade_events_created_at ON trade_events(created_at);",
]


def run_migration():
    print("=" * 60)
    print("ATS Schema Migration")
    print("=" * 60)
    
    with engine.connect() as conn:
        for i, stmt in enumerate(DDL_STATEMENTS, 1):
            stmt = stmt.strip()
            if not stmt:
                continue
            preview = stmt[:80].replace("\n", " ")
            print(f"[{i:02d}] {preview}...")
            try:
                conn.execute(text(stmt))
                conn.commit()
                print(f"     OK")
            except Exception as exc:
                print(f"     Notice: {exc}")
                conn.rollback()

    print()
    print("=" * 60)
    print("Migration complete.")
    print("=" * 60)


if __name__ == "__main__":
    run_migration()
