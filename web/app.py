"""Local dashboard for job-watch. Read-only connection-status view."""
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, render_template

load_dotenv()

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WATCHER_STATE = REPO_ROOT / ".watcher_state.json"
BOT_STATE = REPO_ROOT / ".bot_state.json"

# Hardcoded to match WATCHERS in .github/workflows/watch-files.yml.
# v2 idea: parse from the YAML to avoid drift.
WATCHERS = [
    {
        "label": "Simplify Repo",
        "owner": "SimplifyJobs",
        "repo": "Summer2026-Internships",
        "branch": "dev",
        "file": "README-Off-Season.md",
    },
    {
        "label": "Vansh Repo",
        "owner": "vanshb03",
        "repo": "Summer2027-Internships",
        "branch": "dev",
        "file": "OFFSEASON_README.md",
    },
]


def env(name, default=None):
    return os.environ.get(name) or default


def urlopen_with_retry(req, attempts=3, timeout=10):
    for i in range(attempts):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except (urllib.error.URLError, ConnectionResetError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                raise
            if i + 1 == attempts:
                raise
            time.sleep(2 ** i)


def gh_request(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "job-watch-dashboard",
    }
    token = env("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def gh_get_json(url):
    with urlopen_with_retry(gh_request(url)) as resp:
        return json.loads(resp.read().decode())


def parse_sa_json():
    """Return service-account info dict, or raise. Accepts inline JSON or path."""
    sa = env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
    try:
        return json.loads(sa)
    except json.JSONDecodeError:
        pass
    if not os.path.isfile(sa):
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is neither valid JSON nor a path to a file"
        )
    with open(sa) as f:
        return json.load(f)


def card(status, summary, **details):
    """status: 'ok' | 'warn' | 'fail'. details are rendered in the expandable section."""
    return {
        "status": status,
        "summary": summary,
        "details": {k: v for k, v in details.items() if v not in (None, "", [])},
    }


# ---------- Integration checks ----------

def check_telegram():
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token:
        return card("fail", "TELEGRAM_BOT_TOKEN not set",
                    hint="Add it to your .env file. Token comes from @BotFather.")

    # getMe
    try:
        with urlopen_with_retry(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getMe"
        )) as resp:
            me = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return card("fail", f"getMe returned HTTP {e.code}",
                    hint="Token may be invalid or revoked. Regenerate via @BotFather → /revoke.")
    except Exception as e:
        return card("fail", f"getMe failed: {type(e).__name__}: {e}")

    if not me.get("ok"):
        return card("fail", "Telegram API returned ok=false", response=me)

    bot = me["result"]

    # webhook status
    webhook_url = ""
    try:
        with urlopen_with_retry(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getWebhookInfo"
        )) as resp:
            wh = json.loads(resp.read().decode())
        webhook_url = (wh.get("result") or {}).get("url", "")
    except Exception:
        webhook_url = "(could not check)"

    # chat reachability
    chat_type = "(not configured)"
    if chat_id:
        try:
            with urlopen_with_retry(urllib.request.Request(
                f"https://api.telegram.org/bot{token}/getChat?chat_id={urllib.parse.quote(chat_id)}"
            )) as resp:
                chat_data = json.loads(resp.read().decode())
            if chat_data.get("ok"):
                chat_type = chat_data["result"].get("type", "?")
            else:
                return card("fail", f"getChat returned ok=false",
                            hint="DM your bot to initiate the chat, then retry.",
                            response=chat_data)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:200]
            return card("fail", f"TELEGRAM_CHAT_ID unreachable (HTTP {e.code})",
                        hint="DM your bot first; chat ID must be a chat the bot can see.",
                        response=body)

    warnings = []
    status_level = "ok"
    summary_suffix = ""
    if webhook_url:
        warnings.append(f"Webhook is set ({webhook_url}). getUpdates will fail.")
        status_level = "warn"
        summary_suffix = " (webhook is set — getUpdates polling will conflict)"

    return card(
        status_level,
        f"Bot @{bot.get('username')} ready" + summary_suffix,
        bot_username=bot.get("username"),
        bot_id=bot.get("id"),
        bot_name=bot.get("first_name"),
        chat_id=chat_id or "(not set)",
        chat_type=chat_type,
        webhook_url=webhook_url or "(none — good)",
        warnings=warnings,
    )


def check_gcp_service_account():
    try:
        info = parse_sa_json()
    except Exception as e:
        return card("fail", str(e),
                    hint="Paste the full SA JSON content into GOOGLE_SERVICE_ACCOUNT_JSON, or a path to a JSON file.")

    return card(
        "ok",
        f"SA: {info.get('client_email', '?')}",
        client_email=info.get("client_email"),
        project_id=info.get("project_id"),
        private_key_id=(info.get("private_key_id") or "")[:12] + "…",
        hint=f"Your sheet must be shared with {info.get('client_email')} as Editor.",
    )


