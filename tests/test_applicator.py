"""
tests/test_applicator.py — Unit tests for applicator modules.
"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    from setup import init_db
    init_db(db_path)
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def job_in_db(tmp_db):
    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO jobs (url_hash, source, title, company, location, country,
                          description, url, language)
        VALUES ('hash001', 'hh.ru', 'Data Scientist', 'Acme Corp',
                'SPb', 'Russia', 'Python ML', 'https://hh.ru/1', 'en')
    """)
    job_id = cur.lastrowid
    cur.execute("""
        INSERT INTO scored_jobs (job_id, score, score_label, tailored_cv,
                                  cover_letter, cover_language)
        VALUES (?, 82, 'Good Match',
                'Experienced data scientist with Python and ML expertise.',
                'Dear Hiring Team, I am applying for this role.', 'en')
    """, (job_id,))
    conn.commit()
    conn.close()
    return job_id, tmp_db


# ── Cover letter ──────────────────────────────────────────────────────────

def test_cover_letter_generation(job_in_db, tmp_path):
    job_id, db_path = job_in_db

    with patch("applicator.cover_letter.OUTPUT_DIR", tmp_path):
        from applicator.cover_letter import generate_cover_letter_file
        path = generate_cover_letter_file(job_id, db_path)

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Dear Hiring Team" in content
    assert "Danaya Diarra" in content
    assert "I am applying for this role." in content


def test_cover_letter_russian(job_in_db, tmp_path):
    job_id, db_path = job_in_db

    # Update to Russian
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE scored_jobs SET cover_language='ru', cover_letter='Прошу рассмотреть мою кандидатуру.' WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()

    with patch("applicator.cover_letter.OUTPUT_DIR", tmp_path):
        from applicator.cover_letter import generate_cover_letter_file
        path = generate_cover_letter_file(job_id, db_path)

    content = path.read_text(encoding="utf-8")
    assert "Уважаемая команда" in content
    assert "С уважением" in content


# ── Telegram bot ──────────────────────────────────────────────────────────

def test_telegram_digest_no_token(tmp_db):
    with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
        from applicator.telegram_bot import send_daily_digest
        count = send_daily_digest(tmp_db)
    assert count == 0


def test_telegram_sends_digest(job_in_db):
    job_id, db_path = job_in_db

    # Add a decision-free high-score job
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE scored_jobs SET score=85 WHERE job_id=?", (job_id,))
    conn.commit()
    conn.close()

    with patch("applicator.telegram_bot.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with patch.dict("os.environ", {
            "TELEGRAM_BOT_TOKEN": "fake_token",
            "TELEGRAM_CHAT_ID": "12345",
        }):
            from applicator.telegram_bot import send_daily_digest
            count = send_daily_digest(db_path)

    assert count >= 1
    assert mock_post.called


# ── Email extractor ───────────────────────────────────────────────────────

def test_email_extraction():
    from applicator.email_submitter import _extract_email

    text = "Send your CV to jobs@techcorp.com or hr@company.org"
    assert _extract_email(text) == "jobs@techcorp.com"

    text_no_email = "Apply on our website at www.techcorp.com"
    assert _extract_email(text_no_email) == ""


def test_email_submit_no_credentials(job_in_db, tmp_path):
    job_id, db_path = job_in_db
    with patch.dict("os.environ", {"GMAIL_ADDRESS": "", "GMAIL_APP_PASSWORD": ""}):
        from applicator.email_submitter import send_email_application
        result = send_email_application(job_id, tmp_path / "cv.pdf", tmp_path / "cover.txt", db_path)
    assert result is False
