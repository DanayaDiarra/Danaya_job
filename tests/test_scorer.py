"""
tests/test_scorer.py — Unit tests for the scorer module.
"""
import json
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
def sample_job():
    return {
        "id": 1,
        "title": "Data Scientist",
        "company": "Tech Company",
        "location": "Saint Petersburg",
        "country": "Russia",
        "source": "hh.ru",
        "description": "Python, machine learning, deep learning required. "
                       "Experience with PyTorch or TensorFlow preferred.",
    }


MOCK_CLAUDE_RESPONSE = json.dumps({
    "score": 85,
    "score_label": "Good Match",
    "match_tags": ["Python", "Machine Learning", "PyTorch", "Data Science"],
    "gap_tags": ["TensorFlow experience"],
    "reasoning": "Strong match on technical skills. Minor gap on TensorFlow.",
    "worth_applying": True,
    "tailored_summary": "Data scientist with Python and PyTorch expertise...",
    "cover_letter": "Dear Hiring Team, I am excited to apply...",
    "cover_language": "en",
})


def test_score_job_success(sample_job):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE)]
    mock_client.messages.create.return_value = mock_response

    from scorer.scorer import score_job
    result = score_job(sample_job, mock_client)

    assert result is not None
    assert result["score"] == 85
    assert result["score_label"] == "Good Match"
    assert len(result["match_tags"]) >= 3
    assert result["worth_applying"] is True


def test_score_job_strips_markdown_fences(sample_job):
    wrapped = f"```json\n{MOCK_CLAUDE_RESPONSE}\n```"
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=wrapped)]
    mock_client.messages.create.return_value = mock_response

    from scorer.scorer import score_job
    result = score_job(sample_job, mock_client)

    assert result is not None
    assert result["score"] == 85


def test_score_job_returns_none_on_bad_json(sample_job):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="this is not json at all")]
    mock_client.messages.create.return_value = mock_response

    from scorer.scorer import score_job
    result = score_job(sample_job, mock_client)

    assert result is None


def test_score_all_pending_writes_to_db(tmp_db):
    # Insert a sample job
    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO jobs (url_hash, source, title, company, location, country,
                          description, url)
        VALUES ('abc123', 'hh.ru', 'ML Engineer', 'Corp', 'SPb', 'Russia',
                'Python ML required', 'https://hh.ru/1')
    """)
    conn.commit()
    conn.close()

    with patch("scorer.scorer._get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=MOCK_CLAUDE_RESPONSE)]
        mock_client.messages.create.return_value = mock_response

        from scorer.scorer import score_all_pending
        with patch("scorer.scorer.time.sleep"):
            count = score_all_pending(tmp_db)

    assert count == 1

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT score, score_label FROM scored_jobs")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 85
    assert row[1] == "Good Match"


def test_scorer_missing_api_key():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
        from scorer.scorer import _get_client
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            _get_client()
