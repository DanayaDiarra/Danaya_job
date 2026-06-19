"""
scrapers/global_scraper.py — Himalayas, AI Jobs, Working Nomads, The Muse.
All free public APIs / RSS feeds — no auth tokens required.
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

AI_KEYWORDS = {
    "data", "ml", "machine learning", "ai ", "artificial intelligence",
    "python", "analyst", "science", "nlp", "llm", "deep learning",
    "analytics", "business intelligence", "pytorch", "tensorflow",
}


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _is_relevant(title: str, description: str = "") -> bool:
    combined = (title + " " + description).lower()
    return any(kw in combined for kw in AI_KEYWORDS)


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


# ── Himalayas ─────────────────────────────────────────────────────────────────

def _scrape_himalayas(session: requests.Session, cur: sqlite3.Cursor,
                      conn: sqlite3.Connection) -> int:
    """https://himalayas.app/jobs/api — remote jobs, free JSON API."""
    queries = ["data scientist", "machine learning", "data analyst",
               "AI engineer", "python developer", "business analyst"]
    new = 0

    for q in queries:
        url = f"https://himalayas.app/jobs/api?q={requests.utils.quote(q)}&limit=50"
        logger.info(f"  Himalayas | q='{q}'")
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            jobs = resp.json().get("jobs", [])
        except Exception as e:
            logger.warning(f"  Himalayas fetch failed for '{q}': {e}")
            time.sleep(2)
            continue

        for item in jobs:
            job_url = item.get("applicationLink") or item.get("url", "")
            if not job_url:
                continue

            sal_min = item.get("minSalary") or ""
            sal_max = item.get("maxSalary") or ""
            currency = item.get("currency", "USD")
            salary_raw = f"{sal_min}–{sal_max} {currency}".strip("– ") if (sal_min or sal_max) else ""

            job = {
                "url_hash": _url_hash(job_url),
                "source": "himalayas",
                "title": item.get("title", ""),
                "company": item.get("company", {}).get("name", "") if isinstance(item.get("company"), dict) else item.get("company", ""),
                "location": item.get("location", "Remote") or "Remote",
                "country": item.get("country", "Remote") or "Remote",
                "salary_raw": salary_raw,
                "job_type": "Remote",
                "language": "en",
                "description": _strip_html(item.get("description", ""))[:3000],
                "url": job_url,
                "posted_at": item.get("createdAt", "") or item.get("publishedAt", ""),
            }
            if not job["title"]:
                continue
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── AI Jobs (aijobs.net) ──────────────────────────────────────────────────────

def _scrape_aijobs(session: requests.Session, cur: sqlite3.Cursor,
                   conn: sqlite3.Connection) -> int:
    """https://aijobs.net/feed/ — RSS feed, AI/ML jobs only."""
    url = "https://aijobs.net/feed/"
    logger.info("  AI Jobs (aijobs.net)")
    new = 0

    try:
        resp = session.get(url, headers={**HEADERS, "Accept": "application/rss+xml"}, timeout=20)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        logger.warning(f"  aijobs.net RSS failed: {e}")
        return 0

    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        job_url = (item.findtext("link") or "").strip()
        pub_date = item.findtext("pubDate") or ""
        description_raw = (
            item.findtext("content:encoded", namespaces=ns)
            or item.findtext("description") or ""
        )
        description = _strip_html(description_raw)[:3000]

        # Parse "Company | Location" from title if present
        company, location = "", "Remote"
        if " | " in title:
            parts = title.split(" | ")
            title = parts[0].strip()
            if len(parts) >= 2:
                company = parts[1].strip()
            if len(parts) >= 3:
                location = parts[2].strip()

        if not title or not job_url:
            continue

        job = {
            "url_hash": _url_hash(job_url),
            "source": "aijobs",
            "title": title,
            "company": company,
            "location": location,
            "country": "Remote",
            "salary_raw": "",
            "job_type": "Remote",
            "language": "en",
            "description": description,
            "url": job_url,
            "posted_at": pub_date,
        }
        if _save_job(cur, conn, job):
            new += 1
            logger.info(f"    + {job['title']} @ {job['company']}")

    return new


# ── Working Nomads ────────────────────────────────────────────────────────────

