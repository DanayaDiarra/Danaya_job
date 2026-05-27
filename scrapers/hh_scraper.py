"""
scrapers/hh_scraper.py — hh.ru official REST API scraper.
"""
import hashlib
import html
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
BASE_URL = "https://api.hh.ru/vacancies"
CONTACT_EMAIL = "diarradanaya5544@gmail.com"

def _build_headers() -> dict:
    """Build request headers, adding OAuth token if available."""
    headers = {
        "User-Agent": f"JobAgent/1.0 ({CONTACT_EMAIL})",
        "HH-User-Agent": f"JobAgent/1.0 ({CONTACT_EMAIL})",
    }
    token = os.getenv("HH_API_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

HEADERS = _build_headers()

SEARCH_QUERIES = [
    "Data Scientist",
    "ML Engineer",
    "Data Analyst",
    "Machine Learning",
    "LLM Engineer",
    "Аналитик данных",
    "Business Analyst Data",
    "Product Manager AI",
    "NLP инженер",
    "Deep Learning",
    "LangChain",
]

AREAS = [2, 1, 113]  # SPb, Moscow, Russia


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _fetch_detail(vacancy_id: str, session: requests.Session,
                  headers: Optional[dict] = None) -> Optional[dict]:
    """Fetch full vacancy description from /vacancies/{id}."""
    if headers is None:
        headers = _build_headers()
    try:
        resp = session.get(f"{BASE_URL}/{vacancy_id}", headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"  Could not fetch detail for vacancy {vacancy_id}: {e}")
        return None


def _save_job(cur: sqlite3.Cursor, job: dict) -> bool:
    """Insert job into DB. Returns True if new, False if duplicate."""
    try:
        cur.execute("""
            INSERT OR IGNORE INTO jobs
              (url_hash, source, title, company, location, country,
               salary_raw, job_type, language, description, url, posted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job["url_hash"], job["source"], job["title"], job["company"],
            job["location"], job["country"], job["salary_raw"], job["job_type"],
            job["language"], job["description"], job["url"], job["posted_at"],
        ))
        return cur.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"DB insert error: {e}")
        return False


def scrape_hh(db_path: Path = DB_PATH) -> int:
    """
    Scrape hh.ru using official API.
    Requires HH_API_TOKEN env var for access from non-Russian IPs.
    Returns count of new jobs inserted.
    """
    headers = _build_headers()
    if "Authorization" not in headers:
        logger.warning(
            "HH_API_TOKEN not set — hh.ru blocks non-Russian IPs without OAuth. "
            "Get a free token at: https://dev.hh.ru/  (create app → get access token). "
            "Add HH_API_TOKEN=... to .env and GitHub secrets."
        )

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    session = requests.Session()
    total_new = 0

    for query in SEARCH_QUERIES:
        for area in AREAS:
            logger.info(f"hh.ru | query='{query}' area={area}")
            params = {
                "text": query,
                "area": area,
                "period": 3,
                "per_page": 50,
                "page": 0,
                "only_with_salary": False,
            }
            try:
                resp = session.get(BASE_URL, params=params, headers=headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.warning(f"  Search failed for '{query}'/area={area}: {e}")
                time.sleep(1)
                continue

            items = data.get("items", [])
            logger.info(f"  Found {len(items)} listings")

            for item in items:
                url = item.get("alternate_url", "")
                if not url:
                    continue

                url_hash = _url_hash(url)

                # Skip if already in DB
                cur.execute("SELECT 1 FROM jobs WHERE url_hash=?", (url_hash,))
                if cur.fetchone():
                    continue

                # Fetch full description
                detail = _fetch_detail(item["id"], session, headers)
                time.sleep(0.3)

                if detail:
                    description = _strip_html(detail.get("description", ""))[:3000]
                    salary_raw = ""
                    if detail.get("salary"):
                        s = detail["salary"]
                        salary_raw = f"{s.get('from','')}-{s.get('to','')} {s.get('currency','')}"
                else:
                    description = _strip_html(item.get("snippet", {}).get("requirement", ""))[:3000]
                    salary_raw = ""

                employer = item.get("employer", {})
                area_info = item.get("area", {})

                job = {
                    "url_hash": url_hash,
                    "source": "hh.ru",
                    "title": item.get("name", ""),
                    "company": employer.get("name", ""),
                    "location": area_info.get("name", ""),
                    "country": "Russia",
                    "salary_raw": salary_raw,
                    "job_type": item.get("employment", {}).get("name", ""),
                    "language": "ru",
                    "description": description,
                    "url": url,
                    "posted_at": item.get("published_at", ""),
                }

                if _save_job(cur, job):
                    total_new += 1
                    logger.info(f"  + {job['title']} @ {job['company']}")

            conn.commit()
            time.sleep(1)

    conn.close()
    logger.success(f"hh.ru scrape complete: {total_new} new jobs")
    return total_new
