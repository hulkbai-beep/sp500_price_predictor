import argparse
import subprocess
import sqlite3
from pathlib import Path


DB_PATH = Path("data") / "stock_price_predictor.db"
RAW_JSON_DIR = Path("factset_output")


def run_command(command: list[str]) -> None:
    print("\nRunning:")
    print(" ".join(command))

    result = subprocess.run(command, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(command)}")


def preview_model_features(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)

    try:
        rows = conn.execute("""
            SELECT
                report_date,
                fiscal_quarter,
                sp500_reported_pct,
                eps_beat_pct,
                eps_surprise_pct,
                earnings_growth_current_pct,
                revenue_growth_current_pct,
                forward_12m_pe,
                cy_eps_growth_est_pct,
                extraction_status
            FROM factset_sp500_model_features
            ORDER BY report_date;
        """).fetchall()

        print("\nPreview: factset_sp500_model_features")
        print(
            "report_date | fiscal_quarter | reported_pct | eps_beat_pct | "
            "eps_surprise_pct | earnings_growth | revenue_growth | forward_pe | "
            "cy_eps_growth | status"
        )

        for row in rows:
            print(row)

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run FactSet S&P 500 earnings ingestion and feature pipeline."
    )

    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)

    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="SQLite database path."
    )

    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip FactSet URL scan/download step."
    )

    parser.add_argument(
        "--skip-raw-ingest",
        action="store_true",
        help="Skip raw JSON to SQLite ingest step."
    )

    parser.add_argument(
        "--skip-feature-extract",
        action="store_true",
        help="Skip fixed schema feature extraction step."
    )

    parser.add_argument(
        "--skip-view",
        action="store_true",
        help="Skip creating model view."
    )

    args = parser.parse_args()

    if not args.skip_download:
        run_command([
            "python",
            "factset_sp500_ingest_v3_manifest.py",
            "--year",
            str(args.year),
            "--month",
            str(args.month),
        ])

    if not args.skip_raw_ingest:
        run_command([
            "python",
            "sqlite_ingest_factset_raw.py",
        ])

    if not args.skip_feature_extract:
        run_command([
            "python",
            "factset_extract_fixed_schema.py",
            "--source",
            "db",
            "--db-path",
            str(args.db_path),
        ])

    if not args.skip_view:
        run_command([
            "python",
            "create_factset_model_view.py",
        ])

    preview_model_features(args.db_path)

    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()