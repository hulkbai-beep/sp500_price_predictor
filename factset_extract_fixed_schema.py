"""
Extract fixed-schema FactSet S&P 500 Earnings Season Update features.

This script reads the raw JSON files created by factset_sp500_ingest_v3_manifest.py
and/or the raw SQLite table created by sqlite_ingest_factset_raw.py, extracts a
stable set of numeric features, and upserts them into:

    factset_sp500_earnings_features

Typical usage:
    python factset_extract_fixed_schema.py --json-dir factset_output --db-path data/stock_price_predictor.db

Validate only, no DB write:
    python factset_extract_fixed_schema.py --json-dir factset_output --dry-run

Export extracted rows to CSV:
    python factset_extract_fixed_schema.py --json-dir factset_output --csv-out data/factset_features_preview.csv

Read raw articles from an existing SQLite DB instead of JSON:
    python factset_extract_fixed_schema.py --source db --db-path data/stock_price_predictor.db

Notes:
- Extraction is regex-based and intentionally tolerant. Missing fields become NULL.
- Keep factset_article_raw as the audit/source-of-truth table.
- Re-run this script whenever you improve extraction logic; rows are upserted by report_date.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path("data") / "stock_price_predictor.db"
DEFAULT_JSON_DIR = Path("factset_output")
FEATURE_TABLE = "factset_sp500_earnings_features"


# -----------------------------
# Text helpers
# -----------------------------

def normalize_text(text: str | None) -> str:
    """Collapse whitespace so regex patterns can work across line breaks."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def pct_to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.replace(",", "").strip())
    except ValueError:
        return None


def first_match(text: str, pattern: str, flags: int = re.IGNORECASE) -> re.Match[str] | None:
    return re.search(pattern, text, flags)


def safe_group(match: re.Match[str] | None, idx: int) -> str | None:
    if not match:
        return None
    try:
        return match.group(idx)
    except IndexError:
        return None


def derived_spread(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 4)


# -----------------------------
# Load raw records
# -----------------------------

def load_json_records(json_dir: Path) -> list[dict[str, Any]]:
    json_files = sorted(json_dir.glob("factset_sp500_earnings_update_*.json"))
    records: list[dict[str, Any]] = []

    for json_path in json_files:
        with open(json_path, "r", encoding="utf-8") as f:
            record = json.load(f)
        record["_source_file"] = str(json_path)
        records.append(record)

    return records


