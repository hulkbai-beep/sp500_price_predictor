import sqlite3
import json
from pathlib import Path
from datetime import datetime


DB_PATH = Path("data") / "stock_price_predictor.db"
RAW_JSON_DIR = Path("factset_output")


def connect_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    return conn


def create_factset_article_raw_table(conn: sqlite3.Connection) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS factset_article_raw (
        report_date TEXT PRIMARY KEY,
        input_date TEXT,
        source TEXT,
        dataset TEXT,
        url TEXT,
        final_url TEXT,
        status_code INTEGER,
        ingested_at TEXT,

        html TEXT,
        clean_text TEXT,
        article_body TEXT,

        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """
    conn.execute(sql)
    conn.commit()


def load_factset_json_file(json_path: Path) -> dict:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_factset_article_raw(conn: sqlite3.Connection, record: dict) -> None:
    report_date = record.get("target_friday")

    if not report_date:
        raise ValueError("Missing target_friday in JSON record.")

    sql = """
    INSERT INTO factset_article_raw (
        report_date,
        input_date,
        source,
        dataset,
        url,
        final_url,
        status_code,
        ingested_at,
        html,
        clean_text,
        article_body,
        created_at,
        updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(report_date) DO UPDATE SET
        input_date = excluded.input_date,
        source = excluded.source,
        dataset = excluded.dataset,
        url = excluded.url,
        final_url = excluded.final_url,
        status_code = excluded.status_code,
        ingested_at = excluded.ingested_at,
        html = excluded.html,
        clean_text = excluded.clean_text,
        article_body = excluded.article_body,
        updated_at = excluded.updated_at;
    """

    now = datetime.now().isoformat(timespec="seconds")

    values = (
        report_date,
        record.get("input_date"),
        record.get("source"),
        record.get("dataset"),
        record.get("url"),
        record.get("final_url"),
        record.get("status_code"),
        record.get("ingested_at"),
        record.get("html"),
        record.get("text"),
        record.get("article_body"),
        now,
        now,
    )

    conn.execute(sql, values)


def ingest_factset_raw_json_dir(
    json_dir: Path = RAW_JSON_DIR,
    db_path: Path = DB_PATH,
) -> None:
    conn = connect_db(db_path)

    try:
        create_factset_article_raw_table(conn)

        json_files = sorted(json_dir.glob("factset_sp500_earnings_update_*.json"))

        if not json_files:
            print(f"No raw JSON files found in: {json_dir}")
            return

        inserted_count = 0

        for json_path in json_files:
            record = load_factset_json_file(json_path)
            upsert_factset_article_raw(conn, record)
            inserted_count += 1
            print(f"Upserted: {json_path.name}")

        conn.commit()

        print(f"\nDone. Upserted {inserted_count} records into {db_path}")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    ingest_factset_raw_json_dir()