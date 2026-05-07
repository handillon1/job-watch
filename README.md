# job-watch

Watches job-aggregator repos for new SWE internship listings and DMs you on Telegram. Each alert has a "Mark as Applied" button that logs the role to a Google Sheet you control.

Based on [kiankian/repo-watcher](https://github.com/kiankian/repo-watcher), used with permission. Differences:
- Emoji-stripping in row identity, so changes to Simplify's 🔥/🎓/🛂/🇺🇸 markers don't re-alert on jobs you've already seen.
- Scheduled cron (every 5 min during the day, hourly overnight Pacific time) instead of `workflow_dispatch`-only.

## What it watches

| Source | File | Branch |
|---|---|---|
| `SimplifyJobs/Summer2026-Internships` | `README-Off-Season.md` (active SWE table only) | `dev` |
| `vanshb03/Summer2027-Internships` | `OFFSEASON_README.md` | `dev` |

Only **new** rows trigger alerts. Closures, re-orderings, and emoji-marker flips are silently ignored. When the Summer 2027 Simplify repo drops, add another entry to the `WATCHERS` list in [`watch-files.yml`](.github/workflows/watch-files.yml).

## Schedule

Cron is UTC; calibrated for **PDT (UTC-7)**:
- Every 5 min from **6am-midnight PT**
- Hourly from **midnight-5am PT**

When DST ends in November, the schedule shifts ~1 hour earlier in local time. Adjust the cron in `watch-files.yml` if it bothers you.

## Setup

You need a Telegram account, a bot, and a Google Sheet.

### 1. Create a Telegram bot
- DM [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, follow the prompts.
- Save the bot token. This is `TELEGRAM_BOT_TOKEN`.
- Send your new bot any message (so it can DM you back).
- Get your chat ID:
  ```sh
  curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
  ```
  Find `"chat":{"id": <number>` in the response. That number is `TELEGRAM_CHAT_ID`.

### 2. Create the Google Sheet
- Make a new Google Sheet. Add a tab named `Applications`.
- Add this header row in row 1:

  | A: Company | B: Role | C: Date Applied | D: Email Applied | E: Resource | F: Status |
  |---|---|---|---|---|---|

- If you have a Status dropdown on column F, make sure `Applied` is one of the allowed values, or Sheets will flag rows the bot writes.
- Copy the spreadsheet ID from the URL (`docs.google.com/spreadsheets/d/<ID>/edit`). This is `APPLICATIONS_SHEET_ID`.

### 3. Create a GCP service account (for the sheet)
- Go to https://console.cloud.google.com, make a project (or use an existing one).
- Enable the **Google Sheets API**.
- IAM → Service Accounts → Create one. No special roles needed.
- On the service account, **Keys → Add Key → JSON**. Download the file.
- Open the JSON, copy the entire contents — that's `GOOGLE_SERVICE_ACCOUNT_JSON`.
- Find the service account's email (`...@...iam.gserviceaccount.com`) and **share your sheet with it as Editor**.

### 4. Add secrets to your fork
In GitHub → Settings → Secrets and variables → Actions → New repository secret, add:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from step 1 |
| `TELEGRAM_CHAT_ID` | from step 1 |
| `APPLICATIONS_SHEET_ID` | from step 2 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | from step 3 — paste the whole JSON file |

Optional repo **variable** (not secret):

| Name | Default | Purpose |
|---|---|---|
| `APPLICATIONS_SHEET_RANGE` | `Applications!A:F` | Tab + range to append rows to. |

### 5. Trigger the first run
- Actions tab → Watch files in external repo → Run workflow. The first run **bootstraps** state (logs all current rows) without sending alerts. Subsequent runs alert on new rows only.
- After that, the cron handles everything. You'll get DMs as new SWE listings hit the watched repos.

## Telegram bot caveat

The workflow polls Telegram via `getUpdates`. **Do not set a webhook on your bot** or `getUpdates` returns HTTP 409. If you accidentally did:

```sh
curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"
```

(The workflow auto-clears the webhook on every run as a safety net, but you don't want it set in the first place.)

## When you push local changes

The workflow commits `.watcher_state.json` and `.bot_state.json` back to the repo on every successful run, so the remote often advances ahead of your local. Always rebase before pushing:

```sh
git pull --rebase origin main
git push origin main
```

If you hit a rebase conflict on `.watcher_state.json`, **keep the remote version** — the runner's SHA is more recent than yours:

```sh
git checkout --theirs .watcher_state.json
git add .watcher_state.json
git rebase --continue
```

Avoid `git push --force` — it overwrites the runner's state commits and the next run will re-alert on stale snapshots.

## How identity / dedup works

Each row is keyed by `(company, role, location, term)`, with Unicode emoji-category characters stripped from the key fields. So:
- `"🔥 NVIDIA"` and `"NVIDIA"` are treated as the same company.
- `"Software Engineering Intern 🎓"` and `"Software Engineering Intern"` are treated as the same role.
- Apply URL changes don't trigger re-alerts (URL is not part of the key).

The displayed alert text keeps emojis intact — only the identity tuple is normalized.

## Sharing with friends

Tell them to clone this repo, follow the Setup section with **their own** Telegram bot, sheet, and secrets, and push to **their** fork. Each person's runs are independent — their state lives in their fork.
