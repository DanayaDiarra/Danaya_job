# Danaya's AI Job Agent 🤖

Automated job discovery, AI scoring, and application system.

**Targets:** Russia (hh.ru) · Europe (RemoteOK, Relocate.me) · Africa (Jobberman, Rekrute, BrighterMonday) · UN/INGO (ReliefWeb API)

**Stack:** Python · Claude API · Playwright · Streamlit · SQLite · GitHub Actions

---

## Quick Start

```bash
git clone https://github.com/YOUR_USERNAME/job-agent
cd job-agent

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# ✏️ Edit .env — only ANTHROPIC_API_KEY is required to start

python setup.py                      # init SQLite DB
python main.py --no-submit           # first run: scrape + score + notify only
streamlit run dashboard/app.py       # open review dashboard at localhost:8501
```

---

## How It Works

```
Every 6h (GitHub Actions):
  1. Scrape hh.ru + RemoteOK + Jobberman + ReliefWeb + LinkedIn (optional)
  2. Claude scores each job 0–100 vs Danaya's profile
  3. Jobs ≥70 → Telegram digest with ✅/★/✕ inline buttons
  4. Review & approve in Streamlit dashboard (or via Telegram)
  5. Approved → tailored CV (.docx + .pdf) + cover letter generated
  6. Submit: hh.ru Easy Apply (Playwright) · Email · Manual notification
```

---

## CLI Flags

```bash
python main.py                  # full pipeline
python main.py --scrape-only    # scrape only, no scoring or submission
python main.py --score-only     # score unscored jobs only
python main.py --no-submit      # scrape + score + notify, skip submission
python main.py --dry-run        # everything except actual HTTP submission
```

---

## Environment Variables

Copy `.env.example` → `.env` and fill in:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ Yes | Claude API key — [get one here](https://console.anthropic.com/settings/keys) |
| `TELEGRAM_BOT_TOKEN` | Recommended | From [@BotFather](https://t.me/botfather) |
| `TELEGRAM_CHAT_ID` | Recommended | Your chat ID from [@userinfobot](https://t.me/userinfobot) |
| `HH_EMAIL` | For hh.ru apply | Your hh.ru account email |
| `HH_PASSWORD` | For hh.ru apply | Your hh.ru account password |
| `GMAIL_ADDRESS` | For email apply | Gmail address |
| `GMAIL_APP_PASSWORD` | For email apply | [Gmail App Password](https://myaccount.google.com/apppasswords) |
| `APIFY_API_TOKEN` | Optional | [Apify](https://apify.com) token for LinkedIn scraping |
| `SCORE_THRESHOLD` | Optional | Minimum score to surface (default: 70) |
| `MAX_APPLICATIONS_PER_DAY` | Optional | Daily cap (default: 8) |

---

## Deployment

### GitHub Actions (free scheduler — runs every 6h)

1. Push repo to GitHub
2. **Settings → Secrets and variables → Actions** → add each key from `.env.example`
3. **Actions tab → Run workflow** — manual test run
4. Verify it runs every 6h automatically via the `schedule` trigger

### Streamlit Cloud (free live dashboard)

1. Go to [share.streamlit.io](https://share.streamlit.io)
2. Connect your GitHub repo
3. Set **Main file path**: `dashboard/app.py`
4. Add all secrets from `.env.example` in **Secrets** panel
5. Deploy → get URL like `https://danaya-job-agent.streamlit.app`

---

## Run Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Cost Estimate

| Service | Cost |
|---|---|
| Claude API (scoring ~200 jobs/day) | ~$3–8/month |
| GitHub Actions | Free |
| Streamlit Cloud | Free |
| Apify LinkedIn (optional) | $5–10/month |
| **Total minimum** | **~$3–8/month** |

---

## Getting API Keys

| Key | Link |
|---|---|
| Anthropic (Claude) | https://console.anthropic.com/settings/keys |
| Telegram Bot | Message [@BotFather](https://t.me/botfather) → `/newbot` |
| Telegram Chat ID | Message [@userinfobot](https://t.me/userinfobot) |
| Gmail App Password | https://myaccount.google.com/apppasswords (2FA must be on) |
| Apify (LinkedIn) | https://apify.com → Settings → Integrations → API tokens |

---

## Project Structure

```
job_agent/
├── main.py              # orchestrator: scrape → score → notify → submit
├── setup.py             # DB schema init
├── scheduler.py         # local APScheduler (alternative to GitHub Actions)
├── scrapers/            # hh.ru, Africa, Europe, UN/INGO, LinkedIn
├── scorer/              # Claude API scoring + prompts
├── applicator/          # CV generator, cover letter, hh submitter, Telegram
├── dashboard/           # Streamlit review UI + analytics
├── templates/           # Jinja2 HTML templates for CV/cover PDF
├── tests/               # pytest unit tests
└── .github/workflows/   # GitHub Actions cron job
```
