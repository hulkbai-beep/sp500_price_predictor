import sqlite3
from pathlib import Path

db_path = Path("data") / "stock_price_predictor.db"

conn = sqlite3.connect(db_path)

rows = conn.execute("""
    SELECT
        report_date,
        source,
        dataset,
        status_code,
        LENGTH(article_body) AS article_body_length
    FROM factset_article_raw
    ORDER BY report_date;
""").fetchall()

for row in rows:
    print(row)
    
    
    
row_open = conn.execute("""
   SELECT
    report_date,
    SUBSTR(article_body, 1, 300) AS article_start
    FROM factset_article_raw
    ORDER BY report_date;
""").fetchall()

for row in row_open:
    print(row)
    
row_end = conn.execute("""
   SELECT
    report_date,
    SUBSTR(article_body, LENGTH(article_body) - 300, 300) AS article_end
    FROM factset_article_raw
    ORDER BY report_date;
""").fetchall()

for row in row_end:
    print(row)

conn.close()