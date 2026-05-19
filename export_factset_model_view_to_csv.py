import argparse
import csv
import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = Path("data") / "stock_price_predictor.db"
DEFAULT_OUTPUT_PATH = Path("data") / "factset_sp500_model_features.csv"
DEFAULT_VIEW_NAME = "factset_sp500_model_features"


def export_view_to_csv(
    db_path: Path = DEFAULT_DB_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    view_name: str = DEFAULT_VIEW_NAME,
) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)

    try:
        cursor = conn.execute(f"""
            SELECT *
            FROM {view_name}
            ORDER BY report_date;
        """)

        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)

        print(f"Exported {len(rows)} rows from {view_name}")
        print(f"CSV saved to: {output_path}")

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export SQLite view to CSV."
    )

    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database."
    )

    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Path to output CSV file."
    )

    parser.add_argument(
        "--view-name",
        type=str,
        default=DEFAULT_VIEW_NAME,
        help="SQLite view or table name to export."
    )

    args = parser.parse_args()

    export_view_to_csv(
        db_path=args.db_path,
        output_path=args.output_path,
        view_name=args.view_name,
    )


if __name__ == "__main__":
    main()