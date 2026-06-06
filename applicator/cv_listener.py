"""
applicator/cv_listener.py — Receive CV via Telegram, score jobs instantly.

Flow:
  1. Poll Telegram getUpdates for document messages (PDF or DOCX)
  2. Download the file via Telegram File API
  3. Extract plain text (pdfminer for PDF, python-docx for DOCX)
  4. Save to candidate_profile table (deactivates previous profile)
  5. Clear all existing scores so they are re-scored with the new CV
  6. Score the top 30 most-recent jobs immediately
  7. Send matches back to Telegram
"""
import io
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Score this many existing jobs immediately when a CV is uploaded
INSTANT_SCORE_LIMIT = 30


# ── Text extraction ────────────────────────────────────────────────────────────

def _extract_pdf(data: bytes) -> str:
    from pdfminer.high_level import extract_text_to_fp
    from pdfminer.layout import LAParams
    output = io.StringIO()
    extract_text_to_fp(io.BytesIO(data), output, laparams=LAParams())
    return output.getvalue().strip()


def _extract_docx(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_cv_text(data: bytes, filename: str) -> Optional[str]:
    """Extract plain text from PDF or DOCX bytes."""
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            return _extract_pdf(data)
        elif ext in (".docx", ".doc"):
            return _extract_docx(data)
        else:
            logger.warning(f"Unsupported CV format: {ext}")
            return None
    except Exception as e:
        logger.error(f"CV text extraction failed ({filename}): {e}")
        return None


# ── Telegram helpers ───────────────────────────────────────────────────────────

def _tg_get(endpoint: str, params: dict = None) -> Optional[dict]:
    try:
        resp = requests.get(f"{TG_API}/{endpoint}", params=params, timeout=15)
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.warning(f"Telegram GET {endpoint} failed: {e}")
    return None


def _tg_post(endpoint: str, payload: dict) -> bool:
    try:
        resp = requests.post(f"{TG_API}/{endpoint}", json=payload, timeout=15)
        return resp.ok
    except Exception:
        return False


def _send(text: str) -> None:
    _tg_post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def _download_file(file_id: str) -> Optional[tuple[bytes, str]]:
    """Download a Telegram file by file_id. Returns (bytes, filename)."""
    info = _tg_get("getFile", {"file_id": file_id})
    if not info or not info.get("ok"):
        return None
    file_path = info["result"]["file_path"]
    filename = Path(file_path).name
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content, filename
    except Exception as e:
        logger.error(f"File download failed: {e}")
        return None


# ── DB helpers ─────────────────────────────────────────────────────────────────

def _save_profile(cv_text: str, filename: str, db_path: Path) -> None:
    """Deactivate old profiles, insert new active one."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE candidate_profile SET is_active = 0")
    cur.execute(
        "INSERT INTO candidate_profile (filename, cv_text, is_active) VALUES (?, ?, 1)",
        (filename, cv_text),
    )
    conn.commit()
    conn.close()


def _reset_scores(db_path: Path) -> int:
    """
    Delete all scored_jobs rows so every job is re-scored with the new CV.
    Also resets notified_at so updated matches get sent again.
    Returns number of rows cleared.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM scored_jobs")
    n = cur.fetchone()[0]
    cur.execute("DELETE FROM scored_jobs")
    conn.commit()
    conn.close()
    return n


# ── Instant scoring ────────────────────────────────────────────────────────────

def _score_and_send(cv_text: str, db_path: Path) -> int:
    """Score the most recent INSTANT_SCORE_LIMIT jobs and send top matches."""
    from scorer.scorer import score_job, _get_client
    from scorer.prompts import build_system_prompt
    import html as html_mod

    client = _get_client()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Pick the most recent jobs (freshest listings)
    cur.execute("""
        SELECT * FROM jobs
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (INSTANT_SCORE_LIMIT,))
    jobs = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not jobs:
        _send("📭 No jobs in the database yet. The agent will scrape new listings on the next run.")
        return 0

    _send(f"⏳ Scoring <b>{len(jobs)}</b> recent jobs against your CV… this takes a minute.")

    results = []
    for job in jobs:
        result = score_job(job, client, profile=cv_text)
        time.sleep(0.5)
        if result is None:
            continue
        score = int(result.get("score", 0))
        if score >= SCORE_THRESHOLD:
            results.append((score, job, result))

    results.sort(key=lambda x: x[0], reverse=True)

    # Persist scores
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for score, job, result in results:
        try:
            cur.execute("""
                INSERT OR REPLACE INTO scored_jobs
                  (job_id, score, score_label, match_tags, gap_tags,
                   reasoning, tailored_cv, cover_letter, cover_language)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job["id"], score,
                result.get("score_label", ""),
                json.dumps(result.get("match_tags", []), ensure_ascii=False),
                json.dumps(result.get("gap_tags", []), ensure_ascii=False),
                result.get("reasoning", ""),
                result.get("tailored_summary", ""),
                result.get("cover_letter", ""),
                result.get("cover_language", "en"),
            ))
        except sqlite3.Error as e:
            logger.error(f"DB error saving CV-triggered score: {e}")
    conn.commit()
    conn.close()

    if not results:
        _send(f"🔍 No matches above score {SCORE_THRESHOLD} in the current listings.\nNew jobs will be scored as they're scraped.")
        return 0

    def e(v):
        return html_mod.escape(str(v or ""))

    LABEL_EMOJI = {"Excellent Match": "🟢", "Good Match": "🔵",
                   "Partial Match": "🟡", "Poor Match": "🔴"}

    _send(f"✅ <b>{len(results)} match{'es' if len(results)>1 else ''} found!</b> Top results:")

    sent = 0
    for score, job, result in results[:10]:
        label = result.get("score_label", "")
        emoji = LABEL_EMOJI.get(label, "⚪")
        match_tags = result.get("match_tags", [])
        gap_tags = result.get("gap_tags", [])
        salary_str = f"\n💰 {e(job['salary_raw'])}" if job.get("salary_raw") else ""
        tags_str = " ".join(f"#{e(t).replace(' ','_')}" for t in match_tags[:4])
        gaps_str = " ".join(f"⚠️{e(g)}" for g in gap_tags[:2])

        text = (
            f"{emoji} <b>{e(job['title'])}</b>\n"
            f"🏢 {e(job['company'])} | 📍 {e(job['location'])}\n"
            f"🌐 {e(job['source'])}{salary_str}\n"
            f"📊 Score: <b>{score}/100</b> — {e(label)}\n"
            f"✅ {tags_str}\n"
            f"{gaps_str}\n"
            f"💬 {e(result.get('reasoning',''))}\n"
            f"🔗 <a href='{e(job['url'])}'>View Job</a>"
        )
        markup = {
            "inline_keyboard": [[
                {"text": "✅ Apply", "callback_data": f"apply_{job['id']}"},
                {"text": "★ Save",  "callback_data": f"later_{job['id']}"},
                {"text": "✕ Skip",  "callback_data": f"skip_{job['id']}"},
            ]]
        }
        if _tg_post("sendMessage", {
            "chat_id": CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": markup,
        }):
            # Mark as notified so the regular digest doesn't resend
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE scored_jobs SET notified_at = datetime('now') WHERE job_id = ?",
                (job["id"],)
            )
            conn.commit()
            conn.close()
            sent += 1
        time.sleep(0.3)

    return sent


