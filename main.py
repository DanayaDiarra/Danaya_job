"""
main.py — Orchestrator: scrape → score → notify → submit.

Usage:
  python main.py                  # full pipeline
  python main.py --scrape-only    # scrape only
  python main.py --score-only     # score only
  python main.py --no-submit      # scrape + score + notify, no submission
  python main.py --dry-run        # everything except actual submission
"""
import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
LOG_PATH = Path(os.getenv("LOG_PATH", "data/agent.log"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))
AUTO_APPLY_THRESHOLD = int(os.getenv("AUTO_APPLY_THRESHOLD", "90"))
MAX_APPS_PER_DAY = int(os.getenv("MAX_APPLICATIONS_PER_DAY", "8"))

# Configure loguru
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger.remove()
logger.add(sys.stdout, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add(LOG_PATH, level="DEBUG", rotation="10 MB", retention="30 days",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}")


def init_db():
    from setup import init_db as _init
    _init(DB_PATH)


def run_scrapers() -> int:
    from scrapers.hh_scraper import scrape_hh
    from scrapers.africa_scraper import scrape_africa
    from scrapers.europe_scraper import scrape_europe
    from scrapers.undp_scraper import scrape_undp
    from scrapers.linkedin_scraper import scrape_linkedin

    total = 0
    for name, fn in [
        ("hh.ru", scrape_hh),
        ("Africa", scrape_africa),
        ("Europe", scrape_europe),
        ("UN/INGO", scrape_undp),
        ("LinkedIn", scrape_linkedin),
    ]:
        try:
            logger.info(f"▶ Starting {name} scraper")
            n = fn(DB_PATH)
            total += n
            logger.success(f"  ✓ {name}: {n} new jobs")
        except Exception as e:
            logger.error(f"  ✗ {name} scraper failed: {e}")

    return total


def run_scorer() -> int:
    from scorer.scorer import score_all_pending
    try:
        return score_all_pending(DB_PATH)
    except RuntimeError as e:
        logger.error(f"Scorer failed: {e}")
        return 0


def get_surfaced_count() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM scored_jobs s
        LEFT JOIN decisions d ON d.job_id = s.job_id
        WHERE s.score >= ? AND d.id IS NULL
    """, (SCORE_THRESHOLD,))
    n = cur.fetchone()[0]
    conn.close()
    return n


def get_approved_jobs() -> list:
    """Jobs with decision=apply and no application record yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT j.id, j.title, j.company, j.url, j.source,
               s.cover_letter, s.cover_language
        FROM jobs j
        JOIN decisions d ON d.job_id = j.id
        LEFT JOIN applications a ON a.job_id = j.id
        LEFT JOIN scored_jobs s ON s.job_id = j.id
        WHERE d.decision = 'apply'
          AND a.id IS NULL
        ORDER BY j.id
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def count_todays_applications() -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM applications WHERE date(applied_at) = date('now')"
    )
    n = cur.fetchone()[0]
    conn.close()
    return n


def record_application(job_id: int, method: str, cv_path: str = "") -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO applications (job_id, method, status, cv_path)
        VALUES (?, ?, 'sent', ?)
    """, (job_id, method, cv_path))
    conn.commit()
    conn.close()


def run_submissions(dry_run: bool = False) -> int:
    from applicator.cv_generator import generate_cv
    from applicator.cover_letter import generate_cover_letter_file
    from applicator.telegram_bot import send_application_confirmation

    approved = get_approved_jobs()
    if not approved:
        logger.info("No approved jobs pending submission.")
        return 0

    submitted = 0
    todays_count = count_todays_applications()

    for job in approved:
        if todays_count >= MAX_APPS_PER_DAY:
            logger.warning(f"Daily cap ({MAX_APPS_PER_DAY}) reached — stopping submissions")
            break

        job_id = job["id"]
        title = job["title"]
        company = job["company"]
        source = job["source"]
        url = job["url"] or ""

        logger.info(f"▶ Submitting: {title} @ {company} [{source}]")

        # Generate CV + cover letter
        try:
            docx_path, pdf_path = generate_cv(job_id, DB_PATH)
            cover_path = generate_cover_letter_file(job_id, DB_PATH)
        except Exception as e:
            logger.error(f"  Document generation failed: {e}")
            continue

        if dry_run:
            logger.info(f"  [DRY RUN] Would submit {title} @ {company}")
            record_application(job_id, "manual", str(pdf_path))
            submitted += 1
            todays_count += 1
            continue

        # Determine submission method
        success = False
        method = "manual"

        if source == "hh.ru" and url:
            method = "easy_apply"
            try:
                from applicator.hh_submitter import submit_hh_job
                success = submit_hh_job(job_id, DB_PATH)
            except Exception as e:
                logger.error(f"  hh.ru submission error: {e}")

        elif source in ("jobberman", "rekrute", "brightermonday", "remoteok",
                         "relocateme", "reliefweb"):
            # Try email if recruiter address in description
            method = "email"
            try:
                from applicator.email_submitter import send_email_application
                success = send_email_application(job_id, pdf_path, cover_path, DB_PATH)
            except Exception as e:
                logger.error(f"  Email submission error: {e}")

        if not success:
            # Log as manual + notify user
            method = "manual"
            from applicator.telegram_bot import _send_message
            _send_message(
                f"👆 <b>Manual apply needed:</b>\n"
                f"<b>{title}</b> @ {company}\n"
                f"<a href='{url}'>Open job →</a>"
            )
            logger.info(f"  Manual apply notified via Telegram")

        record_application(job_id, method, str(pdf_path))
        submitted += 1
        todays_count += 1

        if success:
            try:
                send_application_confirmation(job_id, DB_PATH)
            except Exception:
                pass

    return submitted


def main():
    parser = argparse.ArgumentParser(description="Job Agent Pipeline")
    parser.add_argument("--scrape-only", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    parser.add_argument("--no-submit", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info(f"Job Agent starting — {date.today()}")
    logger.info("=" * 60)

    # 1. Init DB
    init_db()

    # 2. Scrape
    scraped = 0
    if not args.score_only:
        scraped = run_scrapers()
        logger.info(f"Scrape total: {scraped} new jobs")

    # 3. Score
    scored = 0
    if not args.scrape_only:
        scored = run_scorer()
        logger.info(f"Scoring total: {scored} jobs scored")

    # 4. Telegram digest
    surfaced = get_surfaced_count()
    if surfaced > 0 and not args.scrape_only:
        logger.info(f"{surfaced} new matches above threshold — sending Telegram digest")
        try:
            from applicator.telegram_bot import send_daily_digest, handle_callback_updates
            send_daily_digest(DB_PATH)
            handle_callback_updates(DB_PATH)
        except Exception as e:
            logger.warning(f"Telegram digest failed: {e}")

    # 5. Submit approved jobs
    submitted = 0
    if not args.scrape_only and not args.score_only and not args.no_submit:
        submitted = run_submissions(dry_run=args.dry_run)

    # 6. Summary
    logger.success("=" * 60)
    logger.success(
        f"Run complete: {scraped} scraped | {scored} scored | "
        f"{surfaced} surfaced | {submitted} submitted"
    )
    logger.success("=" * 60)


if __name__ == "__main__":
    main()
