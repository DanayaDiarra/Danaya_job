"""
scorer/prompts.py — System prompt and candidate profile for job scoring.
"""

CANDIDATE_PROFILE = """
Name: Danaya Diarra
Email: diarradanaya5544@gmail.com
Phone: +7-952-217-0325
Location: Saint Petersburg, Russia
Open to: Russia · Europe · Francophone Africa · Remote

EXPERIENCE:
- Big Data Analyst Intern @ Pulkovo Airport, SPb (Jul–Sep 2025)
  Power BI dashboards (−40% reporting time), time-series forecasting,
  Python/Pandas/Scikit-learn, large-scale EDA

- Market Data Analyst Intern @ RostovIT (Jan–Mar 2025)
  Python/SQL data cleaning, market trend reports, competitive analysis

- Supply Chain Engineer @ Huawei Technologies, Mali (2024)
  Python automation (−60% manual entry), KPI dashboards for 10+ sites,
  last-mile logistics optimisation

- Supply Chain Analyst @ National Immunization Centre, Mali (2022)
  Cold-chain logistics, public health dashboards, data governance

- Co-Founder & CTO @ FocusLock (2026)
  Mobile productivity app, React Native, Figma, user research,
  freemium business model, gamification

PROJECTS:
- MSc Thesis: Agentic AI for Predictive Maintenance — PyTorch, LangChain,
  LSTM/Transformers, multi-agent LLM workflows, RMSE=15.11 production model
- RAG Chatbot: LangChain, FAISS, YandexGPT, Streamlit
- Image Anomaly Detection: YOLO, BLIP, DeepFace, 94%+ accuracy

SKILLS:
Python, SQL, PyTorch, LangChain, FAISS, Scikit-learn, Pandas, NumPy,
Transformers, Streamlit, Power BI, Tableau, Git, Figma, React Native,
LangGraph (learning), RAG, Agentic AI, Docker (basic)

EDUCATION:
- MSc Business Analytics & Big Data, GSOM SPbSU (2024–2026, enrolled)
- BSc Supply Chain Management, CUPP-BALLAT Mali (2021–2023)

LANGUAGES: French (native), English (fluent), Russian (intermediate), German (basic)

TARGET ROLES: Data Scientist, ML Engineer, Data Analyst, Business Analyst,
Business Development (Africa), Product Manager (AI/Data), Agentic AI Researcher,
UN/INGO internships

SENIORITY: Junior–Mid (3 years experience, MSc student)
""".strip()

_SCORING_INSTRUCTIONS = """
SCORING RUBRIC:
90-100: Near-perfect match — almost all key requirements met
75-89:  Strong match — minor gaps only
60-74:  Good match — notable but manageable gaps
40-59:  Partial match — significant gaps
0-39:   Poor match — do not surface

RESPONSE JSON SCHEMA (return exactly this structure):
{
  "score": <integer 0-100>,
  "score_label": <"Excellent Match" | "Good Match" | "Partial Match" | "Poor Match">,
  "match_tags": [<3 to 6 specific matching skills or experiences>],
  "gap_tags": [<1 to 4 specific gaps>],
  "reasoning": "<2-3 sentence honest assessment of fit>",
  "worth_applying": <true | false>,
  "tailored_summary": "<80 word max first-person CV summary using keywords from THIS specific job>",
  "cover_letter": "<150-200 word cover letter body in the correct language for this job's country>",
  "cover_language": <"ru" | "fr" | "en">
}"""


def build_system_prompt(profile: str = None) -> str:
    """Build the scoring system prompt with either the uploaded or default profile."""
    candidate = (profile or CANDIDATE_PROFILE).strip()
    return (
        "You are an expert technical recruiter evaluating job fit for a specific candidate.\n"
        "Respond ONLY with valid JSON — no markdown fences, no preamble, no explanation outside the JSON.\n"
        "Be honest. Do not inflate scores. Consider seniority gaps carefully.\n\n"
        f"CANDIDATE PROFILE:\n{candidate}\n"
        f"{_SCORING_INSTRUCTIONS}"
    )


# Keep backward-compatible default
SYSTEM_PROMPT = build_system_prompt()

USER_PROMPT_TEMPLATE = """JOB TO EVALUATE:
Title: {title}
Company: {company}
Location: {location}
Country: {country}
Source: {source}
Description:
{description}

Evaluate this job for the candidate and respond with JSON only."""
