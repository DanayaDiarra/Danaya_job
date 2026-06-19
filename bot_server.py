"""
bot_server.py — Always-on Telegram webhook server. Deploy on Render (free tier).

Conversation flow:
  /start  → welcome message + ask for CV
  /help   → show available commands
  /status → show pipeline status
  <PDF/DOCX sent> → extract CV → trigger GitHub Actions → send matches in ~3 min
  <Apply/Save/Skip button> → instant ack + queue decision in repo

Environment variables (set in Render dashboard):
  TELEGRAM_BOT_TOKEN   your bot token
  TELEGRAM_CHAT_ID     your Telegram user/chat ID
  GITHUB_PAT           Fine-grained PAT (Contents r/w + Actions w)
  GITHUB_REPO          e.g. DanayaDiarra/Danaya_job
  RENDER_EXTERNAL_URL  injected automatically by Render
"""
import base64
import os

import requests
from fastapi import FastAPI, Request, Response
from loguru import logger

from applicator.cv_listener import extract_cv_text, _download_file as _tg_download
from applicator.github_api import trigger_cv_workflow, queue_decision

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TG_API    = f"https://api.telegram.org/bot{BOT_TOKEN}"
REPO_URL  = f"https://github.com/{os.getenv('GITHUB_REPO', 'DanayaDiarra/Danaya_job')}"

DECISION_LABELS = {
    "apply": "✅ Queued for application",
    "later": "★ Saved for later",
    "skip":  "✕ Skipped",
}

app = FastAPI(title="Job Agent Bot")


# ── Telegram helpers ───────────────────────────────────────────────────────────

def _send(text: str, markup: dict = None) -> None:
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if markup:
        payload["reply_markup"] = markup
    try:
        requests.post(f"{TG_API}/sendMessage", json=payload, timeout=10)
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


# ── Command handlers ───────────────────────────────────────────────────────────

def _handle_start() -> None:
    _send(
        "👋 <b>Welcome to your Job Agent!</b>\n\n"
        "I scrape 15+ job boards every 6 hours, score listings against "
        "your profile, and send you the best matches — right here.\n\n"
        "📄 <b>To get started, send me your CV</b> (PDF or DOCX).\n"
        "I'll instantly score the current job listings against it and "
        "send you your top matches with one-tap Apply / Save / Skip buttons.\n\n"
        "You can update your CV anytime by sending a new file.",
        markup={
            "keyboard": [
                [{"text": "📊 Status"}, {"text": "❓ Help"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
    )


def _handle_help() -> None:
    _send(
        "🤖 <b>Job Agent — Commands</b>\n\n"
        "📄 <b>Send CV</b> (PDF/DOCX) — Update your profile and get instant job matches\n"
        "📊 /status — Check when the agent last ran\n"
        "❓ /help — Show this message\n\n"
        "<b>Job card buttons:</b>\n"
        "✅ <b>Apply</b> — Queue job for automatic application\n"
        "★ <b>Save</b> — Save for later review\n"
        "✕ <b>Skip</b> — Hide this job\n\n"
        f"🔗 <a href='{REPO_URL}/actions'>View pipeline runs →</a>"
    )


def _handle_status() -> None:
    """Fetch latest GitHub Actions run info and report back."""
    pat = os.getenv("GITHUB_PAT", "")
    repo = os.getenv("GITHUB_REPO", "DanayaDiarra/Danaya_job")

    if not pat:
        _send("⚠️ GITHUB_PAT not set — can't check pipeline status.")
        return

    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/runs?per_page=1",
            headers={
                "Authorization": f"token {pat}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=10,
        )
        runs = resp.json().get("workflow_runs", [])
        if not runs:
            _send("No workflow runs found yet.")
            return

        run = runs[0]
        status     = run.get("status", "?")       # queued / in_progress / completed
        conclusion = run.get("conclusion", "?")   # success / failure / None
        created_at = run.get("created_at", "")[:16].replace("T", " ")
        run_url    = run.get("html_url", REPO_URL)

        if status == "completed":
            icon = "✅" if conclusion == "success" else "❌"
            status_str = f"{icon} {conclusion}"
        elif status == "in_progress":
            status_str = "⏳ running now"
        else:
            status_str = f"🕐 {status}"

        _send(
            f"📊 <b>Last pipeline run</b>\n"
            f"Status: {status_str}\n"
            f"Started: {created_at} UTC\n"
            f"🔗 <a href='{run_url}'>View logs →</a>"
        )
    except Exception as e:
        _send(f"⚠️ Could not fetch status: {e}")


def _handle_unknown_text(text: str) -> None:
    _send(
        "I didn't understand that.\n\n"
        "📄 Send your <b>CV as a PDF or DOCX</b> to get job matches,\n"
        "or type /help to see available commands."
    )


# ── Document handler ───────────────────────────────────────────────────────────

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
        _send(
            "That file type isn't supported.\n"
            "Please send your CV as a <b>PDF</b> or <b>DOCX</b> file."
        )
        return

    _send(
        f"📄 <b>{name}</b> received!\n"
        "⏳ Scoring the latest job listings against your CV…\n"
        "<i>Expect your top matches in about 3–5 minutes.</i>"
    )

    # Download from Telegram
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
            "Please send a <b>text-based</b> PDF or DOCX — "
            "scanned image files are not supported."
        )
        return

    logger.info(f"CV extracted: {len(cv_text)} chars from {filename}")

    # Encode and trigger GitHub Actions workflow (score-only run)
    cv_b64 = base64.b64encode(cv_text.encode()).decode()
    if trigger_cv_workflow(cv_b64):
        _send(
            "🚀 <b>Scoring started!</b>\n"
            "I'll send your top job matches here as soon as the run finishes.\n"
            f"🔗 <a href='{REPO_URL}/actions'>Watch it live →</a>"
        )
    else:
        _send(
            "⚠️ Could not start the scoring run automatically.\n"
            f"Please trigger it manually: <a href='{REPO_URL}/actions'>GitHub Actions →</a>"
        )


# ── Callback handler ───────────────────────────────────────────────────────────

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

    # Acknowledge immediately — removes the spinner from the button
    _answer_callback(callback_id, DECISION_LABELS[action])

    # Write decision to repo so next Actions run picks it up
    queue_decision(job_id, action)


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Telegram pushes every update here instantly."""
    try:
        update = await request.json()
    except Exception:
        return Response(status_code=400)

    try:
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        doc  = msg.get("document")

        # ── Text / command messages
        if text:
            cmd = text.split()[0].split("@")[0].lower()  # handle /cmd@botname
            if cmd in ("/start", "start"):
                _handle_start()
            elif cmd in ("/help", "❓ help", "help"):
                _handle_help()
            elif cmd in ("/status", "📊 status", "status"):
                _handle_status()
            else:
                _handle_unknown_text(text)

        # ── Document (CV upload)
        elif doc:
            _handle_document(doc)

        # ── Inline button callback
        cb = update.get("callback_query")
        if cb:
            _handle_callback(cb)

    except Exception as e:
        logger.error(f"Unhandled webhook error: {e}")

    return {"ok": True}


@app.get("/")
def health():
    return {"status": "ok", "service": "job-agent-bot"}


# ── Startup: auto-register webhook with Telegram ───────────────────────────────

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
            json={
                "url": webhook_url,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=10,
        )
        if resp.ok and resp.json().get("ok"):
            logger.success(f"Telegram webhook registered: {webhook_url}")
        else:
            logger.error(f"Webhook registration failed: {resp.text}")
    except Exception as e:
        logger.error(f"Webhook registration error: {e}")
