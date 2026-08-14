import os
import sys
import csv
import logging
import psycopg2
from psycopg2.extras import execute_values

# Ensure backend directory is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import load_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ats.import_data")

def get_db_connection():
    cfg = load_config()
    db_url = cfg.database_url.strip()
    if db_url.startswith("postgresql+psycopg2://"):
        db_url = db_url.replace("postgresql+psycopg2://", "postgresql://")
    return psycopg2.connect(db_url)

def import_csv(conn, file_path, table_name, columns):
    if not os.path.exists(file_path):
        logger.warning(f"File {file_path} not found. Skipping.")
        return
        
    logger.info(f"Loading {file_path} into memory...")
    
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        data = list(reader)
        
    if not data:
        return
        
    logger.info(f"Processing {len(data)} rows for {table_name}...")
    
    # Clean and pad data
    for i in range(len(data)):
        row = data[i]
        
        # Pad row to match columns length
        if len(row) < len(columns):
            row.extend([None] * (len(columns) - len(row)))
        elif len(row) > len(columns):
            row = row[:len(columns)]
            
        # Replace empty strings with None (except for non-nullable string columns)
        for j in range(len(row)):
            if row[j] == "":
                if table_name == "companies" and j == 1: # dhan_security_id
                    row[j] = ""
                elif table_name == "companies" and j == 4: # exchange
                    row[j] = "NSE"
                else:
                    row[j] = None
                
        data[i] = row
    
    col_str = ", ".join(columns)
    
    # Using ON CONFLICT DO NOTHING to ensure existing data is not overwritten or corrupted
    query = f"""
        INSERT INTO {table_name} ({col_str}) 
        VALUES %s 
        ON CONFLICT (id) DO NOTHING
    """
    
    cursor = conn.cursor()
    batch_size = 10000
    
    logger.info(f"Starting database insert for {table_name} in batches of {batch_size}...")
    for i in range(0, len(data), batch_size):
        batch = data[i:i+batch_size]
        execute_values(cursor, query, batch)
        conn.commit()
        if (i + len(batch)) % 50000 == 0 or (i + len(batch)) == len(data):
            logger.info(f"[{table_name}] Inserted {i + len(batch)}/{len(data)} rows...")
        
    cursor.close()
    logger.info(f"✅ Successfully imported {table_name}!\n")

def main():
    logger.info("Connecting to database...")
    conn = get_db_connection()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    
    # 1. Market Holidays
    holiday_file = os.path.join(data_dir, "holiday.csv")
    holiday_cols = ["id", "date", "description", "created_at"]
    import_csv(conn, holiday_file, "market_holidays", holiday_cols)
    
    # 2. Companies
    stocks_file = os.path.join(data_dir, "stocks.csv")
    stocks_cols = [
        "id", "dhan_security_id", "trading_symbol", "company_name", "exchange", 
        "isin", "segment", "market_cap", "is_active", "created_at", "updated_at", 
        "img_url", "is_mtf", "mtf_leverage"
    ]
    import_csv(conn, stocks_file, "companies", stocks_cols)
    
    # 3. Weekly Candles
    weekly_file = os.path.join(data_dir, "weekly.csv")
    weekly_cols = [
        "id", "company_id", "week_start_date", "week_end_date", "open", "high", 
        "low", "close", "volume", "trading_days", "created_at", "updated_at"
    ]
    import_csv(conn, weekly_file, "weekly_candles", weekly_cols)
    
    # 4. Daily Candles
    daily_file = os.path.join(data_dir, "daily.csv")
    daily_cols = [
        "id", "company_id", "date", "open", "high", "low", "close", "volume", "created_at"
    ]
    import_csv(conn, daily_file, "daily_candles", daily_cols)
    
    conn.close()
    logger.info("🎉 All data imported successfully!")

if __name__ == "__main__":
    main()
