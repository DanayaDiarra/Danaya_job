"""
scrapers/europe_scraper.py — RemoteOK JSON API and Relocate.me scraper.
"""
import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


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


# ── RemoteOK ──────────────────────────────────────────────────────────────

def _scrape_remoteok(session: requests.Session, cur: sqlite3.Cursor,
                     conn: sqlite3.Connection) -> int:
    tags = ["data-science", "machine-learning", "python", "analyst", "ai", "llm"]
    new = 0

    for tag in tags:
        url = f"https://remoteok.com/api?tag={tag}"
        logger.info(f"  RemoteOK | tag={tag}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"  RemoteOK fetch failed for {tag}: {e}")
            time.sleep(2)
            continue

        # First item is metadata — skip it
        jobs = [item for item in data if isinstance(item, dict) and item.get("id")]

        for item in jobs[:20]:
            job_url = item.get("url", "")
            if not job_url:
                job_url = f"https://remoteok.com/jobs/{item.get('id', '')}"

            description = (item.get("description") or "")[:3000]

            job = {
                "url_hash": _url_hash(job_url),
                "source": "remoteok",
                "title": item.get("position", ""),
                "company": item.get("company", ""),
                "location": "Remote",
                "country": "Remote",
                "salary_raw": (
                    f"{item.get('salary_min','')}-{item.get('salary_max','')}"
                    .strip("-").strip()
                    if item.get("salary_min") or item.get("salary_max") else ""
                ),
                "job_type": "Remote",
                "language": "en",
                "description": description,
                "url": job_url,
                "posted_at": item.get("date", ""),
            }

            if not job["title"]:
                continue

            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── Relocate.me ───────────────────────────────────────────────────────────

def _scrape_relocateme(session: requests.Session, cur: sqlite3.Cursor,
                       conn: sqlite3.Connection) -> int:
    slugs = [
        "data-scientist", "machine-learning", "python-developer",
        "data-analyst", "artificial-intelligence",
    ]
    base = "https://relocate.me"
    new = 0

    for slug in slugs:
        url = f"{base}/jobs/{slug}"
        logger.info(f"  Relocate.me | {slug}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            logger.warning(f"  Relocate.me fetch failed for {slug}: {e}")
            time.sleep(2)
            continue

        # Try multiple selectors for job cards
        cards = (
            soup.select("article.job-card")
            or soup.select("div.job-item")
            or soup.select("li[data-id]")
            or soup.find_all("article")
        )[:15]

        for card in cards:
            try:
                a = card.select_one("h2 a, h3 a, a.job-title, a[href*='/jobs/']")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                job_url = href if href.startswith("http") else urljoin(base, href)

                company_el = card.select_one(".company, .employer-name, [itemprop='name']")
                company = company_el.get_text(strip=True) if company_el else ""

                location_el = card.select_one(".location, .city, [itemprop='addressLocality']")
                location = location_el.get_text(strip=True) if location_el else "Europe"

                desc_el = card.select_one("p, .description, .summary")
                description = desc_el.get_text(" ", strip=True)[:3000] if desc_el else ""

                salary_el = card.select_one(".salary, .compensation")
                salary_raw = salary_el.get_text(strip=True) if salary_el else ""

                job = {
                    "url_hash": _url_hash(job_url),
                    "source": "relocateme",
                    "title": title,
                    "company": company,
                    "location": location,
                    "country": "Europe",
                    "salary_raw": salary_raw,
                    "job_type": "Relocation",
                    "language": "en",
                    "description": description,
                    "url": job_url,
                    "posted_at": "",
                }

                if _save_job(cur, conn, job):
                    new += 1
                    logger.info(f"    + {title} @ {company}")
            except Exception as e:
                logger.debug(f"    Relocate.me card error: {e}")

        time.sleep(2)

    return new


# ── Main entry ─────────────────────────────────────────────────────────────

def scrape_europe(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    session = requests.Session()
    total = 0

    logger.info("=== Europe scrapers starting ===")

    try:
        total += _scrape_remoteok(session, cur, conn)
    except Exception as e:
        logger.error(f"RemoteOK failed: {e}")

    try:
        total += _scrape_relocateme(session, cur, conn)
    except Exception as e:
        logger.error(f"Relocate.me failed: {e}")

    conn.close()
    logger.success(f"Europe scrape complete: {total} new jobs")
    return total
