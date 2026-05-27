"""
dashboard/components/pipeline_chart.py — Analytics charts for Streamlit.
"""
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PALETTE = {
    "bg": "#080b12",
    "surface": "#0d1117",
    "accent": "#00d4aa",
    "accent2": "#3b82f6",
    "accent3": "#f59e0b",
    "text": "#e2e8f5",
    "muted": "#94a3b8",
}


def _chart_layout(title: str = "") -> dict:
    return dict(
        title=title,
        paper_bgcolor=PALETTE["surface"],
        plot_bgcolor=PALETTE["surface"],
        font=dict(color=PALETTE["text"]),
        margin=dict(t=40, b=30, l=30, r=10),
    )


def render_analytics(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)

    # ── Applications over time ─────────────────────────────────────────────
    apps_df = pd.read_sql(
        "SELECT date(applied_at) as day, COUNT(*) as count FROM applications "
        "GROUP BY day ORDER BY day",
        conn,
    )
    if not apps_df.empty:
        fig = px.line(
            apps_df, x="day", y="count",
            title="Applications Over Time",
            color_discrete_sequence=[PALETTE["accent"]],
        )
        fig.update_layout(**_chart_layout("Applications Over Time"))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No application data yet.")

    col1, col2 = st.columns(2)

    # ── Score distribution ─────────────────────────────────────────────────
    with col1:
        score_df = pd.read_sql("SELECT score FROM scored_jobs", conn)
        if not score_df.empty:
            fig = px.histogram(
                score_df, x="score", nbins=20,
                title="Score Distribution",
                color_discrete_sequence=[PALETTE["accent2"]],
            )
            fig.update_layout(**_chart_layout("Score Distribution"))
            st.plotly_chart(fig, use_container_width=True)

    # ── Applications by source ─────────────────────────────────────────────
    with col2:
        source_df = pd.read_sql(
            "SELECT j.source, COUNT(*) as count FROM applications a "
            "JOIN jobs j ON j.id = a.job_id GROUP BY j.source ORDER BY count DESC",
            conn,
        )
        if not source_df.empty:
            fig = px.bar(
                source_df, x="source", y="count",
                title="Applications by Source",
                color_discrete_sequence=[PALETTE["accent3"]],
            )
            fig.update_layout(**_chart_layout("By Source"))
            st.plotly_chart(fig, use_container_width=True)

    # ── Status funnel ──────────────────────────────────────────────────────
    funnel_data = {}
    funnel_data["Scraped"] = pd.read_sql("SELECT COUNT(*) as n FROM jobs", conn).iloc[0, 0]
    funnel_data["Scored"] = pd.read_sql("SELECT COUNT(*) as n FROM scored_jobs", conn).iloc[0, 0]
    funnel_data["Surfaced ≥70"] = pd.read_sql(
        "SELECT COUNT(*) as n FROM scored_jobs WHERE score >= 70", conn
    ).iloc[0, 0]
    funnel_data["Reviewed"] = pd.read_sql("SELECT COUNT(*) as n FROM decisions", conn).iloc[0, 0]
    funnel_data["Applied"] = pd.read_sql("SELECT COUNT(*) as n FROM applications", conn).iloc[0, 0]
    funnel_data["Interview"] = pd.read_sql(
        "SELECT COUNT(*) as n FROM applications WHERE status='interview'", conn
    ).iloc[0, 0]

    fig = go.Figure(go.Funnel(
        y=list(funnel_data.keys()),
        x=list(funnel_data.values()),
        textinfo="value+percent initial",
        marker={"color": [
            "#94a3b8", "#3b82f6", "#00d4aa", "#f59e0b", "#a78bfa", "#f87171"
        ]},
    ))
    fig.update_layout(**_chart_layout("Application Pipeline Funnel"))
    st.plotly_chart(fig, use_container_width=True)

    # ── Applications by country ────────────────────────────────────────────
    country_df = pd.read_sql(
        "SELECT j.country, COUNT(*) as count FROM applications a "
        "JOIN jobs j ON j.id = a.job_id GROUP BY j.country ORDER BY count DESC",
        conn,
    )
    if not country_df.empty:
        fig = px.bar(
            country_df, x="country", y="count",
            title="Applications by Country",
            color_discrete_sequence=[PALETTE["accent"]],
        )
        fig.update_layout(**_chart_layout("By Country"))
        st.plotly_chart(fig, use_container_width=True)

    conn.close()
