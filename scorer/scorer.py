"""
scorer/scorer.py — Score jobs against Danaya's profile using Claude API.
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv
from loguru import logger

from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))
MODEL = "claude-sonnet-4-20250514"
RATE_LIMIT_DELAY = 0.6  # seconds between API calls


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to your .env file.\n"
            "Get one at: https://console.anthropic.com/settings/keys"
        )
    return anthropic.Anthropic(api_key=api_key)


def _clean_json_response(text: str) -> str:
    """Strip markdown fences and leading/trailing whitespace."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def score_job(job: dict, client: Optional[anthropic.Anthropic] = None) -> Optional[dict]:
    """
    Call Claude API to score a single job dict.
    Returns parsed result dict or None on failure.
    """
    if client is None:
        client = _get_client()

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
            response = client.messages.create(
                model=MODEL,
                max_tokens=1200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
            cleaned = _clean_json_response(raw)
            result = json.loads(cleaned)
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error for job {job.get('id')} (attempt {attempt+1}): {e}")
            logger.debug(f"Raw response: {raw[:200]}")
            if attempt == 0:
                time.sleep(1)
        except anthropic.RateLimitError:
            logger.warning("Rate limited by Anthropic — sleeping 30s")
            time.sleep(30)
        except anthropic.APITimeoutError:
            logger.warning(f"API timeout for job {job.get('id')} (attempt {attempt+1})")
            if attempt == 0:
                time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error scoring job {job.get('id')}: {e}")
            break

    return None


def score_all_pending(db_path: Path = DB_PATH) -> int:
    """
    Score all jobs not yet in scored_jobs table.
    Returns count of newly scored jobs.
    """
    client = _get_client()
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

    logger.info(f"Scoring {len(pending)} pending jobs...")
    scored_count = 0

    for job in pending:
        job_dict = dict(job)
        logger.info(f"Scoring: [{job_dict['source']}] {job_dict['title']} @ {job_dict.get('company', '?')}")

        result = score_job(job_dict, client)
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
