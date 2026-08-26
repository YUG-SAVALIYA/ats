"""
app.data.weekly
===============
Aggregates daily candles into completed weekly candles using PostgreSQL native aggregation.
"""

import logging
from sqlalchemy import text
from app.data.database import SessionLocal

logger = logging.getLogger("ats.weekly")


def aggregate_weekly_candles(company_id=None):
    """
    Aggregates daily candles into weekly candles using PostgreSQL native aggregation.
    Optionally filters by company_id.
    """
    db = SessionLocal()
    try:
        company_filter = ""
        params = {}
        if company_id:
            company_filter = "AND dc.company_id = :company_id"
            params["company_id"] = company_id

        sql = f"""
        WITH weekly_data AS (
            SELECT 
                dc.company_id,
                date_trunc('week', dc.date)::date AS week_start_date,
                (date_trunc('week', dc.date) + INTERVAL '4 days')::date AS week_end_date,
                (array_agg(dc.open ORDER BY dc.date ASC))[1] AS open,
                MAX(dc.high) AS high,
                MIN(dc.low) AS low,
                (array_agg(dc.close ORDER BY dc.date DESC))[1] AS close,
                SUM(dc.volume) AS volume,
                COUNT(*) AS trading_days
            FROM daily_candles dc
            WHERE EXTRACT(ISODOW FROM dc.date) BETWEEN 1 AND 5
            {company_filter}
            GROUP BY dc.company_id, date_trunc('week', dc.date)
        )
        INSERT INTO weekly_candles (
            id, company_id, week_start_date, week_end_date, 
            open, high, low, close, volume, trading_days, created_at, updated_at
        )
        SELECT 
            gen_random_uuid()::varchar(36), 
            company_id, 
            week_start_date, 
            week_end_date,
            open, 
            high, 
            low, 
            close, 
            volume, 
            trading_days,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM weekly_data
        WHERE date_trunc('week', CURRENT_DATE)::date > week_start_date
        ON CONFLICT (company_id, week_start_date) DO UPDATE SET
            week_end_date = EXCLUDED.week_end_date,
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            trading_days = EXCLUDED.trading_days,
            updated_at = CURRENT_TIMESTAMP;
        """
        
        result = db.execute(text(sql), params)
        db.commit()
        
        affected_rows = result.rowcount
        logger.info(f"Successfully processed {affected_rows} weekly candles.")
        return {"status": "success", "processed_rows": affected_rows}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error in weekly candle aggregation: {e}")
        raise e
    finally:
        db.close()