def _scrape_working_nomads(session: requests.Session, cur: sqlite3.Cursor,
                            conn: sqlite3.Connection) -> int:
    """https://www.workingnomads.com/api/exposed_jobs/ — JSON API, remote jobs."""
    categories = ["data-science", "development", "management"]
    new = 0

    for cat in categories:
        url = f"https://www.workingnomads.com/api/exposed_jobs/?category={cat}"
        logger.info(f"  Working Nomads | category={cat}")
        try:
            resp = session.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            jobs = resp.json()
            if not isinstance(jobs, list):
                jobs = jobs.get("results", [])
        except Exception as e:
            logger.warning(f"  Working Nomads failed for {cat}: {e}")
            time.sleep(2)
            continue

        for item in jobs[:50]:
            job_url = item.get("url", "")
            if not job_url:
                continue

            title = item.get("title", "")
            description = _strip_html(item.get("description", ""))[:3000]

            if cat != "data-science" and not _is_relevant(title, description):
                continue

            job = {
                "url_hash": _url_hash(job_url),
                "source": "workingnomads",
                "title": title,
                "company": item.get("company_name", ""),
                "location": "Remote",
                "country": item.get("region", "Remote") or "Remote",
                "salary_raw": "",
                "job_type": "Remote",
                "language": "en",
                "description": description,
                "url": job_url,
                "posted_at": item.get("pub_date", ""),
            }
            if not job["title"]:
                continue
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']} @ {job['company']}")

        time.sleep(1)

    return new


# ── The Muse ──────────────────────────────────────────────────────────────────

def _scrape_the_muse(session: requests.Session, cur: sqlite3.Cursor,
                     conn: sqlite3.Connection) -> int:
    """https://www.themuse.com/api/public/jobs — free public API, no key needed."""
    categories = ["Data Science", "Analytics", "IT", "Engineering"]
    new = 0

    for cat in categories:
        for page in range(1, 4):  # 3 pages × 20 = up to 60 jobs per category
            url = (
                f"https://www.themuse.com/api/public/jobs"
                f"?category={requests.utils.quote(cat)}&page={page}&descending=true"
            )
            logger.info(f"  The Muse | category={cat} page={page}")
            try:
                resp = session.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                items = data.get("results", [])
            except Exception as e:
                logger.warning(f"  The Muse failed for {cat} p{page}: {e}")
                time.sleep(2)
                break

            if not items:
                break

            for item in items:
                job_url = item.get("refs", {}).get("landing_page", "")
                if not job_url:
                    continue

                title = item.get("name", "")
                description = _strip_html(item.get("contents", ""))[:3000]

                if cat != "Data Science" and not _is_relevant(title, description):
                    continue

                company = item.get("company", {}).get("name", "") \
                    if isinstance(item.get("company"), dict) else ""
                locations = item.get("locations", [])
                location = locations[0].get("name", "Remote") if locations else "Remote"
                levels = item.get("levels", [])
                job_type = levels[0].get("name", "") if levels else ""

                job = {
                    "url_hash": _url_hash(job_url),
                    "source": "themuse",
                    "title": title,
                    "company": company,
                    "location": location,
                    "country": "USA",
                    "salary_raw": "",
                    "job_type": job_type,
                    "language": "en",
                    "description": description,
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


# ── Devsnap (dev/tech jobs RSS) ───────────────────────────────────────────────

def _scrape_devsnap(session: requests.Session, cur: sqlite3.Cursor,
                    conn: sqlite3.Connection) -> int:
    """https://devsnap.me/jobs — curated tech job board with RSS."""
    searches = [
        "https://devsnap.me/jobs/rss?q=data+scientist",
        "https://devsnap.me/jobs/rss?q=machine+learning",
        "https://devsnap.me/jobs/rss?q=data+analyst",
    ]
    new = 0

    for url in searches:
        q = url.split("q=")[-1]
        logger.info(f"  Devsnap | q={q}")
        try:
            resp = session.get(url, headers={**HEADERS, "Accept": "application/rss+xml"},
                               timeout=20)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            logger.warning(f"  Devsnap RSS failed for {q}: {e}")
            time.sleep(2)
            continue

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            job_url = (item.findtext("link") or "").strip()
            pub_date = item.findtext("pubDate") or ""
            desc_raw = item.findtext("description") or ""
            description = _strip_html(desc_raw)[:3000]

            if not title or not job_url:
                continue

            job = {
                "url_hash": _url_hash(job_url),
                "source": "devsnap",
                "title": title,
                "company": "",
                "location": "Remote",
                "country": "Remote",
                "salary_raw": "",
                "job_type": "Remote",
                "language": "en",
                "description": description,
                "url": job_url,
                "posted_at": pub_date,
            }
            if _save_job(cur, conn, job):
                new += 1
                logger.info(f"    + {job['title']}")

        time.sleep(1)

    return new


# ── Main entry ─────────────────────────────────────────────────────────────────

def scrape_global(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    session = requests.Session()
    total = 0

    logger.info("=== Global job board scrapers starting ===")

    for name, fn in [
        ("Himalayas",      _scrape_himalayas),
        ("AI Jobs",        _scrape_aijobs),
        ("Working Nomads", _scrape_working_nomads),
        ("The Muse",       _scrape_the_muse),
        ("Devsnap",        _scrape_devsnap),
    ]:
        try:
            n = fn(session, cur, conn)
            total += n
            logger.info(f"  {name}: {n} new jobs")
        except Exception as e:
            logger.error(f"  {name} scraper failed: {e}")

    conn.close()
    logger.success(f"Global scrape complete: {total} new jobs")
    return total
