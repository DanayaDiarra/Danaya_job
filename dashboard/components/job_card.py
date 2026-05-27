"""
dashboard/components/job_card.py — Job card component for Streamlit.
"""
import json
import sqlite3
from pathlib import Path

import streamlit as st


def score_color(score: int) -> str:
    if score >= 85:
        return "#00d4aa"   # teal
    elif score >= 70:
        return "#3b82f6"   # blue
    else:
        return "#f59e0b"   # amber


def render_job_card(job: dict, db_path: Path) -> None:
    score = job.get("score", 0)
    color = score_color(score)

    match_tags = json.loads(job.get("match_tags") or "[]")
    gap_tags = json.loads(job.get("gap_tags") or "[]")
    cover_lang = job.get("cover_language", "en")

    with st.container():
        # Header row
        col_info, col_score = st.columns([4, 1])
        with col_info:
            st.markdown(
                f"### {job.get('title', 'Unknown Title')}\n"
                f"**{job.get('company', '')}** &nbsp;|&nbsp; "
                f"📍 {job.get('location', '')} &nbsp;|&nbsp; "
                f"🌐 `{job.get('source', '')}`"
            )
            if job.get("salary_raw"):
                st.caption(f"💰 {job['salary_raw']}")
            if job.get("posted_at"):
                st.caption(f"📅 Posted: {job['posted_at'][:10]}")

        with col_score:
            st.markdown(
                f"<div style='text-align:center;padding:8px;"
                f"border-radius:12px;background:{color}22;"
                f"border:2px solid {color}'>"
                f"<span style='font-size:28px;font-weight:bold;color:{color}'>{score}</span>"
                f"<br><span style='font-size:11px;color:{color}'>{job.get('score_label','')}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # Tags row
        tag_html = "".join(
            f"<span style='background:#00d4aa22;color:#00d4aa;border:1px solid #00d4aa;"
            f"border-radius:6px;padding:2px 8px;margin:2px;font-size:12px'>{t}</span>"
            for t in match_tags
        )
        gap_html = "".join(
            f"<span style='background:#f59e0b22;color:#f59e0b;border:1px solid #f59e0b;"
            f"border-radius:6px;padding:2px 8px;margin:2px;font-size:12px'>⚠ {g}</span>"
            for g in gap_tags
        )
        st.markdown(tag_html + gap_html, unsafe_allow_html=True)

        # Reasoning quote
        if job.get("reasoning"):
            st.markdown(
                f"<blockquote style='border-left:3px solid #3d5270;"
                f"padding-left:10px;color:#94a3b8;font-style:italic;margin:8px 0'>"
                f"{job['reasoning']}</blockquote>",
                unsafe_allow_html=True,
            )

        # Tabs
        tab1, tab2, tab3 = st.tabs(["📄 Tailored CV Summary", "✉️ Cover Letter", "📋 Job Description"])

        with tab1:
            edited_cv = st.text_area(
                "Edit tailored summary",
                value=job.get("tailored_cv") or "",
                height=120,
                key=f"cv_{job['job_id']}",
                label_visibility="collapsed",
            )
            if st.button("📋 Copy Summary", key=f"copy_cv_{job['job_id']}"):
                st.code(edited_cv)

        with tab2:
            lang_toggle = st.selectbox(
                "Language",
                ["en", "ru", "fr"],
                index=["en", "ru", "fr"].index(cover_lang) if cover_lang in ["en","ru","fr"] else 0,
                key=f"lang_{job['job_id']}",
            )
            edited_cover = st.text_area(
                "Edit cover letter",
                value=job.get("cover_letter") or "",
                height=200,
                key=f"cover_{job['job_id']}",
                label_visibility="collapsed",
            )
            if st.button("📋 Copy Cover Letter", key=f"copy_cover_{job['job_id']}"):
                st.code(edited_cover)

        with tab3:
            st.markdown(
                f"<a href='{job.get('url','')}' target='_blank'>🔗 Open Original Job Posting</a>",
                unsafe_allow_html=True,
            )
            st.text_area(
                "Description",
                value=(job.get("description") or "")[:2000],
                height=200,
                key=f"desc_{job['job_id']}",
                label_visibility="collapsed",
                disabled=True,
            )

        # Decision buttons
        st.markdown("---")
        current_decision = job.get("decision", "")
        col_apply, col_save, col_skip = st.columns(3)

        with col_apply:
            if current_decision == "apply":
                st.success("✅ Queued to Apply")
                _show_download_buttons(job, db_path)
            else:
                if st.button("✅ Apply", key=f"apply_{job['job_id']}", use_container_width=True):
                    _record_decision(job["job_id"], "apply", db_path)
                    st.rerun()

        with col_save:
            if current_decision == "later":
                st.info("★ Saved for Later")
            else:
                if st.button("★ Save for Later", key=f"save_{job['job_id']}", use_container_width=True):
                    _record_decision(job["job_id"], "later", db_path)
                    st.rerun()

        with col_skip:
            if current_decision == "skip":
                st.warning("✕ Skipped")
            else:
                if st.button("✕ Skip", key=f"skip_{job['job_id']}", use_container_width=True,
                             type="secondary"):
                    _record_decision(job["job_id"], "skip", db_path)
                    st.rerun()

        st.markdown("<hr style='border-color:#1e2d45;margin:24px 0'>", unsafe_allow_html=True)


def _record_decision(job_id: int, decision: str, db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO decisions (job_id, decision) VALUES (?, ?)",
        (job_id, decision)
    )
    conn.commit()
    conn.close()


def _show_download_buttons(job: dict, db_path: Path) -> None:
    from pathlib import Path as P
    import os

    out_dir = P("data/applications")
    job_id = job["job_id"]

    for pattern in [f"cv_{job_id}_*.docx", f"cv_{job_id}_*.pdf"]:
        matches = list(out_dir.glob(pattern)) if out_dir.exists() else []
        if matches:
            fpath = matches[0]
            with open(fpath, "rb") as f:
                st.download_button(
                    f"📄 Download {fpath.suffix.upper()}",
                    f.read(),
                    file_name=fpath.name,
                    key=f"dl_{job_id}_{fpath.suffix}",
                )
