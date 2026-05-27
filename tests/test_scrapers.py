"""
tests/test_scrapers.py — Unit tests for scrapers (mocked HTTP).
"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    from setup import init_db
    init_db(db_path)
    yield db_path
    db_path.unlink(missing_ok=True)


# ── hh.ru scraper ─────────────────────────────────────────────────────────

def test_hh_scraper_basic(tmp_db):
    mock_search = {
        "items": [
            {
                "id": "123456",
                "name": "Data Scientist",
                "alternate_url": "https://hh.ru/vacancy/123456",
                "employer": {"name": "Test Company"},
                "area": {"name": "Saint Petersburg"},
                "employment": {"name": "Full-time"},
                "published_at": "2025-05-27T10:00:00+0300",
                "snippet": {"requirement": "Python ML experience"},
                "salary": None,
            }
        ]
    }
    mock_detail = {
        "description": "<p>We need a <strong>Data Scientist</strong> with Python.</p>",
        "salary": None,
    }

    with patch("scrapers.hh_scraper.requests.Session") as MockSession:
        session_inst = MagicMock()
        MockSession.return_value = session_inst

        search_resp = MagicMock()
        search_resp.json.return_value = mock_search
        search_resp.raise_for_status = MagicMock()

        detail_resp = MagicMock()
        detail_resp.json.return_value = mock_detail
        detail_resp.raise_for_status = MagicMock()

        session_inst.get.side_effect = [search_resp] + [detail_resp] * 100

        from scrapers.hh_scraper import scrape_hh
        with patch("scrapers.hh_scraper.time.sleep"):
            count = scrape_hh(tmp_db)

    # Should have inserted the vacancy
    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM jobs WHERE source='hh.ru'")
    n = cur.fetchone()[0]
    conn.close()
    assert n >= 1, "Expected at least 1 hh.ru job inserted"


def test_hh_scraper_deduplication(tmp_db):
    """Second run should not insert duplicates."""
    from scrapers.hh_scraper import _url_hash, _save_job
    import sqlite3

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()

    job = {
        "url_hash": _url_hash("https://hh.ru/vacancy/999"),
        "source": "hh.ru",
        "title": "ML Engineer",
        "company": "Acme",
        "location": "Moscow",
        "country": "Russia",
        "salary_raw": "",
        "job_type": "",
        "language": "ru",
        "description": "test",
        "url": "https://hh.ru/vacancy/999",
        "posted_at": "",
    }

    result1 = _save_job(cur, job)
    conn.commit()
    result2 = _save_job(cur, job)
    conn.commit()
    conn.close()

    assert result1 is True
    assert result2 is False


# ── Africa scraper ────────────────────────────────────────────────────────

def test_africa_scraper_graceful_failure(tmp_db):
    """Africa scraper should return 0 and not crash if all sources fail."""
    with patch("scrapers.africa_scraper.requests.Session") as MockSession:
        session_inst = MagicMock()
        MockSession.return_value = session_inst
        session_inst.get.side_effect = Exception("Network error")

        from scrapers.africa_scraper import scrape_africa
        with patch("scrapers.africa_scraper.time.sleep"):
            count = scrape_africa(tmp_db)

    assert count == 0


# ── RemoteOK scraper ──────────────────────────────────────────────────────

def test_remoteok_scraper_basic(tmp_db):
    mock_data = [
        {"slug": "metadata"},  # first item is metadata
        {
            "id": "abc123",
            "position": "Python Data Scientist",
            "company": "Remote Corp",
            "url": "https://remoteok.com/jobs/abc123",
            "description": "We are hiring a data scientist.",
            "date": "2025-05-27T00:00:00Z",
            "salary_min": 80000,
            "salary_max": 120000,
        }
    ]

    with patch("scrapers.europe_scraper.requests.Session") as MockSession:
        session_inst = MagicMock()
        MockSession.return_value = session_inst

        resp = MagicMock()
        resp.json.return_value = mock_data
        resp.raise_for_status = MagicMock()

        bs_resp = MagicMock()
        bs_resp.text = "<html><body></body></html>"
        bs_resp.raise_for_status = MagicMock()

        session_inst.get.return_value = resp

        from scrapers.europe_scraper import scrape_europe
        with patch("scrapers.europe_scraper.time.sleep"):
            count = scrape_europe(tmp_db)

    conn = sqlite3.connect(tmp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM jobs WHERE source='remoteok'")
    n = cur.fetchone()[0]
    conn.close()
    assert n >= 1
