import os
from sqlalchemy import create_engine, inspect, text

db_url = "postgresql://postgresycxnkpdxpnbwwurguket:Zentrade007@173.249.55.168:5433/ats"

try:
    engine = create_engine(db_url, connect_args={"connect_timeout": 10})
    insp = inspect(engine)
    tables = insp.get_table_names()
    print(f"Connected successfully! Total tables found: {len(tables)}")
    with engine.connect() as conn:
        for t in sorted(tables):
            try:
                count = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                print(f"  {t}: {count} rows")
            except Exception as e:
                print(f"  {t}: error counting ({e})")
except Exception as e:
    print(f"Connection error: {e}")
