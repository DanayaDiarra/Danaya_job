"""
scrapers/linkedin_scraper.py — LinkedIn via Apify actor (optional).
Skips gracefully if APIFY_API_TOKEN is not set.
"""
import hashlib
import os
import sqlite3
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN", "")
APIFY_ACTOR = "curious_coder/linkedin-jobs-scraper"

QUERIES = [
    {"keywords": "Data Scientist", "location": "Russia"},
    {"keywords": "ML Engineer", "location": "Europe"},
    {"keywords": "Data Analyst", "location": "Remote"},
    {"keywords": "LLM Engineer", "location": "Remote"},
    {"keywords": "Agentic AI", "location": "Remote"},
    {"keywords": "Data Scientist", "location": "Africa"},
]


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


def scrape_linkedin(db_path: Path = DB_PATH) -> int:
    """
    Scrape LinkedIn jobs via Apify actor.
    Returns 0 and skips gracefully if APIFY_API_TOKEN is not set.
    """
    if not APIFY_TOKEN:
        logger.info("LinkedIn scraper: APIFY_API_TOKEN not set — skipping.")
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    total = 0

    logger.info("=== LinkedIn scraper (Apify) starting ===")

    for query in QUERIES:
        run_url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items"
        params = {"token": APIFY_TOKEN}
        payload = {
            "keyword": query["keywords"],
            "location": query["location"],
            "maxResults": 15,
            "proxy": {"useApifyProxy": True},
        }

        try:
            logger.info(f"  LinkedIn | {query['keywords']} in {query['location']}")
            resp = requests.post(run_url, json=payload, params=params, timeout=120)
            resp.raise_for_status()
            items = resp.json()
        except Exception as e:
            logger.warning(f"  Apify call failed for {query}: {e}")
            time.sleep(3)
            continue

        for item in items:
            try:
                url = item.get("jobUrl") or item.get("url", "")
                title = item.get("title") or item.get("positionName", "")
                company = item.get("companyName") or item.get("company", "")
                location = item.get("location", query["location"])
                description = (item.get("description") or item.get("descriptionHtml") or "")[:3000]
                posted_at = item.get("postedAt") or item.get("publishedAt", "")

                if not url or not title:
                    continue

                # Detect country from location string
                location_lower = location.lower()
                if any(x in location_lower for x in ["russia", "moscow", "st. petersburg", "spb"]):
                    country = "Russia"
                elif any(x in location_lower for x in ["africa", "nigeria", "kenya", "mali", "ghana", "senegal"]):
                    country = "Africa"
                elif "remote" in location_lower:
                    country = "Remote"
                else:
                    country = "Europe"

                job = {
                    "url_hash": _url_hash(url),
                    "source": "linkedin",
                    "title": title,
                    "company": company,
                    "location": location,
                    "country": country,
                    "salary_raw": item.get("salary", ""),
                    "job_type": item.get("contractType", ""),
                    "language": "en",
                    "description": description,
                    "url": url,
                    "posted_at": posted_at,
                }

                if _save_job(cur, conn, job):
                    total += 1
                    logger.info(f"    + {title} @ {company}")
            except Exception as e:
                logger.debug(f"    Item parse error: {e}")

        time.sleep(2)

    conn.close()
    logger.success(f"LinkedIn scrape complete: {total} new jobs")
    return total
