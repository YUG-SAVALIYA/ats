"""
Migration: Add missing columns to trades and other tables.
Safe to run multiple times -- uses ADD COLUMN IF NOT EXISTS.
"""
import os, sys

# Load DATABASE_URL from .env
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line.startswith('DATABASE_URL=') and not line.startswith('#'):
            db_url = line.split('=', 1)[1].strip().strip('"').strip("'")
            os.environ['DATABASE_URL'] = db_url
            break

from sqlalchemy import create_engine, text, inspect

engine = create_engine(os.environ['DATABASE_URL'], future=True)
insp = inspect(engine)

# --- Reload after adding columns ---
db_trades_cols = {c['name'] for c in insp.get_columns('trades')}
print("CURRENT trades DB columns:", sorted(db_trades_cols))

sys.path.insert(0, '.')
from app.data.models import Trade
model_cols = {c.key: c for c in Trade.__table__.columns}

missing = {name: col for name, col in model_cols.items() if name not in db_trades_cols}
print("MISSING from DB:", list(missing.keys()))

if missing:
    with engine.connect() as conn:
        for col_name, col in missing.items():
            col_type = col.type.compile(dialect=engine.dialect)
            sql = f'ALTER TABLE trades ADD COLUMN IF NOT EXISTS "{col_name}" {col_type}'
            print(f"Running: {sql}")
            conn.execute(text(sql))
            conn.commit()
            print(f"  Added: {col_name}")

# Verify
insp2 = inspect(engine)
db_trades_cols2 = {c['name'] for c in insp2.get_columns('trades')}
still_missing = set(model_cols.keys()) - db_trades_cols2
if still_missing:
    print("STILL MISSING:", still_missing)
else:
    print("OK - trades table fully migrated. All", len(db_trades_cols2), "columns present.")

# Check if strategy_name exists and needs renaming/removing
if 'strategy_name' in db_trades_cols2 and 'strategy_name' not in model_cols:
    print("NOTE: 'strategy_name' column exists in DB but not in model (legacy column, safe to ignore).")

# Also run init_db to create any missing tables (users, dhan_accounts, orders, etc.)
from app.data.models import Base
Base.metadata.create_all(engine)
print("init_db: create_all done (new tables created if missing).")
