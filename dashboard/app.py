"""
dashboard/app.py — Streamlit review dashboard for the Job Agent.
"""
import os
import sqlite3
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()

DB_PATH = Path(os.getenv("DB_PATH", "data/jobs.db"))
SCORE_THRESHOLD = int(os.getenv("SCORE_THRESHOLD", "70"))

# ── Page config ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Danaya's Job Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Dark theme injection ───────────────────────────────────────────────────

st.markdown("""
<style>
:root {
  --bg: #080b12;
  --surface: #0d1117;
  --surface2: #131922;
  --accent: #00d4aa;
  --accent2: #3b82f6;
  --accent3: #f59e0b;
  --text: #e2e8f5;
  --muted: #94a3b8;
  --dim: #3d5270;
  --border: #1e2d45;
}
.stApp { background-color: var(--bg); color: var(--text); }
section[data-testid="stSidebar"] { background-color: var(--surface); }
.stButton > button {
  background: var(--surface2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.stButton > button:hover { border-color: var(--accent); color: var(--accent); }
</style>
""", unsafe_allow_html=True)


# ── DB helpers ─────────────────────────────────────────────────────────────

def get_conn():
    if not DB_PATH.exists():
        st.error(f"Database not found at `{DB_PATH}`. Run `python setup.py` first.")
        st.stop()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_jobs(min_score: int, sources: list, countries: list,
              status_filter: str) -> list:
    conn = get_conn()
    cur = conn.cursor()

    wheres = ["s.score >= ?"]
    params: list = [min_score]

    if sources:
        placeholders = ",".join("?" * len(sources))
        wheres.append(f"j.source IN ({placeholders})")
        params.extend(sources)

    if countries:
        placeholders = ",".join("?" * len(countries))
        wheres.append(f"j.country IN ({placeholders})")
        params.extend(countries)

    if status_filter == "pending":
        wheres.append("d.id IS NULL")
    elif status_filter == "apply":
        wheres.append("d.decision = 'apply'")
    elif status_filter == "skip":
        wheres.append("d.decision = 'skip'")
    elif status_filter == "later":
        wheres.append("d.decision = 'later'")

    where_clause = " AND ".join(wheres)

    cur.execute(f"""
        SELECT j.id as job_id, j.title, j.company, j.location, j.country,
               j.url, j.source, j.salary_raw, j.description, j.posted_at,
               s.score, s.score_label, s.match_tags, s.gap_tags,
               s.reasoning, s.tailored_cv, s.cover_letter, s.cover_language,
               d.decision
        FROM jobs j
        JOIN scored_jobs s ON s.job_id = j.id
        LEFT JOIN decisions d ON d.job_id = j.id
        WHERE {where_clause}
        ORDER BY s.score DESC
    """, params)

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_last_run() -> str:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT MAX(scraped_at) FROM jobs")
    row = cur.fetchone()
    conn.close()
    return row[0] or "Never"


def get_all_sources() -> list:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT source FROM jobs ORDER BY source")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


def get_all_countries() -> list:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT country FROM jobs WHERE country IS NOT NULL ORDER BY country")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows


# ── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🤖 Job Agent")
    st.markdown("*Danaya Diarra — AI/Data roles*")
    st.markdown("---")

    # Live stats
    from dashboard.components.stats_bar import render_stats_bar
    render_stats_bar(DB_PATH)

    st.markdown("---")
    st.markdown("### Filters")

    status_filter = st.selectbox(
        "Status",
        ["pending", "apply", "later", "skip", "all"],
        index=0,
    )

    min_score = st.slider("Min Score", 0, 100, SCORE_THRESHOLD, step=5)

    all_sources = get_all_sources()
    sel_sources = st.multiselect("Source", all_sources, default=[])

    all_countries = get_all_countries()
    sel_countries = st.multiselect("Country", all_countries, default=[])

    st.markdown("---")
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption(f"Last scrape: {get_last_run()[:16] if get_last_run() else 'Never'}")


# ── Main area ──────────────────────────────────────────────────────────────

tab_review, tab_analytics = st.tabs(["📋 Review Jobs", "📊 Analytics"])

with tab_review:
    st.markdown("## Job Review")

    jobs = load_jobs(
        min_score=min_score,
        sources=sel_sources if sel_sources else [],
        countries=sel_countries if sel_countries else [],
        status_filter=status_filter,
    )

    if not jobs:
        st.markdown(
            "<div style='text-align:center;padding:60px;color:#3d5270'>"
            "<div style='font-size:48px'>🔍</div>"
            "<div style='font-size:18px;margin-top:12px'>No jobs match your filters</div>"
            "<div style='font-size:13px;margin-top:8px;color:#94a3b8'>"
            "Try lowering the min score or broadening filters.<br>"
            "Run <code>python main.py --scrape-only</code> to fetch new jobs."
            "</div></div>",
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"Showing {len(jobs)} jobs")
        from dashboard.components.job_card import render_job_card
        for job in jobs:
            render_job_card(job, DB_PATH)

with tab_analytics:
    st.markdown("## Pipeline Analytics")
    from dashboard.components.pipeline_chart import render_analytics
    render_analytics(DB_PATH)
