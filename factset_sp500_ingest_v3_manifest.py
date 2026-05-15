"""
FactSet S&P 500 Earnings Season Update Ingestion Pipeline

Workflow:
1. Input a year/month window, e.g. 2026-04.
2. Generate all Friday URLs in that month.
3. Scan each URL and save a source manifest JSON.
4. Ingest only successful URLs from the manifest.
5. Save raw HTML, clean text, and article body into one JSON file per article.

Example:
    python factset_sp500_ingest_v3_manifest.py --year 2026 --month 4

Optional:
    python factset_sp500_ingest_v3_manifest.py --year 2026 --month 4 --output-dir factset_output
    python factset_sp500_ingest_v3_manifest.py --year 2026 --month 4 --scan-only
    python factset_sp500_ingest_v3_manifest.py --manifest factset_output/source_manifest_2026_04.json
"""

import argparse
import calendar
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# -----------------------------
# Date / URL helpers
# -----------------------------

def get_previous_friday(input_date: date) -> date:
    """
    Return the closest Friday on or before input_date.

    Example:
        Sunday 2026-02-01 -> Friday 2026-01-30
        Friday 2026-04-03 -> Friday 2026-04-03
    """
    days_since_friday = (input_date.weekday() - 4) % 7
    return input_date - timedelta(days=days_since_friday)


def get_fridays_in_month(year: int, month: int) -> list[date]:
    """Return all Fridays inside a given calendar month."""
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")

    _, last_day = calendar.monthrange(year, month)

    fridays = []
    for day in range(1, last_day + 1):
        current_date = date(year, month, day)
        if current_date.weekday() == 4:  # Monday=0, Friday=4
            fridays.append(current_date)

    return fridays


def build_factset_url_from_date(input_date: date) -> dict[str, Any]:
    """
    Build FactSet S&P 500 Earnings Season Update URL from a date.

    If input_date is not Friday, it maps to the previous Friday.
    For month-window scanning, input_date will already be a Friday.
    """
    target_date = get_previous_friday(input_date)

    month = MONTHS[target_date.month - 1]
    day = target_date.day
    year = target_date.year

    url = (
        "https://insight.factset.com/"
        f"sp-500-earnings-season-update-{month}-{day}-{year}"
    )

    return {
        "input_date": input_date.isoformat(),
        "target_friday": target_date.isoformat(),
        "month": month,
        "day": day,
        "year": year,
        "url": url,
    }


# -----------------------------
# Fetch / parse helpers
# -----------------------------

def fetch_factset_page(url: str, timeout: int = 20) -> dict[str, Any]:
    """Fetch one FactSet page and return response metadata plus HTML when available."""
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)

    return {
        "status_code": response.status_code,
        "url": url,
        "final_url": response.url,
        "html": response.text if response.status_code == 200 else None,
    }


