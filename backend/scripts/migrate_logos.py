import os
import sys
import re
from sqlalchemy import text

# Add backend directory to sys.path
sys.path.append(os.path.abspath(os.path.dirname(__file__) + '/..'))

from database import SessionLocal

def main():
    db = SessionLocal()
    
    try:
        # 1. Add img_url column to DB if it doesn't exist
        print("Checking if img_url column exists in companies table...")
        try:
            # Postgres specific:
            db.execute(text("ALTER TABLE companies ADD COLUMN img_url VARCHAR(255);"))
            db.commit()
            print("Successfully added img_url column.")
        except Exception as e:
            db.rollback()
            print("Column might already exist, or error occurred:", e)

        # 2. Parse Company.js
        js_path = os.path.join(os.path.dirname(__file__), '..', 'Company.js')
        print(f"Parsing {js_path} for logos...")
        
        with open(js_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Extract objects using regex (this is simple since the format is consistent)
        # Looking for:
        # share_symbol: "RELIANCE",
        # ...
        # image_url: "https://dhanarthi-stockguru.s3.amazonaws.com/company_logos_webp/RELIANCE.webp",
        
        pattern = r'share_symbol:\s*"([^"]+)",.*?image_url:\s*"([^"]+)"'
        # DOTALL is needed because there are lines between share_symbol and image_url
        matches = re.findall(pattern, content, re.DOTALL)
        
        print(f"Found {len(matches)} logo mappings in Company.js")
        
        # 3. Update companies table
        print("Updating companies in database...")
        update_query = text("UPDATE companies SET img_url = :img_url WHERE trading_symbol = :symbol")
        
        updated_count = 0
        for symbol, img_url in set(matches): # use set to avoid duplicates
            result = db.execute(update_query, {"img_url": img_url, "symbol": symbol})
            updated_count += result.rowcount
            
        db.commit()
        print(f"Successfully updated {updated_count} companies with image URLs.")
        
    except Exception as e:
        db.rollback()
        print(f"Error during migration: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    main()
