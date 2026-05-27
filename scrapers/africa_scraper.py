"""
scrapers/africa_scraper.py — Jobberman, Rekrute, BrighterMonday scrapers.
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
DELAY = 2.0
MAX_RESULTS = 15
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


def _fetch(url: str, session: requests.Session) -> Optional[BeautifulSoup]:
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as e:
        logger.warning(f"  Fetch failed {url}: {e}")
        return None


# ── Jobberman ──────────────────────────────────────────────────────────────

def _scrape_jobberman(session: requests.Session, cur: sqlite3.Cursor,
                      conn: sqlite3.Connection) -> int:
    queries = ["data scientist", "data analyst", "machine learning", "business analyst"]
    new = 0
    base = "https://www.jobberman.com"

    for q in queries:
        url = f"{base}/listings?q={requests.utils.quote(q)}"
        logger.info(f"  Jobberman | {url}")
        soup = _fetch(url, session)
        if not soup:
            time.sleep(DELAY)
            continue

        cards = soup.select(
            "article.job-card, div.job-card, li.job-item, "
            "div[data-testid='job-card'], article"
        )[:MAX_RESULTS]
        if not cards:
            # Fallback: any article tag
            cards = soup.find_all("article")[:MAX_RESULTS]

        for card in cards:
            try:
                title_el = card.select_one("h2 a, h3 a, a.job-title, a[data-testid='job-title']")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                job_url = href if href.startswith("http") else urljoin(base, href)
                company_el = card.select_one(".company-name, [data-testid='company-name'], .employer")
                company = company_el.get_text(strip=True) if company_el else ""
                location_el = card.select_one(".location, [data-testid='location']")
                location = location_el.get_text(strip=True) if location_el else "Nigeria"
                desc_el = card.select_one(".description, .summary, p")
                description = desc_el.get_text(" ", strip=True)[:3000] if desc_el else ""

                job = {
                    "url_hash": _url_hash(job_url),
                    "source": "jobberman",
                    "title": title,
                    "company": company,
                    "location": location,
                    "country": "Nigeria",
                    "salary_raw": "",
                    "job_type": "",
                    "language": "en",
                    "description": description,
                    "url": job_url,
                    "posted_at": "",
                }
                if _save_job(cur, conn, job):
                    new += 1
                    logger.info(f"    + {title} @ {company}")
            except Exception as e:
                logger.debug(f"    Card parse error: {e}")

        time.sleep(DELAY)

    return new


# ── Rekrute ───────────────────────────────────────────────────────────────

def _scrape_rekrute(session: requests.Session, cur: sqlite3.Cursor,
                    conn: sqlite3.Connection) -> int:
    queries = ["data scientist", "analyste donnees", "business development", "machine learning"]
    new = 0
    base = "https://www.rekrute.com"

    for q in queries:
        # Rekrute search URL — keyword only, no lang/s params that cause 404
        encoded_q = requests.utils.quote(q)
        url = f"{base}/offres-emploi.html?keyword={encoded_q}"
        logger.info(f"  Rekrute | query='{q}'")
        soup = _fetch(url, session)
        if not soup:
            time.sleep(DELAY)
            continue

        listings = soup.select(
            "li.post-id, div.post-id, div.post, "
            "article.job-listing, li.job-listing"
        )[:MAX_RESULTS]
        if not listings:
            # Broader fallback: any li/div with an anchor inside
            listings = soup.find_all(["li", "article"], limit=MAX_RESULTS)

        for item in listings:
            try:
                a = item.find("a", href=True)
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a["href"]
                job_url = href if href.startswith("http") else urljoin(base, href)
                company_el = item.select_one(".company, .recruteur")
                company = company_el.get_text(strip=True) if company_el else ""
                desc_el = item.select_one(".description, p")
                description = desc_el.get_text(" ", strip=True)[:3000] if desc_el else ""

                # Detect language from content
                lang = "fr" if any(w in description.lower() for w in ["recherche", "poste", "emploi", "compétences"]) else "en"

                job = {
                    "url_hash": _url_hash(job_url),
                    "source": "rekrute",
                    "title": title,
                    "company": company,
                    "location": "Morocco",
                    "country": "Morocco",
                    "salary_raw": "",
                    "job_type": "",
                    "language": lang,
                    "description": description,
                    "url": job_url,
                    "posted_at": "",
                }
                if _save_job(cur, conn, job):
                    new += 1
                    logger.info(f"    + {title} @ {company}")
            except Exception as e:
                logger.debug(f"    Rekrute parse error: {e}")

        time.sleep(DELAY)

    return new


# ── BrighterMonday ────────────────────────────────────────────────────────

def _scrape_brightermonday(session: requests.Session, cur: sqlite3.Cursor,
                            conn: sqlite3.Connection) -> int:
    queries = ["data scientist", "data analyst", "machine learning", "business analyst"]
    new = 0
    base = "https://www.brightermonday.co.ke"

    for q in queries:
        encoded_q = requests.utils.quote(q)
        url = f"{base}/jobs?q={encoded_q}"
        logger.info(f"  BrighterMonday | query='{q}'")
        soup = _fetch(url, session)
        if not soup:
            time.sleep(DELAY)
            continue

        cards = soup.select(
            "article.search-result, div.search-result, "
            "div[data-job-id], article.job-card, div.job-card"
        )[:MAX_RESULTS]
        if not cards:
            cards = soup.find_all("article")[:MAX_RESULTS]

        for card in cards:
            try:
                a = card.select_one("h2 a, h3 a, a.title")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                job_url = href if href.startswith("http") else urljoin(base, href)
                company_el = card.select_one(".company, .employer")
                company = company_el.get_text(strip=True) if company_el else ""
                location_el = card.select_one(".location, .city")
                location = location_el.get_text(strip=True) if location_el else "Kenya"
                desc_el = card.select_one("p, .summary")
                description = desc_el.get_text(" ", strip=True)[:3000] if desc_el else ""

                job = {
                    "url_hash": _url_hash(job_url),
                    "source": "brightermonday",
                    "title": title,
                    "company": company,
                    "location": location,
                    "country": "Kenya",
                    "salary_raw": "",
                    "job_type": "",
                    "language": "en",
                    "description": description,
                    "url": job_url,
                    "posted_at": "",
                }
                if _save_job(cur, conn, job):
                    new += 1
                    logger.info(f"    + {title} @ {company}")
            except Exception as e:
                logger.debug(f"    BM parse error: {e}")

        time.sleep(DELAY)

    return new


# ── Main entry ─────────────────────────────────────────────────────────────

def scrape_africa(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    session = requests.Session()
    total = 0

    logger.info("=== Africa scrapers starting ===")

    try:
        total += _scrape_jobberman(session, cur, conn)
    except Exception as e:
        logger.error(f"Jobberman failed: {e}")

    try:
        total += _scrape_rekrute(session, cur, conn)
    except Exception as e:
        logger.error(f"Rekrute failed: {e}")

    try:
        total += _scrape_brightermonday(session, cur, conn)
    except Exception as e:
        logger.error(f"BrighterMonday failed: {e}")

    conn.close()
    logger.success(f"Africa scrape complete: {total} new jobs")
    return total
