"""
applicator/email_submitter.py — Send job applications via Gmail.
"""
import mimetypes
import os
import re
import sqlite3
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")


def _extract_email(text: str) -> str:
    """Find first email address in a block of text."""
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else ""


def send_email_application(
    job_id: int,
    cv_path: Path,
    cover_path: Path,
    db_path: Path = DB_PATH,
) -> bool:
    """
    Send a job application email with CV attached.
    Returns True on success.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        logger.error("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in .env")
        return False

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT j.title, j.company, j.description, s.cover_letter
        FROM jobs j
        LEFT JOIN scored_jobs s ON s.job_id = j.id
        WHERE j.id = ?
    """, (job_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        logger.error(f"Job {job_id} not found")
        return False

    title = row["title"]
    company = row["company"]
    description = row["description"] or ""
    cover_text = row["cover_letter"] or (
        f"Dear Hiring Team,\n\nI am applying for the {title} position at {company}.\n\n"
        "Best regards,\nDanaya Diarra"
    )

    # Try to extract recruiter email from description
    to_email = _extract_email(description)
    if not to_email:
        logger.warning(f"  No recruiter email found in job {job_id} description — skipping email send")
        return False

    subject = f"Application: {title} — Danaya Diarra"

    # Build MIME message
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(cover_text, "plain", "utf-8"))

    # Attach CV PDF if exists
    if cv_path.exists():
        with open(cv_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=cv_path.name)
        part["Content-Disposition"] = f'attachment; filename="{cv_path.name}"'
        msg.attach(part)

    # Send via Gmail SMTP with App Password
    try:
        import smtplib
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, to_email, msg.as_string())

        logger.success(f"  Email sent to {to_email} for job {job_id} ({title} @ {company})")
        return True

    except Exception as e:
        logger.error(f"  Email send failed for job {job_id}: {e}")
        return False
