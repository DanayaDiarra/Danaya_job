"""
scorer/scorer.py — Score jobs against Danaya's profile using Groq API (free).
Model: llama-3.3-70b-versatile (free tier: 14,400 req/day)
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

from .prompts import build_system_prompt, USER_PROMPT_TEMPLATE

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))
GROQ_MODEL = "llama-3.3-70b-versatile"
RATE_LIMIT_DELAY = 0.5  # Groq free tier: very generous limits


def _get_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Get a free key at: https://console.groq.com\n"
            "Add it to your .env file as: GROQ_API_KEY=gsk_..."
        )
    try:
        from groq import Groq
        return Groq(api_key=api_key)
    except ImportError:
        raise RuntimeError(
            "groq package not installed. Run: pip install groq"
        )


def _clean_json_response(text: str) -> str:
    """Strip markdown fences and whitespace."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def get_active_profile(db_path: Path = DB_PATH) -> Optional[str]:
    """Return the most recently uploaded CV text, or None to use the default profile."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT cv_text FROM candidate_profile WHERE is_active=1 ORDER BY uploaded_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def score_job(job: dict, client=None, profile: Optional[str] = None) -> Optional[dict]:
    """
    Call Groq API to score a single job dict.
    profile: optional CV text override; uses DB profile or default if None.
    Returns parsed result dict or None on failure.
    """
    if client is None:
        client = _get_client()

    system_prompt = build_system_prompt(profile)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        title=job.get("title", ""),
        company=job.get("company", ""),
        location=job.get("location", ""),
        country=job.get("country", ""),
        source=job.get("source", ""),
        description=(job.get("description") or "")[:3000],
    )

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1200,
                temperature=0.2,
            )
            raw = response.choices[0].message.content
            cleaned = _clean_json_response(raw)
            result = json.loads(cleaned)
            return result

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error for job {job.get('id')} (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(1)

        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "429" in err:
                logger.warning("Rate limited by Groq — sleeping 30s")
                time.sleep(30)
            elif "timeout" in err:
                logger.warning(f"Timeout for job {job.get('id')} (attempt {attempt+1})")
                if attempt == 0:
                    time.sleep(5)
            else:
                logger.error(f"Groq API error for job {job.get('id')}: {e}")
                break

    return None


def score_all_pending(db_path: Path = DB_PATH) -> int:
    """
    Score all jobs not yet in scored_jobs table, using the active CV profile.
    Returns count of newly scored jobs.
    """
    client = _get_client()
    profile = get_active_profile(db_path)
    if profile:
        logger.info("Using uploaded CV profile for scoring.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT j.* FROM jobs j
        LEFT JOIN scored_jobs s ON s.job_id = j.id
        WHERE s.id IS NULL
        ORDER BY j.scraped_at DESC
    """)
    pending = cur.fetchall()

    if not pending:
        logger.info("No unscored jobs found.")
        conn.close()
        return 0

    logger.info(f"Scoring {len(pending)} pending jobs with Groq ({GROQ_MODEL})...")
    scored_count = 0

    for job in pending:
        job_dict = dict(job)
        logger.info(f"Scoring: [{job_dict['source']}] {job_dict['title']} @ {job_dict.get('company', '?')}")

        result = score_job(job_dict, client, profile=profile)
        time.sleep(RATE_LIMIT_DELAY)

        if result is None:
            logger.warning(f"  → Skipped (no result) for job_id={job_dict['id']}")
            continue

        score = int(result.get("score", 0))
        logger.info(f"  → Score: {score}/100 | {result.get('score_label', '')} | worth={result.get('worth_applying')}")

        try:
            cur.execute("""
                INSERT INTO scored_jobs
                  (job_id, score, score_label, match_tags, gap_tags,
                   reasoning, tailored_cv, cover_letter, cover_language)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_dict["id"],
                score,
                result.get("score_label", ""),
                json.dumps(result.get("match_tags", []), ensure_ascii=False),
                json.dumps(result.get("gap_tags", []), ensure_ascii=False),
                result.get("reasoning", ""),
                result.get("tailored_summary", ""),
                result.get("cover_letter", ""),
                result.get("cover_language", "en"),
            ))
            conn.commit()
            scored_count += 1
        except sqlite3.Error as e:
            logger.error(f"DB error saving score for job_id={job_dict['id']}: {e}")

    conn.close()
    logger.success(f"Scoring complete: {scored_count}/{len(pending)} jobs scored.")
    return scored_count