def check_sheets():
    sheet_id = env("APPLICATIONS_SHEET_ID")
    if not sheet_id:
        return card("fail", "APPLICATIONS_SHEET_ID not set",
                    hint="Add your Google Sheet ID from the URL: docs.google.com/spreadsheets/d/<ID>/edit")
    try:
        info = parse_sa_json()
    except Exception as e:
        return card("fail", f"Cannot read SA JSON: {e}")

    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GoogleRequest
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
        )
        creds.refresh(GoogleRequest())
    except Exception as e:
        return card("fail", f"SA auth failed: {type(e).__name__}: {e}",
                    hint="The service account credentials may be invalid or revoked.")

    try:
        req = urllib.request.Request(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}",
            headers={"Authorization": f"Bearer {creds.token}"},
        )
        with urlopen_with_retry(req) as resp:
            sheet = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code == 403:
            hint = f"Share the sheet with {info.get('client_email')} as Editor."
        elif e.code == 404:
            hint = "Sheet ID is wrong — check the URL of your spreadsheet."
        else:
            hint = "Check Sheets API is enabled in your GCP project."
        return card("fail", f"Sheets API returned HTTP {e.code}",
                    hint=hint, response=body, sa_email=info.get("client_email"))
    except Exception as e:
        return card("fail", f"Sheets fetch failed: {type(e).__name__}: {e}")

    title = sheet.get("properties", {}).get("title", "?")
    sheets_list = sheet.get("sheets", [])
    app_tab = next(
        (s for s in sheets_list if s.get("properties", {}).get("title") == "Applications"),
        None,
    )
    row_count = None
    if app_tab:
        row_count = app_tab.get("properties", {}).get("gridProperties", {}).get("rowCount")

    if not app_tab:
        return card("warn", f"Connected to '{title}' but no 'Applications' tab",
                    hint="Bot writes to a tab named 'Applications'. Add one or set APPLICATIONS_SHEET_RANGE.",
                    sheet_title=title,
                    sheet_url=f"https://docs.google.com/spreadsheets/d/{sheet_id}",
                    available_tabs=[s.get("properties", {}).get("title") for s in sheets_list])

    return card(
        "ok",
        f"Connected to '{title}'",
        sheet_title=title,
        sheet_url=f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        applications_tab_row_count=row_count,
        all_tabs=[s.get("properties", {}).get("title") for s in sheets_list],
    )


def check_github_actions():
    bot_repo = env("BOT_REPO")
    if not bot_repo:
        return card("fail", "BOT_REPO not set",
                    hint="Set BOT_REPO=Mekski/job-watch (or your fork) in .env")

    try:
        data = gh_get_json(
            f"https://api.github.com/repos/{bot_repo}/actions/workflows/watch-files.yml/runs?per_page=10"
        )
    except urllib.error.HTTPError as e:
        return card("fail", f"GH API returned HTTP {e.code}",
                    hint="Check BOT_REPO is correct (owner/repo). Add GITHUB_TOKEN if rate-limited.")
    except Exception as e:
        return card("fail", f"GH API call failed: {type(e).__name__}: {e}")

    runs = data.get("workflow_runs", [])
    if not runs:
        return card("fail", "No runs found",
                    hint="Has the workflow ever been triggered? Run it once from Actions tab.")

    success_count = sum(1 for r in runs if r.get("conclusion") == "success")
    last_run = runs[0]
    last_dt = datetime.fromisoformat(last_run["created_at"].replace("Z", "+00:00"))
    age_sec = int((datetime.now(timezone.utc) - last_dt).total_seconds())

    rate_warning = None
    if not env("GITHUB_TOKEN"):
        rate_warning = "Unauthenticated GH API. Add GITHUB_TOKEN to .env for 5000/hr instead of 60/hr."

    if age_sec > 300:
        status_level = "fail"
        summary = f"Last run {age_sec}s ago — cron-job.org may not be firing"
    elif success_count < 8:
        status_level = "warn"
        summary = f"Last run {age_sec}s ago — only {success_count}/10 recent runs successful"
    else:
        status_level = "ok"
        summary = f"Last run {age_sec}s ago, {success_count}/10 successful"

    return card(
        status_level,
        summary,
        last_run_at=last_run["created_at"],
        last_run_conclusion=last_run.get("conclusion"),
        last_run_event=last_run.get("event"),
        last_run_url=last_run.get("html_url"),
        success_rate=f"{success_count}/10",
        all_runs_url=f"https://github.com/{bot_repo}/actions/workflows/watch-files.yml",
        rate_limit_note=rate_warning,
    )


