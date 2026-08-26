"""
Comprehensive Schema Migration:
Scans every SQLAlchemy model table in Base.metadata and adds any missing columns
to the live PostgreSQL database using ALTER TABLE ... ADD COLUMN IF NOT EXISTS.
"""
import os
import sys

# 1. Load DATABASE_URL from .env
with open('.env') as f:
    for line in f:
        line = line.strip()
        if line.startswith('DATABASE_URL=') and not line.startswith('#'):
            db_url = line.split('=', 1)[1].strip().strip('"').strip("'")
            os.environ['DATABASE_URL'] = db_url
            break

from sqlalchemy import create_engine, text, inspect

sys.path.insert(0, '.')
from app.data.models import Base

engine = create_engine(os.environ['DATABASE_URL'], future=True)
insp = inspect(engine)

existing_db_tables = set(insp.get_table_names())
print(f"Existing DB tables ({len(existing_db_tables)}): {sorted(existing_db_tables)}")

# First create any completely missing tables
Base.metadata.create_all(engine)
print("Base.metadata.create_all completed.")

# Now re-inspect to find any existing tables with missing columns
insp = inspect(engine)
total_added = 0

with engine.connect() as conn:
    for table_name, table in Base.metadata.tables.items():
        if table_name not in insp.get_table_names():
            continue
        
        db_cols = {c['name'] for c in insp.get_columns(table_name)}
        model_cols = {c.name: c for c in table.columns}
        
        missing_in_db = {name: col for name, col in model_cols.items() if name not in db_cols}
        
        if missing_in_db:
            print(f"\nTable '{table_name}' is missing {len(missing_in_db)} column(s): {list(missing_in_db.keys())}")
            for col_name, col in missing_in_db.items():
                col_type = col.type.compile(dialect=engine.dialect)
                nullable = "NULL" if col.nullable else "NOT NULL"
                default_clause = ""
                if col.default is not None and col.default.is_scalar:
                    default_clause = f" DEFAULT {repr(col.default.arg)}"
                elif col.nullable:
                    default_clause = " DEFAULT NULL"
                
                sql = f'ALTER TABLE "{table_name}" ADD COLUMN IF NOT EXISTS "{col_name}" {col_type}{default_clause}'
                print(f"  Executing: {sql}")
                try:
                    conn.execute(text(sql))
                    conn.commit()
                    print(f"  [OK] Added column '{col_name}' to '{table_name}'")
                    total_added += 1
                except Exception as e:
                    print(f"  [ERROR] Could not add '{col_name}': {e}")
                    conn.rollback()
        else:
            print(f"Table '{table_name}' ({len(db_cols)} cols) -> OK (In sync)")

print(f"\n==========================================")
print(f"Migration completed! Added {total_added} missing column(s).")
print(f"==========================================")
