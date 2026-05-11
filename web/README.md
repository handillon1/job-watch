# Dashboard

Local Flask dashboard that surfaces the job-watch bot's connection status in one view. Read-only — it doesn't change any state, just shows you what's connected and what isn't.

## Why

The bot involves Telegram, Google Sheets, GCP service accounts, GitHub Actions, cron-job.org, and two state files. When something's off, finding the broken piece means clicking through 5 different services. This dashboard answers "is everything wired up?" in one page.

## Setup

```sh
cd web
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your real values (same secrets you set on GitHub Actions)
```

For `GOOGLE_SERVICE_ACCOUNT_JSON` you can either:
- Paste the entire JSON content into the variable (as one line, escaping internal newlines), OR
- Set it to a filesystem path like `/Users/you/path/to/sa.json` — the app will read the file

## Run

```sh
python app.py
```

Open http://localhost:5001.

The page is read-only; reload to re-check status. The app is stateless — every page load re-queries all the services live.

## What it shows

Seven status cards, each with green/yellow/red + a one-line summary + collapsible details with remediation hints:

1. **Telegram bot** — bot identity, chat reachability, webhook status (webhook set = bad, breaks getUpdates)
2. **Google Sheets** — sheet auth, title, `Applications` tab presence
3. **GCP service account** — SA email + project for verifying you shared the sheet correctly
4. **GitHub Actions** — last 10 workflow runs, success rate, freshness
5. **cron-job.org cadence** — inferred from how recent the last GH run was
6. **Watched job repos** — for each upstream source (Simplify, Vansh): last snapshot SHA, row count, and whether upstream has advanced
7. **Bot state** — pending vs applied counts from `.bot_state.json`

## What it doesn't do (yet)

- No tables of individual pending/applied jobs (v2)
- No live sheet contents preview (v2)
- No funnel chart from your sheet's Status column (v3)
- No runs history table (v3)
- No interactive setup wizard / write operations (v4 — long-term goal)
- No auto-refresh — manual page reload

## Troubleshooting

**Dashboard won't start (ModuleNotFoundError):** you forgot `pip install -r requirements.txt` or you're in the wrong venv.

**Everything is red:** probably your `.env` didn't load. Check you copied `.env.example` to `.env` and you're running `python app.py` from the `web/` directory.

**GitHub API rate-limit errors:** add a `GITHUB_TOKEN` to `.env` for 5000 req/hr instead of 60. Any GitHub token works (even a fresh fine-grained one with no scopes).

**Sheets card red with HTTP 403:** you didn't share the sheet with the SA email. The GCP card shows the email — share the sheet with that address as Editor.

**Sheets card red with HTTP 404:** wrong sheet ID. Open your sheet, copy the ID from the URL (`docs.google.com/spreadsheets/d/<ID>/edit`).

**Watched-repos card shows "not bootstrapped":** the bot has never run for that source. Trigger the workflow once manually from the GitHub Actions tab.