# ── Main entry ─────────────────────────────────────────────────────────────────

def check_for_cv_upload(db_path: Path = DB_PATH) -> bool:
    """
    Poll Telegram for pending CV document uploads.
    Processes the most recent unread CV if found.
    Returns True if a CV was processed.
    """
    if not BOT_TOKEN or not CHAT_ID:
        return False

    result = _tg_get("getUpdates", {
        "timeout": 5,
        "allowed_updates": ["message"],
    })
    if not result or not result.get("ok"):
        return False

    updates = result.get("result", [])
    cv_updates = []

    for update in updates:
        msg = update.get("message", {})
        doc = msg.get("document")
        if not doc:
            continue
        mime = doc.get("mime_type", "")
        name = doc.get("file_name", "")
        if mime in ("application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") \
                or name.lower().endswith((".pdf", ".docx")):
            cv_updates.append((update["update_id"], doc))

    if not cv_updates:
        return False

    # Process the most recent CV only
    update_id, doc = cv_updates[-1]
    filename = doc.get("file_name", "cv.pdf")
    file_id = doc["file_id"]

    logger.info(f"CV upload detected: {filename}")
    _send(f"📄 CV received: <b>{filename}</b>\nAnalysing and matching jobs…")

    # Download
    downloaded = _download_file(file_id)
    if not downloaded:
        _send("❌ Could not download the file. Please try again.")
        return False
    data, filename = downloaded

    # Extract text
    cv_text = extract_cv_text(data, filename)
    if not cv_text or len(cv_text.strip()) < 50:
        _send("❌ Could not read text from the file. Please send a text-based PDF or DOCX (not a scanned image).")
        return False

    logger.info(f"CV text extracted: {len(cv_text)} chars")

    # Save profile + reset old scores
    _save_profile(cv_text, filename, db_path)
    cleared = _reset_scores(db_path)
    logger.info(f"Profile saved. Cleared {cleared} old scores.")

    # Score and send
    sent = _score_and_send(cv_text, db_path)
    logger.success(f"CV-triggered scoring complete: {sent} matches sent")

    # Acknowledge all CV updates so they don't replay
    _tg_get("getUpdates", {"offset": update_id + 1, "limit": 1})

    return True