def extract_clean_text(html: str) -> str:
    """Extract cleaned page text from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    return "\n".join(lines)


def extract_article_body(clean_text: str) -> str:
    """
    Extract article body from cleaned text.

    This version avoids using a hard-coded opening sentence because FactSet's
    weekly article intro can change. It mainly removes the legal disclaimer and
    anything after it when that marker exists.
    """
    if not clean_text:
        return ""

    end_markers = [
        "This blog post is for informational purposes only.",
        "This blog post is for informational purposes only",
    ]

    end_idx_candidates = [clean_text.find(marker) for marker in end_markers]
    end_idx_candidates = [idx for idx in end_idx_candidates if idx != -1]

    if end_idx_candidates:
        return clean_text[:min(end_idx_candidates)].strip()

    return clean_text.strip()


# -----------------------------
# Manifest helpers
# -----------------------------

def scan_factset_month(year: int, month: int) -> list[dict[str, Any]]:
    """
    Scan all Friday URLs in a month.

    Returns all attempts, including successes, 404s, unexpected status codes,
    and request exceptions. This is more useful than only returning successful
    URLs because it gives you a full audit trail.
    """
    fridays = get_fridays_in_month(year, month)
    all_attempts = []

    for friday in fridays:
        url_info = build_factset_url_from_date(friday)
        print(f"Checking: {url_info['target_friday']} | {url_info['url']}")

        attempt = {
            "input_date": url_info["input_date"],
            "target_friday": url_info["target_friday"],
            "url": url_info["url"],
            "final_url": None,
            "status_code": None,
            "success": False,
            "error": None,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }

        try:
            page_data = fetch_factset_page(url_info["url"])
            status = page_data["status_code"]

            attempt.update({
                "final_url": page_data["final_url"],
                "status_code": status,
                "success": status == 200,
            })

            if status == 200:
                print("  Success")
            elif status == 404:
                print("  Not found")
            else:
                print(f"  Unexpected status: {status}")

        except requests.RequestException as exc:
            attempt["error"] = str(exc)
            print(f"  Request failed: {exc}")

        all_attempts.append(attempt)

    return all_attempts


def save_source_manifest(
    year: int,
    month: int,
    all_attempts: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Save source_manifest_YYYY_MM.json with successful and failed URL attempts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    successful_urls = [attempt for attempt in all_attempts if attempt.get("success")]
    failed_urls = [attempt for attempt in all_attempts if not attempt.get("success")]

    manifest = {
        "source": "FactSet",
        "dataset": "sp500_earnings_season_update",
        "year": year,
        "month": month,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "friday_count": len(all_attempts),
        "successful_count": len(successful_urls),
        "failed_count": len(failed_urls),
        "successful_urls": successful_urls,
        "failed_urls": failed_urls,
        "all_attempts": all_attempts,
    }

    output_path = output_dir / f"source_manifest_{year}_{month:02d}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return output_path


def build_source_manifest_for_month(year: int, month: int, output_dir: Path) -> Path:
    """Convenience function: scan a month and immediately save its manifest."""
    all_attempts = scan_factset_month(year, month)
    manifest_path = save_source_manifest(
        year=year,
        month=month,
        all_attempts=all_attempts,
        output_dir=output_dir,
    )
    print(f"\nSaved source manifest: {manifest_path}")
    return manifest_path


# -----------------------------
# Raw JSON saving / ingestion
# -----------------------------

def save_raw_json(
    url_info: dict[str, Any],
    page_data: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Save one article's raw HTML, clean text, and article body into JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)

    html = page_data.get("html")
    clean_text = extract_clean_text(html) if html else None
    article_body = extract_article_body(clean_text) if clean_text else None

    output = {
        "source": "FactSet",
        "dataset": "sp500_earnings_season_update",
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
        "input_date": url_info["input_date"],
        "target_friday": url_info["target_friday"],
        "url": url_info["url"],
        "final_url": page_data.get("final_url"),
        "status_code": page_data.get("status_code"),
        "html": html,
        "text": clean_text,
        "article_body": article_body,
    }

    filename = f"factset_sp500_earnings_update_{url_info['target_friday']}.json"
    output_path = output_dir / filename

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output_path


def ingest_from_source_manifest(manifest_path: Path, output_dir: Path) -> list[Path]:
    """
    Read a source manifest and ingest only successful URLs.

    This avoids manually selecting dates and avoids ingesting known 404 URLs.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    saved_files = []
    successful_urls = manifest.get("successful_urls", [])

    if not successful_urls:
        print("No successful URLs found in manifest. Nothing to ingest.")
        return saved_files

    for item in successful_urls:
        target_friday = date.fromisoformat(item["target_friday"])
        url_info = build_factset_url_from_date(target_friday)

        print(f"Ingesting: {item['target_friday']} | {item['url']}")

        try:
            page_data = fetch_factset_page(item["url"])
        except requests.RequestException as exc:
            print(f"  Request failed during ingest: {exc}")
            continue

        if page_data["status_code"] == 200:
            output_path = save_raw_json(
                url_info=url_info,
                page_data=page_data,
                output_dir=output_dir,
            )
            saved_files.append(output_path)
            print(f"  Saved: {output_path}")
        else:
            print(f"  Skip changed status {page_data['status_code']}: {item['url']}")

    return saved_files


def ingest_factset_month(year: int, month: int, output_dir: Path) -> list[Path]:
    """
    Full one-command pipeline:
    1. Build source manifest for the month.
    2. Ingest successful URLs from the manifest.
    """
    manifest_path = build_source_manifest_for_month(year, month, output_dir)
    return ingest_from_source_manifest(manifest_path, output_dir)


# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan and ingest FactSet S&P 500 Earnings Season Update pages."
    )

    parser.add_argument("--year", type=int, help="Year to scan, e.g. 2026")
    parser.add_argument("--month", type=int, help="Month to scan, 1-12")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "factset_output",
        help="Directory for source manifest and raw article JSON files.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Existing source manifest path. If provided, ingest from this manifest instead of scanning a new month.",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan URLs and save source manifest. Do not ingest article JSON files.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.manifest:
        saved_files = ingest_from_source_manifest(
            manifest_path=args.manifest,
            output_dir=args.output_dir,
        )
        print("\nSaved raw files:")
        for path in saved_files:
            print(path)
        return

    if args.year is None or args.month is None:
        raise ValueError("Please provide --year and --month, or provide --manifest.")

    manifest_path = build_source_manifest_for_month(
        year=args.year,
        month=args.month,
        output_dir=args.output_dir,
    )

    if args.scan_only:
        return

    saved_files = ingest_from_source_manifest(
        manifest_path=manifest_path,
        output_dir=args.output_dir,
    )

    print("\nSaved raw files:")
    for path in saved_files:
        print(path)


if __name__ == "__main__":
    main()
