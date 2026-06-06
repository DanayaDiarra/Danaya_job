"""
setup.py — Initialise SQLite database schema.
Run once before first use: python setup.py
"""
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
Path("data/applications").mkdir(parents=True, exist_ok=True)


def init_db(db_path: Path = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS jobs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url_hash    TEXT    UNIQUE NOT NULL,
        source      TEXT    NOT NULL,
        title       TEXT    NOT NULL,
        company     TEXT,
        location    TEXT,
        country     TEXT,
        salary_raw  TEXT,
        job_type    TEXT,
        language    TEXT    DEFAULT 'en',
        description TEXT,
        url         TEXT,
        posted_at   TEXT,
        scraped_at  TEXT    DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS scored_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        score           INTEGER NOT NULL,
        score_label     TEXT,
        match_tags      TEXT,
        gap_tags        TEXT,
        reasoning       TEXT,
        tailored_cv     TEXT,
        cover_letter    TEXT,
        cover_language  TEXT    DEFAULT 'en',
        scored_at       TEXT    DEFAULT (datetime('now')),
        notified_at     TEXT    DEFAULT NULL
    );

    CREATE TABLE IF NOT EXISTS decisions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id      INTEGER UNIQUE NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        decision    TEXT    NOT NULL CHECK(decision IN ('apply','skip','later')),
        decided_at  TEXT    DEFAULT (datetime('now')),
        notes       TEXT
    );

    CREATE TABLE IF NOT EXISTS applications (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        method       TEXT    NOT NULL CHECK(method IN ('easy_apply','email','portal','manual')),
        status       TEXT    NOT NULL DEFAULT 'sent'
                             CHECK(status IN ('sent','viewed','interview','rejected','offer')),
        applied_at   TEXT    DEFAULT (datetime('now')),
        last_updated TEXT    DEFAULT (datetime('now')),
        cv_path      TEXT,
        notes        TEXT
    );

    CREATE TABLE IF NOT EXISTS candidate_profile (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        filename    TEXT,
        cv_text     TEXT    NOT NULL,
        uploaded_at TEXT    DEFAULT (datetime('now')),
        is_active   INTEGER DEFAULT 1
    );

    CREATE INDEX IF NOT EXISTS idx_jobs_source   ON jobs(source);
    CREATE INDEX IF NOT EXISTS idx_jobs_scraped  ON jobs(scraped_at);
    CREATE INDEX IF NOT EXISTS idx_scored_score  ON scored_jobs(score);
    CREATE INDEX IF NOT EXISTS idx_decisions_job ON decisions(job_id);
    """)

    # Migrations: add columns / tables introduced after initial schema
    migrations = [
        "ALTER TABLE scored_jobs ADD COLUMN notified_at TEXT DEFAULT NULL",
        """CREATE TABLE IF NOT EXISTS candidate_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            cv_text TEXT NOT NULL,
            uploaded_at TEXT DEFAULT (datetime('now')),
            is_active INTEGER DEFAULT 1
        )""",
    ]
    for sql in migrations:
        try:
            cur.execute(sql)
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.commit()
    conn.close()
    print(f"Database initialised at {db_path.resolve()}")


if __name__ == "__main__":
    init_db()
