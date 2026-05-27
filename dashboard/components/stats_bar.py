"""
dashboard/components/stats_bar.py — Live pipeline stats bar.
"""
import sqlite3
from pathlib import Path

import streamlit as st


def render_stats_bar(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    stats = {}

    cur.execute("SELECT COUNT(*) FROM jobs")
    stats["Scraped"] = cur.fetchone()[0]

    cur.execute(f"SELECT COUNT(*) FROM scored_jobs WHERE score >= 70")
    stats["Surfaced"] = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM decisions WHERE decision='apply'")
    stats["Queued"] = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM decisions")
    stats["Reviewed"] = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM applications")
    stats["Submitted"] = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM applications WHERE status='interview'"
    )
    stats["Interviews"] = cur.fetchone()[0]

    conn.close()

    cols = st.columns(len(stats))
    colors = {
        "Scraped": "#94a3b8",
        "Surfaced": "#3b82f6",
        "Reviewed": "#f59e0b",
        "Queued": "#00d4aa",
        "Submitted": "#a78bfa",
        "Interviews": "#f87171",
    }

    for col, (label, value) in zip(cols, stats.items()):
        color = colors.get(label, "#94a3b8")
        col.markdown(
            f"<div style='text-align:center;padding:12px 4px;"
            f"background:#0d1117;border-radius:10px;border:1px solid #1e2d45'>"
            f"<div style='font-size:26px;font-weight:bold;color:{color}'>{value}</div>"
            f"<div style='font-size:11px;color:#94a3b8;margin-top:2px'>{label}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
