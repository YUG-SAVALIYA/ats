import sys
import os
import csv
import logging
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values

# Ensure backend directory is in path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from config import load_config

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("ats.import_data")


def get_db_connection():
    cfg = load_config()
    db_url = cfg.database_url.strip()
    if db_url.startswith("postgresql+psycopg2://"):
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(db_url)


def clean_val(val, table_name, col_idx, total_cols):
    if val is None or val == "" or val == "\\N":
        if table_name == "companies" and col_idx == 1:  # dhan_security_id
            return ""
        if table_name == "companies" and col_idx == 4:  # exchange
            return "NSE"
        return None
    val = val.strip()
    if val == "":
        if table_name == "companies" and col_idx == 1:
            return ""
        if table_name == "companies" and col_idx == 4:
            return "NSE"
        return None
    # Boolean conversions
    if table_name == "companies" and col_idx in (8, 12):  # is_active, is_mtf
        if val in ("t", "true", "True", "1"):
            return True
        if val in ("f", "false", "False", "0"):
            return False
    return val


def import_csv_file(conn, file_path, table_name, columns, conflict_target="id"):
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} not found. Skipping.")
        return 0

    logger.info(f"Starting import of {file_path} into table '{table_name}'...")
    cursor = conn.cursor()
    col_str = ", ".join(columns)
    
    query = f"""
        INSERT INTO {table_name} ({col_str})
        VALUES %s
        ON CONFLICT ({conflict_target}) DO NOTHING
    """

    batch_size = 10000
    batch = []
    total_imported = 0
    total_rows = 0

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            total_rows += 1

            # Pad or truncate row to match columns
            if len(row) < len(columns):
                row.extend([None] * (len(columns) - len(row)))
            elif len(row) > len(columns):
                row = row[:len(columns)]

            cleaned_row = [clean_val(row[j], table_name, j, len(columns)) for j in range(len(columns))]
            batch.append(tuple(cleaned_row))

            if len(batch) >= batch_size:
                execute_values(cursor, query, batch, page_size=batch_size)
                conn.commit()
                total_imported += len(batch)
                logger.info(f"[{table_name}] Processed {total_imported} rows...")
                batch = []

        if batch:
            execute_values(cursor, query, batch, page_size=batch_size)
            conn.commit()
            total_imported += len(batch)
            logger.info(f"[{table_name}] Processed {total_imported} rows...")

    cursor.close()
    logger.info(f"✅ Successfully finished importing {table_name}: {total_rows} total rows processed.\n")
    return total_imported


def main():
    logger.info("Connecting to PostgreSQL database...")
    conn = get_db_connection()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

    # 1. Market Holidays (holiday.csv -> market_holidays)
    holiday_file = os.path.join(data_dir, "holiday.csv")
    holiday_cols = ["id", "date", "description", "created_at"]
    import_csv_file(conn, holiday_file, "market_holidays", holiday_cols, conflict_target="id")

    # 2. Companies (stocks.csv -> companies)
    stocks_file = os.path.join(data_dir, "stocks.csv")
    stocks_cols = [
        "id", "dhan_security_id", "trading_symbol", "company_name", "exchange",
        "isin", "segment", "market_cap", "is_active", "created_at", "updated_at",
        "img_url", "is_mtf", "mtf_leverage"
    ]
    import_csv_file(conn, stocks_file, "companies", stocks_cols, conflict_target="id")

    # 3. Weekly Candles (weekly.csv -> weekly_candles)
    weekly_file = os.path.join(data_dir, "weekly.csv")
    weekly_cols = [
        "id", "company_id", "week_start_date", "week_end_date", "open", "high",
        "low", "close", "volume", "trading_days", "created_at", "updated_at"
    ]
    import_csv_file(conn, weekly_file, "weekly_candles", weekly_cols, conflict_target="company_id, week_start_date")

    # 4. Daily Candles (daily.csv -> daily_candles)
    daily_file = os.path.join(data_dir, "daily.csv")
    daily_cols = [
        "id", "company_id", "date", "open", "high", "low", "close", "volume", "created_at"
    ]
    import_csv_file(conn, daily_file, "daily_candles", daily_cols, conflict_target="id")

    # Verify counts
    cursor = conn.cursor()
    logger.info("═══════════════════════════════════════════════════════")
    logger.info("FINAL DATABASE RECORD COUNTS:")
    for table in ["market_holidays", "companies", "weekly_candles", "daily_candles"]:
        cursor.execute(f"SELECT count(*) FROM {table};")
        count = cursor.fetchone()[0]
        logger.info(f"  - {table:20}: {count:,} rows")
    logger.info("═══════════════════════════════════════════════════════")
    cursor.close()
    conn.close()
    logger.info("🎉 All CSV data imported successfully into PostgreSQL!")


if __name__ == "__main__":
    main()
