"""
applicator/hh_submitter.py — Playwright-based hh.ru Easy Apply submission.
"""
import asyncio
import os
import random
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
HH_EMAIL = os.getenv("HH_EMAIL", "")
HH_PASSWORD = os.getenv("HH_PASSWORD", "")
SCREENSHOTS_DIR = Path("data/applications")


def _human_delay(lo: float = 1.5, hi: float = 3.5) -> None:
    asyncio.get_event_loop().run_until_complete(
        asyncio.sleep(random.uniform(lo, hi))
    )


async def _submit_job_async(job_url: str, cover_text: str, cv_path: str) -> bool:
    """Async Playwright session to submit one job on hh.ru."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("playwright not installed. Run: pip install playwright && playwright install chromium")
        return False

    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            # ── Login ──────────────────────────────────────────────────
            logger.info("  hh.ru: logging in...")
            await page.goto("https://hh.ru/account/login", timeout=30000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # Accept cookies if present
            try:
                await page.click("button[data-qa='cookie-accept']", timeout=3000)
            except Exception:
                pass

            # Fill credentials
            await page.fill("input[data-qa='login-input-username']", HH_EMAIL)
            await asyncio.sleep(random.uniform(0.5, 1.2))
            await page.fill("input[data-qa='login-input-password']", HH_PASSWORD)
            await asyncio.sleep(random.uniform(0.8, 1.5))
            await page.click("button[data-qa='account-login-submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)

            # Check for CAPTCHA
            if await page.query_selector(".captcha, [data-qa='captcha']"):
                logger.warning("  CAPTCHA detected — skipping this job")
                await page.screenshot(
                    path=str(SCREENSHOTS_DIR / f"captcha_{id(job_url)}.png")
                )
                return False

            # ── Navigate to job ────────────────────────────────────────
            logger.info(f"  Navigating to {job_url}")
            await page.goto(job_url, timeout=30000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(random.uniform(2, 3.5))

            # ── Click Apply button ─────────────────────────────────────
            apply_btn = await page.query_selector(
                "[data-qa='vacancy-response-link-top'], "
                "[data-qa='vacancy-response-link-bot'], "
                "button.bloko-button:has-text('Откликнуться')"
            )
            if not apply_btn:
                logger.warning("  Apply button not found — job may require login or be closed")
                return False

            await apply_btn.click()
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # ── Cover letter field ─────────────────────────────────────
            cover_field = await page.query_selector(
                "textarea[data-qa='vacancy-response-popup-form-letter'],"
                "textarea[name='letter'],"
                "textarea.vacancy-response-letter"
            )
            if cover_field and cover_text:
                await cover_field.fill(cover_text[:1500])  # hh.ru cap
                await asyncio.sleep(random.uniform(0.5, 1.0))

            # ── Submit ────────────────────────────────────────────────
            submit_btn = await page.query_selector(
                "[data-qa='vacancy-response-letter-submit'],"
                "button[type='submit']:has-text('Откликнуться'),"
                "button[type='submit']:has-text('Отправить')"
            )
            if not submit_btn:
                logger.warning("  Submit button not found after Apply click")
                await page.screenshot(
                    path=str(SCREENSHOTS_DIR / f"no_submit_{id(job_url)}.png")
                )
                return False

            await submit_btn.click()
            await asyncio.sleep(random.uniform(2, 3))

            # Screenshot success
            await page.screenshot(
                path=str(SCREENSHOTS_DIR / f"success_{id(job_url)}.png")
            )
            logger.success(f"  Applied successfully to {job_url}")
            return True

        except Exception as e:
            logger.error(f"  hh.ru submission error: {e}")
            try:
                await page.screenshot(
                    path=str(SCREENSHOTS_DIR / f"error_{id(job_url)}.png")
                )
            except Exception:
                pass
            return False
        finally:
            await browser.close()


def submit_hh_job(job_id: int, db_path: Path = DB_PATH) -> bool:
    """
    Submit a job application on hh.ru.
    Returns True on success.
    """
    if not HH_EMAIL or not HH_PASSWORD:
        logger.error("HH_EMAIL or HH_PASSWORD not set in .env")
        return False

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT j.url, s.cover_letter
        FROM jobs j
        LEFT JOIN scored_jobs s ON s.job_id = j.id
        WHERE j.id = ?
    """, (job_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        logger.error(f"Job {job_id} not found")
        return False

    job_url = row["url"]
    cover_text = row["cover_letter"] or ""

    return asyncio.get_event_loop().run_until_complete(
        _submit_job_async(job_url, cover_text, "")
    )
