"""
Database Flush Utility:
Clears all table records in the target database EXCEPT the 'portfolio' table.
Uses CASCADE to respect foreign key constraints.
"""
import sys
from sqlalchemy import create_engine, inspect, text

EXCLUDE_TABLES = {"portfolio"}

def flush_database(db_url: str):
    print(f"Connecting to database: {db_url.split('@')[-1]} ...")
    engine = create_engine(db_url, connect_args={"connect_timeout": 10})
    
    insp = inspect(engine)
    all_tables = insp.get_table_names()
    
    target_tables = [t for t in all_tables if t.lower() not in EXCLUDE_TABLES]
    excluded_found = [t for t in all_tables if t.lower() in EXCLUDE_TABLES]
    
    print(f"\nTotal tables found in DB: {len(all_tables)}")
    print(f"Tables to be PRESERVED: {excluded_found}")
    print(f"Tables to be FLUSHED ({len(target_tables)}): {target_tables}")
    
    if not target_tables:
        print("No tables to flush.")
        return

    with engine.connect() as conn:
        print("\nFlushing tables...")
        # Disable foreign key triggers or use TRUNCATE ... CASCADE
        table_list_sql = ", ".join(f'"{t}"' for t in target_tables)
        try:
            conn.execute(text(f"TRUNCATE TABLE {table_list_sql} CASCADE;"))
            conn.commit()
            print("TRUNCATE ... CASCADE executed successfully.")
        except Exception as e:
            print(f"TRUNCATE failed ({e}), falling back to individual DELETEs...")
            conn.rollback()
            for t in target_tables:
                try:
                    conn.execute(text(f'DELETE FROM "{t}";'))
                    conn.commit()
                    print(f"  Deleted rows from '{t}'")
                except Exception as del_err:
                    print(f"  Error deleting '{t}': {del_err}")
                    conn.rollback()
                    
        # Verification
        print("\n--- Post-Flush Verification ---")
        for t in all_tables:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
            preserved_tag = " [PRESERVED]" if t.lower() in EXCLUDE_TABLES else ""
            print(f"  {t}: {count} row(s){preserved_tag}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_url = sys.argv[1]
    else:
        target_url = "postgresql://postgresycxnkpdxpnbwwurguket:Zentrade007@173.249.55.168:5433/ats"
    flush_database(target_url)
