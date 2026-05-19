import sqlite3
from pathlib import Path

DB_PATH = Path("data") / "stock_price_predictor.db"


def create_model_view(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)

    try:
        conn.execute("DROP VIEW IF EXISTS factset_sp500_model_features;")

        conn.execute("""
        CREATE VIEW factset_sp500_model_features AS
        SELECT
            report_date,
            fiscal_quarter,
            sp500_reported_pct,

            eps_beat_pct,
            eps_surprise_pct,
            earnings_growth_current_pct,
            earnings_growth_wow_change,
            earnings_growth_since_qtr_end_change,

            revenue_beat_pct,
            revenue_surprise_pct,
            revenue_growth_current_pct,
            revenue_growth_wow_change,
            revenue_growth_since_qtr_end_change,

            forward_12m_pe,
            forward_pe_vs_5y_avg_spread,
            forward_pe_vs_10y_avg_spread,
            forward_pe_vs_qtr_end_change,

            cy_eps_growth_est_pct,

            extraction_status
        FROM factset_sp500_earnings_features
        WHERE extraction_status = 'ok';
        """)

        conn.commit()
        print("Created view: factset_sp500_model_features")

    finally:
        conn.close()


if __name__ == "__main__":
    create_model_view()