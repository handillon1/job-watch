# job-watch

Watches job-aggregator repos for new SWE internship listings and DMs you on Telegram. Each alert has a "Mark as Applied" button that logs the role to a Google Sheet you control.

Based on [kiankian/repo-watcher](https://github.com/kiankian/repo-watcher), used with permission. Differences:
- Emoji-stripping in row identity, so changes to Simplify's 🔥/🎓/🛂/🇺🇸 markers don't re-alert on jobs you've already seen.
- Optional `APPLIED_EMAIL` secret to auto-fill the Email Applied column when you tap Mark as Applied.
- Sheet writes use `OVERWRITE` rather than `INSERT_ROWS`, so anything you place in columns G+ alongside your data rows is preserved.
- GH `schedule:` triggers added as a backup for the every-minute cron-job.org trigger described in Step 5 below.

## What it watches

| Source | File | Branch |
|---|---|---|
| `SimplifyJobs/Summer2026-Internships` | `README-Off-Season.md` (active SWE table only) | `dev` |
| `vanshb03/Summer2027-Internships` | `OFFSEASON_README.md` | `dev` |

Only **new** rows trigger alerts. Closures, re-orderings, and emoji-marker flips are silently ignored. When the Summer 2027 Simplify repo drops, add another entry to the `WATCHERS` list in [`watch-files.yml`](.github/workflows/watch-files.yml).

## How polling works

**Primary trigger: cron-job.org every minute** via the GitHub `workflow_dispatch` API. This is the recommended setup because GitHub's built-in scheduled workflows are aggressively throttled on fresh public repos — often firing as little as once an hour, which makes the apply-button experience flaky (taps sit in Telegram's queue and can race-drop). The 60-second cron-job.org cadence keeps tap-to-sheet latency under 2 min and avoids the documented [getUpdates concurrent-poll race](https://github.com/tdlib/telegram-bot-api/issues/43). Setup is in Step 5 below.

**Fallback: GitHub's built-in `schedule:` triggers** in `watch-files.yml`:
- Every 5 min from **6am-midnight PT**
- Hourly from **midnight-5am PT**

These keep the bot working at degraded latency if cron-job.org has an outage. Times are UTC, calibrated for **PDT (UTC-7)**; during PST (Nov-Mar) the local Pacific times shift ~1 hour earlier. Adjust the cron in `watch-files.yml` if it bothers you.

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

| Name | Required | Value |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | from step 1 |
| `TELEGRAM_CHAT_ID` | yes | from step 1 |
| `APPLICATIONS_SHEET_ID` | yes | from step 2 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | yes | from step 3 — paste the whole JSON file |
| `APPLIED_EMAIL` | optional | the email you'll auto-fill in the "Email Applied" column when you tap "Mark as Applied" (e.g. `you@gmail.com`). If unset, the column is left blank for manual fill-in. |

Optional repo **variable** (not secret):

| Name | Default | Purpose |
|---|---|---|
| `APPLICATIONS_SHEET_RANGE` | `Applications!A:F` | Tab + range to append rows to. |

### 5. Set up the every-minute trigger (cron-job.org)

This is what makes the apply-button reliable. GitHub's built-in scheduled cron is throttled to once an hour on fresh public repos, which is way too slow for the Telegram callback flow. Instead, we use [cron-job.org](https://cron-job.org) (free) to hit GitHub's `workflow_dispatch` API every minute.

**a. Create a fine-grained GitHub PAT**

1. Go to https://github.com/settings/personal-access-tokens/new
2. **Token name**: `cron-job-org-trigger`
3. **Expiration**: 1 year (or shorter — you'll need to renew it)
4. **Repository access**: Only select repositories → check this repo
5. **Repository permissions** → scroll to **Actions** → set to **Read and write**. Everything else stays at "No access."
6. **Generate token**. Copy it immediately — looks like `github_pat_...`. GitHub only shows it once.

**b. (Optional) Test the PAT before configuring cron-job.org**

In your terminal:
```sh
curl -X POST \
  -H "Authorization: Bearer YOUR_PAT_HERE" \
  -H "Accept: application/vnd.github+json" \
  -d '{"ref":"main"}' \
  https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO/actions/workflows/watch-files.yml/dispatches
```

Expected: empty body + HTTP 204 (success). A new workflow run should appear in the Actions tab within ~5 sec. A 401/403 means the PAT scoping is wrong.

**c. Create the cron-job.org cron**

1. Sign up at https://cron-job.org (email + password, no card).
2. **Create cronjob**, fill in:
   - **Title**: `job-watch trigger`
   - **URL**: `https://api.github.com/repos/YOUR_USERNAME/YOUR_REPO/actions/workflows/watch-files.yml/dispatches`
   - **Schedule**: Every minute (1-min interval)
3. **Advanced** tab:
   - **Request method**: `POST`
   - **Request headers** (add three):
     - `Authorization` → `Bearer YOUR_PAT_HERE`
     - `Accept` → `application/vnd.github+json`
     - `Content-Type` → `application/json`
   - **Request body**: `{"ref":"main"}`
4. **Create cronjob**.

After ~60 sec you should see new `workflow_dispatch` runs appearing in the Actions tab on the minute, every minute.

### 6. Trigger the first run
- Actions tab → Watch files in external repo → Run workflow. The first run **bootstraps** state (snapshots all current rows) without sending alerts. Subsequent runs alert on new rows only.
- After that, cron-job.org handles everything. You'll get DMs as new SWE listings hit the watched repos, and your apply-button taps will log to the sheet within 1-2 min.

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

Tell them to clone this repo, follow the Setup section (all 6 steps including their own cron-job.org account) with **their own** Telegram bot, sheet, secrets, and PAT, and push to **their** fork. Each person's runs are independent — their state lives in their fork.

## Maintenance

### PAT renewal (~yearly)
The fine-grained GitHub PAT you created for cron-job.org expires after the period you chose (default suggestion: 1 year). GitHub will email you a heads-up before expiration. To renew:
1. Generate a new PAT with the same `Actions: Read and write` scope on this repo (Step 5a).
2. Open your cron-job.org cronjob → Advanced → Headers → update the `Authorization` value with the new `Bearer github_pat_...` value.
3. Save. Within 60 sec the next firing uses the new token; you can verify by watching for fresh runs in the Actions tab.

If you forget to renew before expiry, cron-job.org will start getting 401 responses from GitHub. The bot doesn't break — the GH `schedule:` fallback still fires hourly — but the apply-button reverts to slow.

### If cron-job.org has an outage
The GH `schedule:` entries in `watch-files.yml` keep firing (heavily throttled but functional). The bot stays operational at degraded latency — alerts and apply-button taps process within an hour instead of within a minute. Investigate cron-job.org's status and re-enable when it's back up.

### Rotating secrets
If any secret leaks (bot token, service account JSON, PAT):
- **`TELEGRAM_BOT_TOKEN`**: DM @BotFather → `/revoke` → pick your bot → it gives you a new token. Update the GitHub secret.
- **`GOOGLE_SERVICE_ACCOUNT_JSON`**: In GCP console, delete the leaked key from the service account and create a new JSON key. Update the GitHub secret.
- **PAT** (for cron-job.org): In GitHub Settings → Personal access tokens → revoke the leaked one. Generate a new PAT and update the `Authorization` header in cron-job.org.
