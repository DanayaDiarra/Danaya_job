"""
scrapers/undp_scraper.py — ReliefWeb API for UN/INGO jobs.
"""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))

RELIEFWEB_URL = "https://api.reliefweb.int/v1/jobs"
UN_ORGS = ["UNDP", "UNICEF", "WFP", "UNHCR", "UN Women", "FAO", "ILO",
           "IOM", "UNOPS", "UN-Habitat"]
MAX_PAGES = 3
PAGE_SIZE = 25


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _save_job(cur: sqlite3.Cursor, conn: sqlite3.Connection, job: dict) -> bool:
    cur.execute("SELECT 1 FROM jobs WHERE url_hash=?", (job["url_hash"],))
    if cur.fetchone():
        return False
    try:
        cur.execute("""
            INSERT INTO jobs
              (url_hash, source, title, company, location, country,
               salary_raw, job_type, language, description, url, posted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job["url_hash"], job["source"], job["title"], job["company"],
            job["location"], job["country"], job["salary_raw"], job["job_type"],
            job["language"], job["description"], job["url"], job["posted_at"],
        ))
        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"DB error: {e}")
        return False


def _strip_html(text: str) -> str:
    import re, html
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def scrape_undp(db_path: Path = DB_PATH) -> int:
    """Scrape UN/INGO jobs from ReliefWeb API."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    total = 0

    logger.info("=== UN/INGO scraper (ReliefWeb) starting ===")

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        payload = {
            "appname": "danaya-job-agent",
            "profile": "list",
            "preset": "latest",
            "offset": offset,
            "limit": PAGE_SIZE,
            "filter": {
                "operator": "OR",
                "conditions": [
                    {"field": "source.name", "value": org}
                    for org in UN_ORGS
                ],
            },
            "fields": {
                "include": [
                    "title", "body", "date", "source", "url",
                    "country", "career_categories", "experience",
                    "type", "closing_date",
                ]
            },
            "sort": ["date:desc"],
        }

        try:
            resp = requests.post(
                RELIEFWEB_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"  ReliefWeb page {page} failed: {e}")
            time.sleep(2)
            continue

        items = data.get("data", [])
        if not items:
            logger.info(f"  No more results at page {page}")
            break

        logger.info(f"  ReliefWeb page {page}: {len(items)} items")

        for item in items:
            fields = item.get("fields", {})
            try:
                title = fields.get("title", "")
                body = _strip_html(fields.get("body", ""))[:3000]
                sources = fields.get("source", [{}])
                company = sources[0].get("name", "") if sources else ""
                countries = fields.get("country", [{}])
                country = countries[0].get("name", "") if countries else ""
                url = fields.get("url", "") or item.get("href", "")
                posted_at = fields.get("date", {}).get("created", "")
                closing = fields.get("closing_date", "")
                categories = fields.get("career_categories", [])
                job_type = ", ".join(c.get("name", "") for c in categories) if categories else ""

                if not title or not url:
                    continue

                job = {
                    "url_hash": _url_hash(url),
                    "source": "reliefweb",
                    "title": title,
                    "company": company,
                    "location": country,
                    "country": country,
                    "salary_raw": "",
                    "job_type": job_type,
                    "language": "en",
                    "description": body,
                    "url": url,
                    "posted_at": posted_at,
                }

                if _save_job(cur, conn, job):
                    total += 1
                    logger.info(f"    + {title} @ {company} ({country})")

            except Exception as e:
                logger.debug(f"    Item parse error: {e}")

        time.sleep(1)

    conn.close()
    logger.success(f"UN/INGO scrape complete: {total} new jobs")
    return total
