"""
applicator/cover_letter.py — Generate cover letter files from DB content.
"""
import os
import re
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
OUTPUT_DIR = Path("data/applications")

SALUTATIONS = {
    "ru": "Уважаемая команда {company},",
    "fr": "Madame, Monsieur,",
    "en": "Dear Hiring Team,",
}

SIGN_OFFS = {
    "ru": "С уважением,\nДаная Диарра\ndiarradanaya5544@gmail.com | +7-952-217-0325",
    "fr": "Cordialement,\nDanaya Diarra\ndiarradanaya5544@gmail.com | +7-952-217-0325",
    "en": "Best regards,\nDanaya Diarra\ndiarradanaya5544@gmail.com | +7-952-217-0325",
}


def _slugify(text: str) -> str:
    return re.sub(r"[^\w]+", "_", text.lower()).strip("_")[:40]


def generate_cover_letter_file(job_id: int, db_path: Path = DB_PATH) -> Path:
    """
    Load cover letter from scored_jobs, wrap with salutation + sign-off,
    save as .txt file.
    Returns path to the saved file.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT j.title, j.company, s.cover_letter, s.cover_language
        FROM jobs j
        LEFT JOIN scored_jobs s ON s.job_id = j.id
        WHERE j.id = ?
    """, (job_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        raise ValueError(f"Job {job_id} not found")

    title = row["title"] or "position"
    company = row["company"] or "the company"
    body = row["cover_letter"] or ""
    lang = row["cover_language"] or "en"

    # Build full letter
    salutation = SALUTATIONS[lang].format(company=company)
    sign_off = SIGN_OFFS[lang]
    full_letter = f"{salutation}\n\n{body}\n\n{sign_off}"

    slug = _slugify(f"{company}_{title}")
    out_path = OUTPUT_DIR / f"cover_{job_id}_{lang}_{slug}.txt"
    out_path.write_text(full_letter, encoding="utf-8")

    logger.info(f"Cover letter saved: {out_path}")
    return out_path