def load_db_raw_records(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(
            """
            SELECT
                report_date,
                input_date,
                source,
                dataset,
                url,
                final_url,
                status_code,
                ingested_at,
                article_body,
                clean_text
            FROM factset_article_raw
            ORDER BY report_date
            """
        ).fetchall()
    finally:
        conn.close()

    records = []
    for row in rows:
        rec = dict(row)
        rec["target_friday"] = rec.get("report_date")
        rec["text"] = rec.get("clean_text")
        records.append(rec)

    return records


# -----------------------------
# Feature extraction
# -----------------------------

def extract_forward_estimates(text: str) -> dict[str, Any]:
    """
    Extract future quarter earnings growth estimates.

    Handles two common FactSet phrasings:
      1) For Q1 2026 and Q2 2026, analysts are calling for earnings growth rates of 11.2% and 14.5%, respectively.
      2) For Q2 2026 through Q4 2026, analysts are calling for earnings growth rates of 10.2%, 11.5%, and 8.7%, respectively.
    """
    out: dict[str, Any] = {
        "next_q1_label": None,
        "next_q1_eps_growth_est_pct": None,
        "next_q2_label": None,
        "next_q2_eps_growth_est_pct": None,
        "next_q3_label": None,
        "next_q3_eps_growth_est_pct": None,
        "cy_eps_growth_est_year": None,
        "cy_eps_growth_est_pct": None,
    }

    # Three-quarter form: Q2 through Q4
    m3 = first_match(
        text,
        r"For\s+(Q[1-4]\s+\d{4})\s+through\s+(Q[1-4]\s+\d{4}),\s+analysts\s+are\s+calling\s+for\s+earnings\s+growth\s+rates\s+of\s+(-?\d+(?:\.\d+)?)%,\s+(-?\d+(?:\.\d+)?)%,\s+and\s+(-?\d+(?:\.\d+)?)%,\s+respectively",
    )
    if m3:
        start_label = m3.group(1)
        end_label = m3.group(2)
        labels = expand_quarter_range(start_label, end_label)
        vals = [pct_to_float(m3.group(3)), pct_to_float(m3.group(4)), pct_to_float(m3.group(5))]
        for i, (label, val) in enumerate(zip(labels[:3], vals), start=1):
            out[f"next_q{i}_label"] = label
            out[f"next_q{i}_eps_growth_est_pct"] = val

    # Two-quarter form: Q1 and Q2
    if out["next_q1_label"] is None:
        m2 = first_match(
            text,
            r"For\s+(Q[1-4]\s+\d{4})\s+and\s+(Q[1-4]\s+\d{4}),\s+analysts\s+are\s+calling\s+for\s+earnings\s+growth\s+rates\s+of\s+(-?\d+(?:\.\d+)?)%\s+and\s+(-?\d+(?:\.\d+)?)%,\s+respectively",
        )
        if m2:
            out["next_q1_label"] = m2.group(1)
            out["next_q1_eps_growth_est_pct"] = pct_to_float(m2.group(3))
            out["next_q2_label"] = m2.group(2)
            out["next_q2_eps_growth_est_pct"] = pct_to_float(m2.group(4))

    cy = first_match(
        text,
        r"For\s+CY\s+(\d{4}),?\s+analysts\s+are\s+(?:projecting|predicting)\s+\(year-over-year\)\s+earnings\s+growth\s+of\s+(-?\d+(?:\.\d+)?)%",
    )
    if cy:
        out["cy_eps_growth_est_year"] = int_or_none(cy.group(1))
        out["cy_eps_growth_est_pct"] = pct_to_float(cy.group(2))

    return out


def expand_quarter_range(start_label: str, end_label: str) -> list[str]:
    """Expand Q2 2026 through Q4 2026 into [Q2 2026, Q3 2026, Q4 2026]."""
    start_q, start_y = parse_quarter_label(start_label)
    end_q, end_y = parse_quarter_label(end_label)

    labels = []
    q, y = start_q, start_y
    while True:
        labels.append(f"Q{q} {y}")
        if q == end_q and y == end_y:
            break
        q += 1
        if q == 5:
            q = 1
            y += 1
        if len(labels) > 12:
            break

    return labels


def parse_quarter_label(label: str) -> tuple[int, int]:
    m = re.match(r"Q([1-4])\s+(\d{4})", label.strip(), re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid quarter label: {label}")
    return int(m.group(1)), int(m.group(2))


def extract_features(record: dict[str, Any]) -> dict[str, Any]:
    text = normalize_text(record.get("article_body") or record.get("text") or "")

    report_date = record.get("target_friday") or record.get("report_date") or record.get("input_date")
    errors: list[str] = []

    row: dict[str, Any] = {
        "report_date": report_date,
        "fiscal_quarter": None,

        "sp500_reported_pct": None,

        "eps_beat_pct": None,
        "eps_beat_5y_avg_pct": None,
        "eps_beat_10y_avg_pct": None,
        "eps_surprise_pct": None,
        "eps_surprise_5y_avg_pct": None,
        "eps_surprise_10y_avg_pct": None,
        "eps_beat_vs_5y_spread": None,
        "eps_surprise_vs_5y_spread": None,

        "earnings_growth_current_pct": None,
        "earnings_growth_last_week_pct": None,
        "earnings_growth_qtr_end_pct": None,
        "earnings_growth_wow_change": None,
        "earnings_growth_since_qtr_end_change": None,

        "revenue_beat_pct": None,
        "revenue_beat_5y_avg_pct": None,
        "revenue_beat_10y_avg_pct": None,
        "revenue_surprise_pct": None,
        "revenue_surprise_5y_avg_pct": None,
        "revenue_surprise_10y_avg_pct": None,
        "revenue_beat_vs_5y_spread": None,
        "revenue_surprise_vs_5y_spread": None,

        "revenue_growth_current_pct": None,
        "revenue_growth_last_week_pct": None,
        "revenue_growth_qtr_end_pct": None,
        "revenue_growth_wow_change": None,
        "revenue_growth_since_qtr_end_change": None,

        "next_q1_label": None,
        "next_q1_eps_growth_est_pct": None,
        "next_q2_label": None,
        "next_q2_eps_growth_est_pct": None,
        "next_q3_label": None,
        "next_q3_eps_growth_est_pct": None,

        "cy_eps_growth_est_year": None,
        "cy_eps_growth_est_pct": None,

        "forward_12m_pe": None,
        "forward_12m_pe_5y_avg": None,
        "forward_12m_pe_10y_avg": None,
        "forward_pe_qtr_end": None,
        "forward_pe_vs_5y_avg_spread": None,
        "forward_pe_vs_10y_avg_spread": None,
        "forward_pe_vs_qtr_end_change": None,

        "next_week_sp500_reports_count": None,
        "next_week_dow30_reports_count": None,

        "extraction_status": "ok",
        "extraction_error": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    if not report_date:
        errors.append("missing report_date")

    if not text:
        errors.append("missing article_body/text")
        row["extraction_status"] = "failed"
        row["extraction_error"] = "; ".join(errors)
        return row

    # Overall reported % and fiscal quarter.
    m = first_match(
        text,
        r"Overall,\s+(-?\d+(?:\.\d+)?)%\s+of\s+the\s+companies\s+in\s+the\s+S&P\s+500\s+have\s+reported\s+actual\s+results\s+for\s+(Q[1-4]\s+\d{4})\s+to\s+date",
    )
    if m:
        row["sp500_reported_pct"] = pct_to_float(m.group(1))
        row["fiscal_quarter"] = m.group(2)
    else:
        errors.append("overall reported pct / fiscal quarter not found")

    # EPS beat rate.
    m = first_match(
        text,
        r"Of\s+these\s+companies,\s+(-?\d+(?:\.\d+)?)%\s+have\s+reported\s+actual\s+EPS\s+above\s+estimates.*?5-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%.*?10-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%",
    )
    if m:
        row["eps_beat_pct"] = pct_to_float(m.group(1))
        row["eps_beat_5y_avg_pct"] = pct_to_float(m.group(2))
        row["eps_beat_10y_avg_pct"] = pct_to_float(m.group(3))
    else:
        errors.append("eps beat rates not found")

    # EPS surprise magnitude.
    m = first_match(
        text,
        r"In\s+aggregate,\s+companies\s+are\s+reporting\s+earnings\s+that\s+are\s+(-?\d+(?:\.\d+)?)%\s+above\s+estimates.*?5-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%.*?10-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%",
    )
    if m:
        row["eps_surprise_pct"] = pct_to_float(m.group(1))
        row["eps_surprise_5y_avg_pct"] = pct_to_float(m.group(2))
        row["eps_surprise_10y_avg_pct"] = pct_to_float(m.group(3))
    else:
        errors.append("eps surprise rates not found")

    # Earnings growth current / last week / quarter end.
    m = first_match(
        text,
        r"blended\s+\(combines\s+actual\s+results.*?\)\s+earnings\s+growth\s+rate\s+for\s+the\s+.*?\s+is\s+(-?\d+(?:\.\d+)?)%\s+today,\s+compared\s+to\s+an\s+earnings\s+growth\s+rate\s+of\s+(-?\d+(?:\.\d+)?)%\s+last\s+week\s+and\s+an\s+earnings\s+growth\s+rate\s+of\s+(-?\d+(?:\.\d+)?)%\s+at\s+the\s+end\s+of\s+the",
    )
    if m:
        row["earnings_growth_current_pct"] = pct_to_float(m.group(1))
        row["earnings_growth_last_week_pct"] = pct_to_float(m.group(2))
        row["earnings_growth_qtr_end_pct"] = pct_to_float(m.group(3))
    else:
        errors.append("earnings growth triplet not found")

    # Revenue beat rate.
    m = first_match(
        text,
        r"In\s+terms\s+of\s+revenues,\s+(-?\d+(?:\.\d+)?)%\s+of\s+S&P\s+500\s+companies\s+have\s+reported\s+actual\s+revenues\s+above\s+estimates.*?5-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%.*?10-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%",
    )
    if m:
        row["revenue_beat_pct"] = pct_to_float(m.group(1))
        row["revenue_beat_5y_avg_pct"] = pct_to_float(m.group(2))
        row["revenue_beat_10y_avg_pct"] = pct_to_float(m.group(3))
    else:
        errors.append("revenue beat rates not found")

    # Revenue surprise magnitude.
    m = first_match(
        text,
        r"In\s+aggregate,\s+companies\s+are\s+reporting\s+revenues\s+that\s+are\s+(-?\d+(?:\.\d+)?)%\s+above\s+(?:the\s+)?estimates.*?5-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%.*?10-year\s+average\s+of\s+(-?\d+(?:\.\d+)?)%",
    )
    if m:
        row["revenue_surprise_pct"] = pct_to_float(m.group(1))
        row["revenue_surprise_5y_avg_pct"] = pct_to_float(m.group(2))
        row["revenue_surprise_10y_avg_pct"] = pct_to_float(m.group(3))
    else:
        errors.append("revenue surprise rates not found")

    # Revenue growth current / last week / quarter end.
    m = first_match(
        text,
        r"blended\s+revenue\s+growth\s+rate\s+for\s+the\s+.*?\s+is\s+(-?\d+(?:\.\d+)?)%\s+today,\s+compared\s+to\s+a\s+revenue\s+growth\s+rate\s+of\s+(-?\d+(?:\.\d+)?)%\s+last\s+week\s+and\s+a\s+revenue\s+growth\s+rate\s+of\s+(-?\d+(?:\.\d+)?)%\s+at\s+the\s+end\s+of\s+the",
    )
    if m:
        row["revenue_growth_current_pct"] = pct_to_float(m.group(1))
        row["revenue_growth_last_week_pct"] = pct_to_float(m.group(2))
        row["revenue_growth_qtr_end_pct"] = pct_to_float(m.group(3))
    else:
        errors.append("revenue growth triplet not found")

    # Forward estimates.
    row.update(extract_forward_estimates(text))
    if row["cy_eps_growth_est_pct"] is None:
        errors.append("forward earnings estimates not found")

    # Forward 12-month P/E.
    m = first_match(
        text,
        r"The\s+forward\s+12-month\s+P/E\s+ratio\s+is\s+(-?\d+(?:\.\d+)?)(?:\s+\([^)]*\))?,\s+which\s+is\s+.*?5-year\s+average\s+\((-?\d+(?:\.\d+)?)\)\s+and\s+.*?10-year\s+average\s+\((-?\d+(?:\.\d+)?)\).*?forward\s+P/E\s+ratio\s+of\s+(-?\d+(?:\.\d+)?)\s+recorded\s+at\s+the\s+end",
    )
    if m:
        row["forward_12m_pe"] = pct_to_float(m.group(1))
        row["forward_12m_pe_5y_avg"] = pct_to_float(m.group(2))
        row["forward_12m_pe_10y_avg"] = pct_to_float(m.group(3))
        row["forward_pe_qtr_end"] = pct_to_float(m.group(4))
    else:
        errors.append("forward pe not found")

    # Upcoming reports.
    m = first_match(
        text,
        r"During\s+the\s+upcoming\s+week,\s+([\d,]+)\s+S&P\s+500\s+companies\s+\(including\s+([\d,]+)\s+Dow\s+30\s+components?\)\s+are\s+scheduled\s+to\s+report",
    )
    if m:
        row["next_week_sp500_reports_count"] = int_or_none(m.group(1))
        row["next_week_dow30_reports_count"] = int_or_none(m.group(2))
    else:
        errors.append("upcoming report counts not found")

    # Derived features.
    row["eps_beat_vs_5y_spread"] = derived_spread(row["eps_beat_pct"], row["eps_beat_5y_avg_pct"])
    row["eps_surprise_vs_5y_spread"] = derived_spread(row["eps_surprise_pct"], row["eps_surprise_5y_avg_pct"])

    row["earnings_growth_wow_change"] = derived_spread(
        row["earnings_growth_current_pct"], row["earnings_growth_last_week_pct"]
    )
    row["earnings_growth_since_qtr_end_change"] = derived_spread(
        row["earnings_growth_current_pct"], row["earnings_growth_qtr_end_pct"]
    )

    row["revenue_beat_vs_5y_spread"] = derived_spread(row["revenue_beat_pct"], row["revenue_beat_5y_avg_pct"])
    row["revenue_surprise_vs_5y_spread"] = derived_spread(
        row["revenue_surprise_pct"], row["revenue_surprise_5y_avg_pct"]
    )

    row["revenue_growth_wow_change"] = derived_spread(
        row["revenue_growth_current_pct"], row["revenue_growth_last_week_pct"]
    )
    row["revenue_growth_since_qtr_end_change"] = derived_spread(
        row["revenue_growth_current_pct"], row["revenue_growth_qtr_end_pct"]
    )

    row["forward_pe_vs_5y_avg_spread"] = derived_spread(row["forward_12m_pe"], row["forward_12m_pe_5y_avg"])
    row["forward_pe_vs_10y_avg_spread"] = derived_spread(row["forward_12m_pe"], row["forward_12m_pe_10y_avg"])
    row["forward_pe_vs_qtr_end_change"] = derived_spread(row["forward_12m_pe"], row["forward_pe_qtr_end"])

    # Mark partial instead of failed when most important fields exist but some optional fields are missing.
    critical_fields = [
        "sp500_reported_pct",
        "eps_beat_pct",
        "eps_surprise_pct",
        "earnings_growth_current_pct",
        "revenue_beat_pct",
        "revenue_surprise_pct",
        "revenue_growth_current_pct",
        "forward_12m_pe",
    ]
    missing_critical = [c for c in critical_fields if row.get(c) is None]

    if missing_critical:
        row["extraction_status"] = "partial"
        errors.append(f"missing critical fields: {', '.join(missing_critical)}")
    elif errors:
        row["extraction_status"] = "ok_with_warnings"
    else:
        row["extraction_status"] = "ok"

    row["extraction_error"] = "; ".join(errors) if errors else None
    return row


# -----------------------------
# SQLite output
# -----------------------------

def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    return conn


def create_feature_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {FEATURE_TABLE} (
            report_date TEXT PRIMARY KEY,
            fiscal_quarter TEXT,

            sp500_reported_pct REAL,

            eps_beat_pct REAL,
            eps_beat_5y_avg_pct REAL,
            eps_beat_10y_avg_pct REAL,
            eps_surprise_pct REAL,
            eps_surprise_5y_avg_pct REAL,
            eps_surprise_10y_avg_pct REAL,
            eps_beat_vs_5y_spread REAL,
            eps_surprise_vs_5y_spread REAL,

            earnings_growth_current_pct REAL,
            earnings_growth_last_week_pct REAL,
            earnings_growth_qtr_end_pct REAL,
            earnings_growth_wow_change REAL,
            earnings_growth_since_qtr_end_change REAL,

            revenue_beat_pct REAL,
            revenue_beat_5y_avg_pct REAL,
            revenue_beat_10y_avg_pct REAL,
            revenue_surprise_pct REAL,
            revenue_surprise_5y_avg_pct REAL,
            revenue_surprise_10y_avg_pct REAL,
            revenue_beat_vs_5y_spread REAL,
            revenue_surprise_vs_5y_spread REAL,

            revenue_growth_current_pct REAL,
            revenue_growth_last_week_pct REAL,
            revenue_growth_qtr_end_pct REAL,
            revenue_growth_wow_change REAL,
            revenue_growth_since_qtr_end_change REAL,

            next_q1_label TEXT,
            next_q1_eps_growth_est_pct REAL,
            next_q2_label TEXT,
            next_q2_eps_growth_est_pct REAL,
            next_q3_label TEXT,
            next_q3_eps_growth_est_pct REAL,

            cy_eps_growth_est_year INTEGER,
            cy_eps_growth_est_pct REAL,

            forward_12m_pe REAL,
            forward_12m_pe_5y_avg REAL,
            forward_12m_pe_10y_avg REAL,
            forward_pe_qtr_end REAL,
            forward_pe_vs_5y_avg_spread REAL,
            forward_pe_vs_10y_avg_spread REAL,
            forward_pe_vs_qtr_end_change REAL,

            next_week_sp500_reports_count INTEGER,
            next_week_dow30_reports_count INTEGER,

            extraction_status TEXT,
            extraction_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def upsert_feature_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    columns = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_sql = ", ".join(columns)

    update_cols = [c for c in columns if c not in {"report_date", "created_at"}]
    update_sql = ", ".join([f"{c} = excluded.{c}" for c in update_cols])

    sql = f"""
        INSERT INTO {FEATURE_TABLE} ({col_sql})
        VALUES ({placeholders})
        ON CONFLICT(report_date) DO UPDATE SET
            {update_sql};
    """

    values = [[row.get(col) for col in columns] for row in rows]
    conn.executemany(sql, values)
    conn.commit()


def write_csv(rows: list[dict[str, Any]], csv_path: Path) -> None:
    if not rows:
        return

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys())

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows: list[dict[str, Any]]) -> None:
    print(f"Extracted {len(rows)} rows.")
    if not rows:
        return

    status_counts: dict[str, int] = {}
    for row in rows:
        status = row.get("extraction_status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    print("Extraction status counts:")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")

    preview_cols = [
        "report_date",
        "fiscal_quarter",
        "sp500_reported_pct",
        "eps_beat_pct",
        "eps_surprise_pct",
        "earnings_growth_current_pct",
        "revenue_growth_current_pct",
        "forward_12m_pe",
        "cy_eps_growth_est_pct",
        "extraction_status",
    ]
    print("\nPreview:")
    print("\t".join(preview_cols))
    for row in rows:
        print("\t".join(str(row.get(c, "")) for c in preview_cols))


# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract fixed-schema features from FactSet S&P 500 earnings season raw records."
    )
    parser.add_argument(
        "--source",
        choices=["json", "db"],
        default="json",
        help="Read raw records from JSON files or from SQLite factset_article_raw table. Default: json.",
    )
    parser.add_argument(
        "--json-dir",
        type=Path,
        default=DEFAULT_JSON_DIR,
        help="Directory containing factset_sp500_earnings_update_*.json files.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite DB path. Used for DB source and feature table output.",
    )
    parser.add_argument(
        "--csv-out",
        type=Path,
        default=None,
        help="Optional CSV output path for extracted features.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and print summary, but do not write to SQLite.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.source == "json":
        records = load_json_records(args.json_dir)
    else:
        records = load_db_raw_records(args.db_path)

    if not records:
        raise SystemExit("No raw records found.")

    rows = [extract_features(record) for record in records]
    rows = sorted(rows, key=lambda r: r.get("report_date") or "")

    print_summary(rows)

    if args.csv_out:
        write_csv(rows, args.csv_out)
        print(f"\nWrote CSV: {args.csv_out}")

    if args.dry_run:
        print("\nDry run only. No SQLite table was written.")
        return

    conn = connect_db(args.db_path)
    try:
        create_feature_table(conn)
        upsert_feature_rows(conn, rows)
    finally:
        conn.close()

    print(f"\nUpserted {len(rows)} rows into {args.db_path}::{FEATURE_TABLE}")


if __name__ == "__main__":
    main()
