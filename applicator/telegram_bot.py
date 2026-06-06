"""
applicator/telegram_bot.py — Telegram notifications and inline review buttons.
Falls back to plain requests if python-telegram-bot is not installed.
"""
import html as html_mod
import json
import os
import sqlite3
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

LABEL_EMOJI = {
    "Excellent Match": "🟢",
    "Good Match": "🔵",
    "Partial Match": "🟡",
    "Poor Match": "🔴",
}


def _tg_post(endpoint: str, payload: dict, retries: int = 3) -> bool:
    """Low-level Telegram HTTP POST with retry on network errors."""
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram not configured (BOT_TOKEN or CHAT_ID missing)")
        return False
    for attempt in range(retries):
        try:
            resp = requests.post(f"{TG_API}/{endpoint}", json=payload, timeout=15)
            if not resp.ok:
                logger.error(f"Telegram API {resp.status_code}: {resp.text[:300]}")
                return False
            return True
        except requests.exceptions.ConnectionError as e:
            wait = 2 ** attempt  # 1s, 2s, 4s
            logger.warning(f"Telegram connection error (attempt {attempt+1}/{retries}), retrying in {wait}s: {e}")
            if attempt < retries - 1:
                time.sleep(wait)
        except Exception as e:
            logger.error(f"Telegram API error: {e}")
            return False
    logger.error(f"Telegram: all {retries} attempts failed")
    return False


def _send_message(text: str, parse_mode: str = "HTML",
                  reply_markup: dict = None) -> bool:
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _tg_post("sendMessage", payload)


def send_daily_digest(db_path: Path = DB_PATH) -> int:
    """
    Send Telegram digest of top new matches not yet notified or decided.
    A job is sent at most once — notified_at is stamped on success.
    Returns number of jobs sent.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT j.id, j.title, j.company, j.location, j.country,
               j.url, j.source, j.salary_raw,
               s.score, s.score_label, s.match_tags, s.gap_tags, s.reasoning
        FROM jobs j
        JOIN scored_jobs s ON s.job_id = j.id
        LEFT JOIN decisions d ON d.job_id = j.id
        WHERE s.score >= ?
          AND s.notified_at IS NULL
          AND d.id IS NULL
        ORDER BY s.score DESC
        LIMIT 10
    """, (SCORE_THRESHOLD,))
    jobs = [dict(row) for row in cur.fetchall()]

    if not jobs:
        logger.info("No new matches to send in digest")
        conn.close()
        _send_message(f"🤖 <b>Job Agent — {date.today()}</b>\nNo new matches above threshold.")
        return 0

    # Header
    _send_message(
        f"🤖 <b>Job Agent — {date.today()}</b>\n"
        f"<b>{len(jobs)} new match{'es' if len(jobs)>1 else ''}</b> above score {SCORE_THRESHOLD}\n"
        f"Review each below 👇"
    )

    def e(v):
        """Escape a value for Telegram HTML mode."""
        return html_mod.escape(str(v or ""))

    sent = 0
    for job in jobs:
        try:
            match_tags = json.loads(job["match_tags"] or "[]")
            gap_tags = json.loads(job["gap_tags"] or "[]")
            label = job["score_label"] or ""
            emoji = LABEL_EMOJI.get(label, "⚪")

            tags_str = " ".join(f"#{e(t).replace(' ','_')}" for t in match_tags[:4])
            gaps_str = " ".join(f"⚠️{e(g)}" for g in gap_tags[:2])
            salary_str = f"\n💰 {e(job['salary_raw'])}" if job.get("salary_raw") else ""

            text = (
                f"{emoji} <b>{e(job['title'])}</b>\n"
                f"🏢 {e(job['company'])} | 📍 {e(job['location'])}\n"
                f"🌐 {e(job['source'])}{salary_str}\n"
                f"📊 Score: <b>{job['score']}/100</b> — {e(label)}\n"
                f"✅ {tags_str}\n"
                f"{gaps_str}\n"
                f"💬 {e(job['reasoning'])}\n"
                f"🔗 <a href='{e(job['url'])}'>View Job</a>"
            )

            markup = {
                "inline_keyboard": [[
                    {"text": "✅ Apply", "callback_data": f"apply_{job['id']}"},
                    {"text": "★ Save", "callback_data": f"later_{job['id']}"},
                    {"text": "✕ Skip", "callback_data": f"skip_{job['id']}"},
                ]]
            }

            if _send_message(text, reply_markup=markup):
                # Stamp notified_at so this job is never sent again
                cur.execute(
                    "UPDATE scored_jobs SET notified_at = datetime('now') WHERE job_id = ?",
                    (job["id"],)
                )
                conn.commit()
                sent += 1

        except Exception as e:
            logger.error(f"Error sending job {job['id']} to Telegram: {e}")

    conn.close()
    logger.success(f"Telegram digest sent: {sent}/{len(jobs)} jobs")
    return sent


def send_application_confirmation(job_id: int, db_path: Path = DB_PATH) -> None:
    """Send confirmation message after successful application."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT title, company, url FROM jobs WHERE id=?", (job_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return

    _send_message(
        f"✅ <b>Applied!</b>\n"
        f"<b>{row['title']}</b> @ {row['company']}\n"
        f"<a href='{row['url']}'>View listing</a>"
    )


def handle_callback_updates(db_path: Path = DB_PATH) -> int:
    """
    Poll Telegram for callback_query updates and write decisions to DB.
    Returns count of decisions processed.
    This is a single-poll (not a long-running loop) for use in cron jobs.
    """
    if not BOT_TOKEN or not CHAT_ID:
        return 0

    try:
        resp = requests.get(
            f"{TG_API}/getUpdates",
            params={"timeout": 5, "allowed_updates": ["callback_query"]},
            timeout=15,
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except Exception as e:
        logger.warning(f"Telegram getUpdates failed: {e}")
        return 0

    if not updates:
        return 0

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    processed = 0
    last_update_id = 0

    for update in updates:
        last_update_id = update.get("update_id", 0)
        cb = update.get("callback_query", {})
        data = cb.get("data", "")
        callback_id = cb.get("id", "")

        if not data:
            continue

        parts = data.split("_", 1)
        if len(parts) != 2:
            continue

        action, job_id_str = parts
        if action not in ("apply", "later", "skip"):
            continue

        try:
            job_id = int(job_id_str)
        except ValueError:
            continue

        decision_map = {"apply": "apply", "later": "later", "skip": "skip"}
        decision = decision_map[action]

        try:
            cur.execute("""
                INSERT OR REPLACE INTO decisions (job_id, decision)
                VALUES (?, ?)
            """, (job_id, decision))
            conn.commit()
            processed += 1
            logger.info(f"Decision recorded: job_id={job_id} → {decision}")

            # Answer callback to remove spinner
            requests.post(
                f"{TG_API}/answerCallbackQuery",
                json={
                    "callback_query_id": callback_id,
                    "text": {"apply": "✅ Queued", "later": "★ Saved", "skip": "✕ Skipped"}[action],
                    "show_alert": False,
                },
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Error processing callback for job {job_id_str}: {e}")

    # Acknowledge processed updates
    if last_update_id:
        try:
            requests.get(
                f"{TG_API}/getUpdates",
                params={"offset": last_update_id + 1, "limit": 1},
                timeout=5,
            )
        except Exception:
            pass

    conn.close()
    return processed