def check_cron_inference():
    bot_repo = env("BOT_REPO")
    if not bot_repo:
        return card("fail", "BOT_REPO not set",
                    hint="Need BOT_REPO to infer cron-job.org cadence from GH runs.")

    try:
        data = gh_get_json(
            f"https://api.github.com/repos/{bot_repo}/actions/workflows/watch-files.yml/runs?per_page=1"
        )
    except Exception as e:
        return card("fail", f"Could not check: {type(e).__name__}: {e}")

    runs = data.get("workflow_runs", [])
    if not runs:
        return card("fail", "No runs to infer from")

    last_dt = datetime.fromisoformat(runs[0]["created_at"].replace("Z", "+00:00"))
    age_sec = int((datetime.now(timezone.utc) - last_dt).total_seconds())

    if age_sec < 90:
        status_level = "ok"
        summary = f"Last fire {age_sec}s ago — every-minute cadence holding"
    elif age_sec < 300:
        status_level = "warn"
        summary = f"Last fire {age_sec}s ago — slower than expected"
    else:
        status_level = "fail"
        summary = f"Last fire {age_sec}s ago — likely outage or expired PAT"

    return card(
        status_level,
        summary,
        last_fire_seconds_ago=age_sec,
        last_event=runs[0].get("event"),
        cron_job_dashboard="https://console.cron-job.org/jobs",
        note="cron-job.org has no public API to query directly. This is inferred from GH Actions run history.",
        hint=(
            "If red: check cron-job.org dashboard execution history. "
            "Common causes: PAT expired, cron paused, GH rate-limiting."
        ) if status_level != "ok" else None,
    )


def check_watched_repos():
    state = None
    if WATCHER_STATE.exists():
        try:
            state = json.loads(WATCHER_STATE.read_text())
        except json.JSONDecodeError as e:
            return card("fail", f".watcher_state.json is invalid JSON: {e}")

    sources = []
    overall = "ok"
    for w in WATCHERS:
        repo_key = f"{w['owner']}/{w['repo']}"
        item = {
            "label": w["label"],
            "repo": repo_key,
            "branch": w["branch"],
            "file": w["file"],
            "upstream_url": f"https://github.com/{repo_key}/blob/{w['branch']}/{w['file']}",
        }

        saved = (state or {}).get(repo_key)
        if not saved:
            item["status"] = "fail"
            item["state"] = "not bootstrapped — bot has never seen this source"
            item["last_sha"] = "—"
            item["row_count"] = 0
            sources.append(item)
            overall = "fail"
            continue

        last_sha = saved.get("last_sha", "")
        item["last_sha"] = last_sha[:8] if last_sha else "?"
        item["row_count"] = len(saved.get("rows") or [])

        try:
            commit = gh_get_json(
                f"https://api.github.com/repos/{repo_key}/commits/{w['branch']}"
            )
            upstream_sha = commit["sha"]
            if last_sha == upstream_sha:
                item["status"] = "ok"
                item["state"] = "snapshot matches upstream"
            else:
                item["status"] = "warn"
                item["state"] = f"upstream advanced to {upstream_sha[:8]}"
                item["upstream_pushed_at"] = commit["commit"]["committer"]["date"]
                if overall == "ok":
                    overall = "warn"
        except Exception as e:
            item["status"] = "warn"
            item["state"] = f"could not query upstream: {type(e).__name__}"
            if overall == "ok":
                overall = "warn"

        sources.append(item)

    ok_count = sum(1 for s in sources if s["status"] == "ok")
    return card(
        overall,
        f"{ok_count}/{len(sources)} sources current",
        sources=sources,
    )


def check_bot_state():
    if not BOT_STATE.exists():
        return card("fail", ".bot_state.json missing",
                    hint="Bot has never run. Trigger the workflow once from Actions tab.")
    try:
        data = json.loads(BOT_STATE.read_text())
    except json.JSONDecodeError as e:
        return card("fail", f".bot_state.json is invalid JSON: {e}",
                    hint="State file is corrupt. May need to manually fix or re-bootstrap.")

    pending = data.get("pending", {}) or {}
    applied = data.get("applied", {}) or {}
    offset = (data.get("telegram") or {}).get("last_update_id", 0)

    last_applied_at = max(
        (j.get("applied_at", "") for j in applied.values()),
        default="",
    )

    status_level = "ok"
    notes = []
    if len(pending) > 5 and len(applied) == 0:
        status_level = "warn"
        notes.append("No applies despite multiple pending — apply-button flow may be broken")

    summary = f"{len(pending)} pending · {len(applied)} applied"
    if last_applied_at:
        summary += f" · last applied {last_applied_at}"

    return card(
        status_level,
        summary,
        pending_count=len(pending),
        applied_count=len(applied),
        last_applied_at=last_applied_at or "(never)",
        telegram_offset=offset,
        notes=notes,
    )


# ---------- Flask ----------

app = Flask(__name__)


@app.route("/")
def index():
    cards_list = [
        ("Telegram bot", check_telegram()),
        ("Google Sheets", check_sheets()),
        ("GCP service account", check_gcp_service_account()),
        ("GitHub Actions", check_github_actions()),
        ("cron-job.org cadence", check_cron_inference()),
        ("Watched job repos", check_watched_repos()),
        ("Bot state", check_bot_state()),
    ]
    overall_ok = all(c[1]["status"] == "ok" for c in cards_list)
    overall_has_fail = any(c[1]["status"] == "fail" for c in cards_list)
    return render_template(
        "index.html",
        cards=cards_list,
        overall_ok=overall_ok,
        overall_has_fail=overall_has_fail,
        now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


if __name__ == "__main__":
    # Port 5001 to avoid macOS AirPlay receiver (which hogs 5000).
    app.run(host="127.0.0.1", port=5001, debug=True)
