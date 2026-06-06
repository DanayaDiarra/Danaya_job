"""
scrapers/remote_scraper.py — Remotive, Jobicy, Arbeitnow, We Work Remotely.
All use free public JSON or RSS APIs — no tokens needed.
"""
import hashlib
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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


# ── Remotive ──────────────────────────────────────────────────────────────────

def _scrape_remotive(session: requests.Session, cur: sqlite3.Cursor,
                     conn: sqlite3.Connection) -> int:
    """https://remotive.com/api/remote-jobs — free, no auth."""
    categories = ["data", "software-dev", "ai"]
    new = 0

    for cat in categories:
        url = f"https://remotive.com/api/remote-jobs?category={cat}&limit=50"
        logger.info(f"  Remotive | category={cat}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning(f"  Remotive fetch failed for {cat}: {e}")
            time.sleep(2)
            continue

        for item in jobs:
            job_url = item.get("url", "")
            if not job_url:
                continue

            salary_min = item.get("salary", "") or ""

            job = {
                "url_hash": _url_hash(job_url),
                "source": "remotive",
                "title": item.get("title", ""),
                "company": item.get("company_name", ""),
                "location": item.get("candidate_required_location", "Remote") or "Remote",
                "country": "Remote",
                "salary_raw": salary_min,
                "job_type": item.get("job_type", ""),
                "language": "en",
                "description": _strip_html(item.get("description", ""))[:3000],
                "url": job_url,
                "posted_at": item.get("publication_date", ""),
            }
            if not job["title"]:
                continue
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── Jobicy ────────────────────────────────────────────────────────────────────

def _scrape_jobicy(session: requests.Session, cur: sqlite3.Cursor,
                   conn: sqlite3.Connection) -> int:
    """https://jobicy.com/api/v2/remote-jobs — free, no auth."""
    tags = ["data-science", "machine-learning", "python", "artificial-intelligence"]
    new = 0

    for tag in tags:
        url = f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={tag}"
        logger.info(f"  Jobicy | tag={tag}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning(f"  Jobicy fetch failed for {tag}: {e}")
            time.sleep(2)
            continue

        for item in jobs:
            job_url = item.get("url", "")
            if not job_url:
                continue

            sal_min = item.get("annualSalaryMin") or ""
            sal_max = item.get("annualSalaryMax") or ""
            currency = item.get("salaryCurrency", "")
            if sal_min or sal_max:
                salary_raw = f"{sal_min}-{sal_max} {currency}".strip()
            else:
                salary_raw = ""

            job = {
                "url_hash": _url_hash(job_url),
                "source": "jobicy",
                "title": item.get("jobTitle", ""),
                "company": item.get("companyName", ""),
                "location": item.get("jobGeo", "Remote") or "Remote",
                "country": "Remote",
                "salary_raw": salary_raw,
                "job_type": item.get("jobType", ""),
                "language": "en",
                "description": _strip_html(item.get("jobDescription", ""))[:3000],
                "url": job_url,
                "posted_at": item.get("pubDate", ""),
            }
            if not job["title"]:
                continue
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── Arbeitnow ─────────────────────────────────────────────────────────────────

def _scrape_arbeitnow(session: requests.Session, cur: sqlite3.Cursor,
                      conn: sqlite3.Connection) -> int:
    """https://www.arbeitnow.com/api/job-board-api — free, European + relocation jobs."""
    new = 0
    # Fetch first 3 pages (75 jobs); API is paginated
    for page in range(1, 4):
        url = f"https://www.arbeitnow.com/api/job-board-api?page={page}"
        logger.info(f"  Arbeitnow | page={page}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            items = resp.json().get("data", [])
        except Exception as e:
            logger.warning(f"  Arbeitnow page {page} failed: {e}")
            time.sleep(2)
            break

        if not items:
            break

        for item in items:
            job_url = item.get("url", "")
            if not job_url:
                continue

            tags = item.get("tags", [])
            # Filter to relevant roles
            tag_str = " ".join(tags).lower()
            relevant = any(kw in tag_str or kw in item.get("title", "").lower()
                           for kw in ["data", "ml", "machine learning", "ai",
                                      "python", "analyst", "science", "nlp"])
            if not relevant:
                continue

            location = item.get("location", "Europe")
            is_remote = item.get("remote", False)

            job = {
                "url_hash": _url_hash(job_url),
                "source": "arbeitnow",
                "title": item.get("title", ""),
                "company": item.get("company_name", ""),
                "location": "Remote" if is_remote else location,
                "country": "Europe",
                "salary_raw": "",
                "job_type": ", ".join(item.get("job_types", [])),
                "language": "en",
                "description": _strip_html(item.get("description", ""))[:3000],
                "url": job_url,
                "posted_at": str(item.get("created_at", "")),
            }
            if not job["title"]:
                continue
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── We Work Remotely ──────────────────────────────────────────────────────────

def _scrape_wwr(session: requests.Session, cur: sqlite3.Cursor,
                conn: sqlite3.Connection) -> int:
    """We Work Remotely RSS feeds — no auth, reliable."""
    feeds = [
        ("https://weworkremotely.com/categories/remote-data-science-jobs.rss",
         "Data Science"),
        ("https://weworkremotely.com/categories/remote-programming-jobs.rss",
         "Programming"),
    ]
    new = 0

    for feed_url, category in feeds:
        logger.info(f"  WeWorkRemotely | {category}")
        try:
            resp = session.get(feed_url, headers={**HEADERS, "Accept": "application/rss+xml"},
                               timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            logger.warning(f"  WWR feed failed for {category}: {e}")
            time.sleep(2)
            continue

        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            job_url = (item.findtext("link") or "").strip()
            company_region = (item.findtext("region") or "")
            description_raw = (
                item.findtext("content:encoded", namespaces=ns)
                or item.findtext("description")
                or ""
            )
            pub_date = item.findtext("pubDate") or ""

            if not title or not job_url:
                continue

            # WWR title format: "Company: Job Title"
            if ": " in title:
                company, title = title.split(": ", 1)
            else:
                company = ""

            # Filter non-relevant programming jobs
            if category == "Programming":
                kws = ["data", "ml", "machine learning", "ai", "python",
                       "analyst", "science", "nlp", "llm"]
                if not any(kw in title.lower() for kw in kws):
                    continue

            job = {
                "url_hash": _url_hash(job_url),
                "source": "weworkremotely",
                "title": title.strip(),
                "company": company.strip(),
                "location": company_region or "Remote",
                "country": "Remote",
                "salary_raw": "",
                "job_type": "Remote",
                "language": "en",
                "description": _strip_html(description_raw)[:3000],
                "url": job_url,
                "posted_at": pub_date,
            }
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── Main entry ─────────────────────────────────────────────────────────────────

def scrape_remote(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    session = requests.Session()
    total = 0

    logger.info("=== Remote job board scrapers starting ===")

    for name, fn in [
        ("Remotive", _scrape_remotive),
        ("Jobicy", _scrape_jobicy),
        ("Arbeitnow", _scrape_arbeitnow),
        ("WeWorkRemotely", _scrape_wwr),
    ]:
        try:
            n = fn(session, cur, conn)
            total += n
            logger.info(f"  {name}: {n} new jobs")
        except Exception as e:
            logger.error(f"  {name} scraper failed: {e}")

    conn.close()
    logger.success(f"Remote scrape complete: {total} new jobs")
    return total
