import os
import sys
import uuid
import pandas as pd
from datetime import datetime

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from database import SessionLocal
from models import Company, DailyCandle

def main():
    db = SessionLocal()
    
    print("Loading companies from database...")
    companies = db.query(Company).all()
    # Map dhan_security_id -> company_id
    company_map = {str(c.dhan_security_id): c.id for c in companies}
    print(f"Found {len(company_map)} companies in database.")
    
    csv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'daily_candles_export.csv'))
    print(f"Reading CSV from {csv_path}...")
    
    # Read the CSV
    df = pd.read_csv(csv_path)
    # Ensure security_id is string for matching
    df['security_id'] = df['security_id'].astype(str)
    
    print(f"Total rows in CSV: {len(df)}")
    
    unique_csv_securities = df['security_id'].unique()
    
    # Find matching and missing securities
    matched_securities = set()
    unmatched_securities = set()
    
    for sec_id in unique_csv_securities:
        if sec_id in company_map:
            matched_securities.add(sec_id)
        else:
            unmatched_securities.add(sec_id)
            
    print(f"Matched securities: {len(matched_securities)}")
    print(f"Unmatched securities: {len(unmatched_securities)}")
    
    # Filter df to only matched securities
    valid_df = df[df['security_id'].isin(matched_securities)].copy()
    
    # Calculate stats
    candle_counts = valid_df.groupby('security_id').size().to_dict()
    
    # Find max candles
    if candle_counts:
        max_candles = max(candle_counts.values())
        min_candles = min(candle_counts.values())
        
        # Find companies with fewer candles
        companies_with_low_candles = {sec_id: count for sec_id, count in candle_counts.items() if count < max_candles}
    else:
        max_candles = 0
        min_candles = 0
        companies_with_low_candles = {}
        
    # Generate Markdown Report
    report_path = r'C:\Users\Yug\.gemini\antigravity-ide\brain\4b5a4bbf-8ba5-4f42-abc0-8f6a0b4f54cc\candle_import_report.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# Daily Candle Import Report\n\n")
        f.write("## Overview\n")
        f.write(f"- **Total rows in CSV:** {len(df)}\n")
        f.write(f"- **Total active companies in DB:** {len(company_map)}\n")
        f.write(f"- **Companies matched with CSV:** {len(matched_securities)}\n")
        f.write(f"- **Total valid candles to insert:** {len(valid_df)}\n\n")
        
        f.write("## Security ID Matching\n")
        f.write(f"There are **{len(unmatched_securities)}** security IDs in the CSV that do NOT exist in the database's company table.\n\n")
        if unmatched_securities:
            f.write("<details><summary>Click to view unmatched security IDs</summary>\n\n")
            f.write("```text\n")
            f.write(", ".join(list(unmatched_securities)[:100]))
            if len(unmatched_securities) > 100:
                f.write(f"\n... and {len(unmatched_securities) - 100} more.")
            f.write("\n```\n")
            f.write("</details>\n\n")
            
        f.write("## Candle Count Statistics\n")
        f.write(f"- **Maximum candles for a company:** {max_candles}\n")
        f.write(f"- **Minimum candles for a company:** {min_candles}\n\n")
        
        if max_candles == min_candles and len(matched_securities) > 0:
            f.write("✅ **All companies have the exact same number of candles.**\n\n")
        else:
            f.write(f"⚠️ **Not all companies have the same number of candles.**\n")
            f.write(f"There are **{len(companies_with_low_candles)}** companies with fewer than {max_candles} candles.\n\n")
            
            if companies_with_low_candles:
                f.write("<details><summary>Click to view companies with missing candles</summary>\n\n")
                f.write("| Security ID | Candle Count | Difference |\n")
                f.write("|-------------|--------------|------------|\n")
                
                # Try to map to trading symbol for better readability
                rev_company_map = {str(c.dhan_security_id): c.trading_symbol for c in companies}
                
                for sec_id, count in sorted(companies_with_low_candles.items(), key=lambda x: x[1]):
                    symbol = rev_company_map.get(sec_id, "Unknown")
                    diff = max_candles - count
                    f.write(f"| {sec_id} ({symbol}) | {count} | -{diff} |\n")
                
                f.write("\n</details>\n\n")
    
    print(f"Report written to {report_path}")
    
    print("Preparing bulk insert...")
    # Add company_id based on security_id
    valid_df['company_id'] = valid_df['security_id'].map(company_map)
    
    # We will use sqlalchemy core for faster bulk insert
    from sqlalchemy import insert
    
    print("Converting data...")
    records_to_insert = []
    
    # We need to drop duplicates from valid_df just in case
    # Not strictly necessary but safe
    
    for _, row in valid_df.iterrows():
        records_to_insert.append({
            'id': str(uuid.uuid4()),
            'company_id': row['company_id'],
            'date': datetime.strptime(row['candle_date'], "%Y-%m-%d").date(),
            'open': row['open'],
            'high': row['high'],
            'low': row['low'],
            'close': row['close'],
            'volume': row['volume'],
            'created_at': datetime.utcnow()
        })
        
    print(f"Inserting {len(records_to_insert)} records...")
    
    # Insert in chunks of 10000 to avoid memory issues
    chunk_size = 10000
    try:
        # Clear existing daily candles to prevent duplication (optional)
        print("Clearing existing DailyCandle records to prevent duplicates...")
        db.query(DailyCandle).delete()
        db.commit()
        
        for i in range(0, len(records_to_insert), chunk_size):
            chunk = records_to_insert[i:i + chunk_size]
            db.execute(insert(DailyCandle).values(chunk))
            db.commit()
            print(f"Inserted chunk {i//chunk_size + 1}/{(len(records_to_insert)//chunk_size)+1}")
            
        print("Bulk insert completed successfully!")
    except Exception as e:
        db.rollback()
        print(f"Error during insert: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
