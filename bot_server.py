"""
bot_server.py — Always-on Telegram webhook server. Deploy on Render (free tier).

What it does:
  • Receives every Telegram update the moment it arrives (webhook, not polling)
  • CV document  → extract text → trigger GitHub Actions score-only run
  • Apply/Save/Skip button → acknowledge immediately + queue decision in repo

Environment variables (set in Render dashboard):
  TELEGRAM_BOT_TOKEN   your bot token
  TELEGRAM_CHAT_ID     your Telegram user/chat ID
  GITHUB_PAT           Personal Access Token (repo + workflow scope)
  GITHUB_REPO          e.g. DanayaDiarra/Danaya_job
  GROQ_API_KEY         not used here but kept for parity
  RENDER_EXTERNAL_URL  injected automatically by Render

Setup (one-time):
  After deploying, register the webhook:
    python scripts/register_webhook.py
  Or manually:
    curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
         -d "url=https://<your-app>.onrender.com/webhook"
"""
import base64
import os

import requests
from fastapi import FastAPI, Request, Response
from loguru import logger

from applicator.cv_listener import extract_cv_text, _download_file as _tg_download
from applicator.github_api import trigger_cv_workflow, queue_decision

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
TG_API     = f"https://api.telegram.org/bot{BOT_TOKEN}"

LABEL_EMOJI = {
    "apply": "✅ Queued for application",
    "later": "★ Saved for later",
    "skip":  "✕ Skipped",
}

app = FastAPI(title="Job Agent Bot")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _send(text: str) -> None:
    try:
        requests.post(f"{TG_API}/sendMessage", json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
    except Exception as e:
        logger.warning(f"_send failed: {e}")


def _answer_callback(callback_id: str, text: str) -> None:
    try:
        requests.post(f"{TG_API}/answerCallbackQuery", json={
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        }, timeout=5)
    except Exception:
        pass


# ── Update handlers ────────────────────────────────────────────────────────────

def _handle_document(doc: dict) -> None:
    """User sent a document — check if it's a CV and process it."""
    mime = doc.get("mime_type", "")
    name = doc.get("file_name", "cv")

    is_cv = (
        mime in (
            "application/pdf",
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document",
        )
        or name.lower().endswith((".pdf", ".docx", ".doc"))
    )
    if not is_cv:
        _send("Please send your CV as a <b>PDF</b> or <b>DOCX</b> file.")
        return

    _send(f"📄 <b>{name}</b> received!\n⏳ Scoring jobs against your CV… expect results in ~3–5 min.")

    # Download
    result = _tg_download(doc["file_id"])
    if result is None:
        _send("❌ Could not download the file. Please try again.")
        return
    data, filename = result

    # Extract text
    cv_text = extract_cv_text(data, filename)
    if not cv_text or len(cv_text.strip()) < 50:
        _send(
            "❌ Could not read text from the file.\n"
            "Please send a <b>text-based</b> PDF or DOCX (not a scanned image)."
        )
        return

    logger.info(f"CV extracted: {len(cv_text)} chars from {filename}")

    # Encode and trigger GitHub Actions
    cv_b64 = base64.b64encode(cv_text.encode()).decode()
    if trigger_cv_workflow(cv_b64):
        _send(
            "🚀 Workflow started! I'll send your top matches here as soon as scoring finishes.\n"
            "<i>(Usually takes 3–5 minutes)</i>"
        )
    else:
        _send(
            "⚠️ Could not start the scoring workflow automatically.\n"
            "Please trigger it manually: "
            "https://github.com/DanayaDiarra/Danaya_job/actions"
        )


def _handle_callback(cb: dict) -> None:
    """User tapped Apply / Save / Skip on a job card."""
    data        = cb.get("data", "")
    callback_id = cb.get("id", "")

    parts = data.split("_", 1)
    if len(parts) != 2 or parts[0] not in ("apply", "later", "skip"):
        return

    action, job_id_str = parts
    try:
        job_id = int(job_id_str)
    except ValueError:
        return

    # Answer immediately to dismiss the spinner
    _answer_callback(callback_id, LABEL_EMOJI[action])

    # Persist the decision in the repo so the next Actions run applies it
    queue_decision(job_id, action)


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Telegram calls this URL for every update."""
    try:
        update = await request.json()
    except Exception:
        return Response(status_code=400)

    try:
        # Document message (CV upload)
        msg = update.get("message", {})
        doc = msg.get("document")
        if doc:
            _handle_document(doc)

        # Inline button callback
        cb = update.get("callback_query")
        if cb:
            _handle_callback(cb)

    except Exception as e:
        logger.error(f"Unhandled error in webhook: {e}")

    # Always return 200 so Telegram doesn't retry
    return {"ok": True}


@app.get("/")
def health():
    return {"status": "ok", "service": "job-agent-bot"}


# ── Startup: auto-register webhook ────────────────────────────────────────────

@app.on_event("startup")
def register_webhook():
    render_url = os.getenv("RENDER_EXTERNAL_URL", "")
    if not render_url or not BOT_TOKEN:
        logger.warning("RENDER_EXTERNAL_URL or BOT_TOKEN not set — skipping webhook registration")
        return
    webhook_url = f"{render_url.rstrip('/')}/webhook"
    try:
        resp = requests.post(
            f"{TG_API}/setWebhook",
            json={"url": webhook_url, "allowed_updates": ["message", "callback_query"]},
            timeout=10,
        )
        if resp.ok and resp.json().get("ok"):
            logger.success(f"Telegram webhook registered: {webhook_url}")
        else:
            logger.error(f"Webhook registration failed: {resp.text}")
    except Exception as e:
        logger.error(f"Webhook registration error: {e}")
