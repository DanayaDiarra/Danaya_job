"""
scheduler.py — APScheduler wrapper to run the pipeline every 6 hours locally.
Run: python scheduler.py
"""
import os
import subprocess
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

INTERVAL_HOURS = int(os.getenv("SCRAPE_INTERVAL_HOURS", "6"))


def run_pipeline():
    logger.info("Scheduler: triggering pipeline run")
    result = subprocess.run(
        [sys.executable, "main.py", "--no-submit"],
        cwd=Path(__file__).parent,
        capture_output=False,
    )
    if result.returncode != 0:
        logger.error(f"Pipeline exited with code {result.returncode}")
    else:
        logger.success("Pipeline run complete")


if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_pipeline,
        "interval",
        hours=INTERVAL_HOURS,
        id="job_agent_pipeline",
    )

    logger.info(f"Scheduler started — running every {INTERVAL_HOURS}h")
    logger.info("Press Ctrl+C to stop")

    # Run immediately on start
    run_pipeline()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped")
