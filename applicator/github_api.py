"""
applicator/github_api.py — GitHub REST API helpers for the always-on bot server.

Used by bot_server.py to:
  - Trigger workflow_dispatch (CV-driven scoring run)
  - Queue Apply/Save/Skip decisions in decisions_queue.json
"""
import base64
import json
import os

import requests
from loguru import logger

GITHUB_PAT  = os.getenv("GITHUB_PAT", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "DanayaDiarra/Danaya_job")
WORKFLOW_FILE = "agent.yml"
DECISIONS_PATH = "decisions_queue.json"

_GH_HEADERS = lambda: {
    "Authorization": f"token {GITHUB_PAT}",
    "Accept": "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def trigger_cv_workflow(cv_b64: str) -> bool:
    """
    Fire workflow_dispatch on agent.yml with the base64-encoded CV text.
    GitHub Actions will decode it, save as the active profile, and run score-only.
    """
    if not GITHUB_PAT:
        logger.error("GITHUB_PAT not set — cannot trigger workflow")
        return False

    url = (f"https://api.github.com/repos/{GITHUB_REPO}"
           f"/actions/workflows/{WORKFLOW_FILE}/dispatches")
    resp = requests.post(
        url,
        json={"ref": "main", "inputs": {"cv_b64": cv_b64}},
        headers=_GH_HEADERS(),
        timeout=15,
    )
    if resp.status_code == 204:
        logger.success("GitHub Actions workflow triggered via CV upload")
        return True
    logger.error(f"workflow_dispatch failed {resp.status_code}: {resp.text[:200]}")
    return False


def queue_decision(job_id: int, decision: str) -> bool:
    """
    Append an Apply/Save/Skip decision to decisions_queue.json in the repo.
    GitHub Actions reads and applies this file at the start of each run.
    """
    if not GITHUB_PAT:
        logger.warning("GITHUB_PAT not set — decision not persisted")
        return False

    api_url = (f"https://api.github.com/repos/{GITHUB_REPO}"
               f"/contents/{DECISIONS_PATH}")
    headers = _GH_HEADERS()

    # Read current file (may not exist yet)
    resp = requests.get(api_url, headers=headers, timeout=10)
    if resp.ok:
        file_meta = resp.json()
        sha = file_meta["sha"]
        queue = json.loads(base64.b64decode(file_meta["content"]).decode())
    else:
        sha = None
        queue = {"decisions": []}

    # Avoid duplicates — overwrite if same job_id already queued
    queue["decisions"] = [d for d in queue["decisions"] if d["job_id"] != job_id]
    queue["decisions"].append({"job_id": job_id, "decision": decision})

    new_content = base64.b64encode(json.dumps(queue, indent=2).encode()).decode()
    payload = {
        "message": f"queue: {decision} job#{job_id} [bot]",
        "content": new_content,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, json=payload, headers=headers, timeout=15)
    if resp.ok:
        logger.info(f"Decision queued: job#{job_id} → {decision}")
        return True
    logger.error(f"queue_decision failed {resp.status_code}: {resp.text[:200]}")
    return False
