import calendar
from pathlib import Path
from datetime import datetime, date, timedelta
import requests
import json
from bs4 import BeautifulSoup

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december"
]


def get_previous_friday(input_date: date) -> date:
    days_since_friday = (input_date.weekday() - 4) % 7
    return input_date - timedelta(days=days_since_friday)


def build_factset_url_from_date(input_date: date) -> dict:
    target_date = get_previous_friday(input_date)

    month = MONTHS[target_date.month - 1]
    day = target_date.day
    year = target_date.year

    url = (
        f"https://insight.factset.com/"
        f"sp-500-earnings-season-update-{month}-{day}-{year}"
    )

    return {
        "input_date": input_date.isoformat(),
        "target_friday": target_date.isoformat(),
        "month": month,
        "day": day,
        "year": year,
        "url": url
    }


def fetch_factset_page(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers, timeout=20)

    return {
        "status_code": response.status_code,
        "url": url,
        "final_url": response.url,
        "html": response.text if response.status_code != 404 else None
    }
    
def extract_clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # remove useless tags
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # clean empty lines / spaces
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    return "\n".join(lines)

def extract_article_body(clean_text: str) -> str:
    start_marker = "At this late stage of the earnings season"
    end_marker = "This blog post is for informational purposes only."

    start_idx = clean_text.find(start_marker)
    end_idx = clean_text.find(end_marker)

    if start_idx == -1:
        return clean_text

    if end_idx == -1:
        return clean_text[start_idx:].strip()

    return clean_text[start_idx:end_idx].strip()

def save_raw_json(url_info: dict, page_data: dict) -> Path:
    html = page_data["html"]
    clean_text = extract_clean_text(html) if html else None
    article_body = extract_article_body(clean_text) if clean_text else None

    output = {
        "source": "FactSet",
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
        "input_date": url_info["input_date"],
        "target_friday": url_info["target_friday"],
        "url": url_info["url"],
        "final_url": page_data["final_url"],
        "status_code": page_data["status_code"],

        # raw html
        "html": html,

        # full cleaned page text
        "text": clean_text,

        # useful article content only
        "article_body": article_body
    }

    filename = f"factset_sp500_earnings_update_{url_info['target_friday']}.json"
    output_path = Path.cwd() / filename

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return output_path

def get_fridays_in_month(year: int, month: int) -> list[date]:
    _, last_day = calendar.monthrange(year, month)

    fridays = []
    for day in range(1, last_day + 1):
        d = date(year, month, day)
        if d.weekday() == 4:  # Monday=0, Friday=4
            fridays.append(d)

    return fridays


def scan_factset_month(year: int, month: int) -> list[dict]:
    fridays = get_fridays_in_month(year, month)

    successful_pages = []

    for friday in fridays:
        url_info = build_factset_url_from_date(friday)
        print(f"Checking: {url_info['target_friday']} | {url_info['url']}")

        page_data = fetch_factset_page(url_info["url"])
        status = page_data["status_code"]

        if status == 200:
            print("Success")
            successful_pages.append({
                "target_friday": url_info["target_friday"],
                "url": url_info["url"],
                "final_url": page_data["final_url"],
                "status_code": status
            })
        elif status == 404:
            print("Not found")
        else:
            print(f"Unexpected status: {status}")

    return successful_pages

def ingest_factset_month(year: int, month: int) -> list[Path]:
    fridays = get_fridays_in_month(year, month)

    saved_files = []

    for friday in fridays:
        url_info = build_factset_url_from_date(friday)
        page_data = fetch_factset_page(url_info["url"])

        if page_data["status_code"] == 200:
            output_path = save_raw_json(url_info, page_data)
            saved_files.append(output_path)
            print(f"Saved: {output_path}")
        elif page_data["status_code"] == 404:
            print(f"Skip 404: {url_info['url']}")
        else:
            print(f"Skip unexpected status {page_data['status_code']}: {url_info['url']}")

    return saved_files

if __name__ == "__main__":
    year = 2026
    month = 2

    successful_urls = scan_factset_month(year, month)

    print("\nSuccessful URLs:")
    for item in successful_urls:
        print(item)
        print(item["target_friday"], item["url"])

    #saved_files = ingest_factset_month(year, month)

    # print("\nSaved files:")
    # for path in saved_files:
    #     print(path)