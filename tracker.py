#!/usr/bin/env python3
"""
tracker.py - Time-tracker service for the Pi (runs alongside the list app).

Every N minutes it pings you (via Pushover) asking what you've been up to.
You reply from your phone at http://<pi>:5050 (over Tailscale). Entries are
logged to tracker_log.json. The pygame display reads that log to show recent
check-ins and the next check-in countdown under the todo/shopping lists.

Secrets (Pushover keys, Gmail) live in tracker_config.py (gitignored).
Copy tracker_config.example.py -> tracker_config.py and fill it in.
"""
import json
import math
import os
import shutil
import smtplib
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from threading import Event, Thread

from flask import Flask, jsonify, render_template, request

# --- Load config (with safe fallbacks if tracker_config.py is missing) ---
try:
    import tracker_config as cfg
except ImportError:
    class cfg:  # noqa: N801 - fallback stub
        PUSHOVER_USER = ""
        PUSHOVER_TOKEN = ""
        GMAIL_FROM = ""
        GMAIL_PASS = ""
        RECIPIENT = ""
        CHECKIN_INTERVAL_MIN = 30
        FOLLOWUP_MIN = 5
        HABIT_REMINDER_HOUR = 20  # 8pm
        CATEGORIES = ["Work", "Break", "Meal", "Errands", "Health",
                      "Personal", "TikTok", "YouTube"]
        PI_BASE_URL = ""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "tracker_log.json")
STATE_FILE = os.path.join(BASE_DIR, "tracker_state.json")
APPUSE_FILE = os.path.join(BASE_DIR, "app_usage.json")  # per-app session times
MOOD_FILE = os.path.join(BASE_DIR, "mood_log.json")     # mood check-ins (1-5)
SLEEP_FILE = os.path.join(BASE_DIR, "sleep_log.json")   # sleep/wake events
WATER_FILE = os.path.join(BASE_DIR, "water_log.json")   # water bottles (1 L) per day
METRIC_FILE = os.path.join(BASE_DIR, "metric_log.json")  # daily numeric metric (weight)
JOURNAL_FILE = os.path.join(BASE_DIR, "journal_log.json")  # one line per day
USAGE_MIN_FILE = os.path.join(BASE_DIR, "usage_minutes.json")  # phone: minute-of-day -> app per date
MAC_USAGE_MIN_FILE = os.path.join(BASE_DIR, "mac_usage_minutes.json")  # Mac: minute-of-day -> app per date
DB_PATH = os.path.join(BASE_DIR, "lists.db")  # habits live in the list app's DB

# ======================================================================
# Configuration. Every value falls back to a sensible default, so
# tracker_config.py is optional; set any of these there to override.
# See tracker_config.example.py for the full list with descriptions.
# ======================================================================

# --- Check-ins ---
INTERVAL = int(getattr(cfg, "CHECKIN_INTERVAL_MIN", 30)) * 60
FOLLOWUP = int(getattr(cfg, "FOLLOWUP_MIN", 5)) * 60
# Hour (0-23) to send the evening reminder about unchecked habits. -1 disables.
HABIT_REMINDER_HOUR = int(getattr(cfg, "HABIT_REMINDER_HOUR", 20))
# Quick-pick categories for check-ins (buttons + tags + daily summary).
CATEGORIES = list(getattr(cfg, "CATEGORIES",
                          ["Work", "Break", "Meal", "Errands", "Health",
                           "Personal", "TikTok", "YouTube"]))
# Categories shown as a plain count ("TikTok x9") instead of estimated time,
# since app-opens don't imply a full interval of activity.
COUNT_ONLY_CATEGORIES = set(getattr(cfg, "COUNT_ONLY_CATEGORIES",
                                    ["TikTok", "YouTube"]))
# Daily per-app screen-time limit (minutes). Crossing it fires one Pushover
# alert per app per day. Set to 0 to disable.
APP_TIME_LIMIT_MIN = int(getattr(cfg, "APP_TIME_LIMIT_MIN", 60))

# Focus vs distraction classification for the productive/distracting ratio.
# By default: Mac apps (prefix "Mac:") count as focused work; the app-open
# categories (TikTok/YouTube) count as distraction. Override in config with
# FOCUS_APPS / DISTRACTION_APPS (exact names) if you want finer control.
FOCUS_APPS = set(getattr(cfg, "FOCUS_APPS", []))
DISTRACTION_APPS = set(getattr(cfg, "DISTRACTION_APPS",
                                ["TikTok", "YouTube"]))
# Hour (0-23) to send the end-of-day summary push. -1 disables.
DAILY_SUMMARY_HOUR = int(getattr(cfg, "DAILY_SUMMARY_HOUR", 21))

# Weekly review push: a 7-day recap. WEEKLY_REVIEW_DOW is the weekday to send
# (0=Mon .. 6=Sun; default 6=Sunday), WEEKLY_REVIEW_HOUR the hour. -1 disables.
WEEKLY_REVIEW_DOW = int(getattr(cfg, "WEEKLY_REVIEW_DOW", 6))
WEEKLY_REVIEW_HOUR = int(getattr(cfg, "WEEKLY_REVIEW_HOUR", 19))

# Nightly backups: copy runtime data (JSON logs + lists.db) into a timestamped
# folder under BACKUP_DIR once a day at BACKUP_HOUR. Keeps the last
# BACKUP_KEEP snapshots. Set BACKUP_HOUR = -1 to disable.
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
BACKUP_HOUR = int(getattr(cfg, "BACKUP_HOUR", 3))
BACKUP_KEEP = int(getattr(cfg, "BACKUP_KEEP", 14))

# Bedtime wind-down nudge: when it's within WINDDOWN_WINDOW_MIN before your
# average bedtime (learned from sleep data) and you're in a distraction app,
# send a "wind down" Pushover (once per night). Set WINDDOWN_WINDOW_MIN = 0
# to disable. AVG bedtime needs at least a few logged nights to kick in.
WINDDOWN_WINDOW_MIN = int(getattr(cfg, "WINDDOWN_WINDOW_MIN", 30))

# Check-in compliance: the hours you're expected to be logging check-ins.
# Expected count = elapsed check-in intervals within these hours so far today.
COMPLIANCE_START_HOUR = int(getattr(cfg, "COMPLIANCE_START_HOUR", 8))
COMPLIANCE_END_HOUR = int(getattr(cfg, "COMPLIANCE_END_HOUR", 22))
# You're "behind" if compliance drops below this fraction (0-1).
COMPLIANCE_BEHIND_BELOW = float(getattr(cfg, "COMPLIANCE_BEHIND_BELOW", 0.7))

# "Close the app" nudge: if a tracked app stays open longer than this many
# minutes in one sitting, send a Pushover telling you to close it. Re-nudges
# every APP_OPEN_RENUDGE_MIN while you're still in it. Set APP_OPEN_NUDGE_MIN=0
# to disable. Applies to the app-open categories (TikTok/YouTube) by default.
APP_OPEN_NUDGE_MIN = int(getattr(cfg, "APP_OPEN_NUDGE_MIN", 5))
APP_OPEN_RENUDGE_MIN = int(getattr(cfg, "APP_OPEN_RENUDGE_MIN", 5))
NUDGE_APPS = set(getattr(cfg, "NUDGE_APPS", ["TikTok", "YouTube"]))

# Hydration: daily goal in 1 L bottles. Tap logs a bottle; the board/display
# show a progress bar toward this goal.
WATER_GOAL = int(getattr(cfg, "WATER_GOAL", 3))
# Daily numeric metric (e.g. weight). METRIC_LABEL/UNIT are display-only.
METRIC_LABEL = str(getattr(cfg, "METRIC_LABEL", "Weight"))
METRIC_UNIT = str(getattr(cfg, "METRIC_UNIT", "lb"))

# Google Health API (Fitbit data). Set these in tracker_config.py after running
# health_auth.py. Leave blank to disable the health integration entirely.
GOOGLE_HEALTH_CLIENT_ID = str(getattr(cfg, "GOOGLE_HEALTH_CLIENT_ID", ""))
GOOGLE_HEALTH_CLIENT_SECRET = str(getattr(cfg, "GOOGLE_HEALTH_CLIENT_SECRET", ""))
GOOGLE_HEALTH_REFRESH_TOKEN = str(getattr(cfg, "GOOGLE_HEALTH_REFRESH_TOKEN", ""))
# Daily steps goal for the progress bar.
STEPS_GOAL = int(getattr(cfg, "STEPS_GOAL", 10000))

# The Pi's reachable tracker URL, so Pushover reminders can deep-link to the
# check-in page. Defaults to the Pi 5's Tailscale address; override in config.
PI_BASE_URL = getattr(cfg, "PI_BASE_URL", "http://100.102.96.42:5050")

# --- end configuration ---


def classify_app(name):
    """Return 'focus', 'distraction', or 'neutral' for an app/category name."""
    if name in DISTRACTION_APPS:
        return "distraction"
    if name in FOCUS_APPS:
        return "focus"
    if name.startswith("Mac:"):
        return "focus"
    return "neutral"


app = Flask(__name__)


# ---------- helpers ----------

# Pushover free tier = 10,000 messages/month. Cap per day at an even share of
# that (10000 / days-in-this-month) so a runaway nag loop can't blow the month.
PUSHOVER_MONTHLY_LIMIT = 10000
PUSHOVER_COUNT_FILE = os.path.join(BASE_DIR, "pushover_count.json")


def _daily_pushover_budget():
    import calendar
    now = datetime.now()
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    return PUSHOVER_MONTHLY_LIMIT // days_in_month


def _load_pushover_count():
    try:
        with open(PUSHOVER_COUNT_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _pushover_budget_ok():
    today = date.today().isoformat()
    count = _load_pushover_count().get(today, 0)
    return count < _daily_pushover_budget()


def _pushover_budget_increment():
    today = date.today().isoformat()
    data = _load_pushover_count()
    # Keep only today's key so the file doesn't grow.
    data = {today: data.get(today, 0) + 1}
    try:
        with open(PUSHOVER_COUNT_FILE, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def send_pushover(message, title="Time Tracker", url=None, url_title=None,
                  priority=0):
    if not cfg.PUSHOVER_USER or not cfg.PUSHOVER_TOKEN:
        return  # notifications disabled
    # Stay within Pushover's free 10,000/month by capping each day at
    # 10000 / days_in_current_month. Prevents runaway nag loops from blowing
    # the whole monthly allowance in one day.
    if not _pushover_budget_ok():
        print("Pushover daily budget reached - skipping notification.")
        return
    payload = {
        "token": cfg.PUSHOVER_TOKEN, "user": cfg.PUSHOVER_USER,
        "title": title, "message": message, "sound": "vibrate",
    }
    if priority:
        payload["priority"] = priority   # 1 = high (bypasses quiet hours)
    # Default every notification to open the tracker page when tapped, unless
    # the caller supplied a specific link. Requires PI_BASE_URL to be set.
    if not url and PI_BASE_URL:
        url = PI_BASE_URL.rstrip("/") + "/"
        url_title = url_title or "Open tracker"
    if url:
        payload["url"] = url
        if url_title:
            payload["url_title"] = url_title
    data = urllib.parse.urlencode(payload).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://api.pushover.net/1/messages.json", data=data),
            timeout=10,
        )
        _pushover_budget_increment()
    except Exception as e:
        print(f"Pushover error: {e}")


def _page_url(path=""):
    """Build a link to a tracker page (e.g. '/usage'). Empty if no base URL."""
    if not PI_BASE_URL:
        return None
    return PI_BASE_URL.rstrip("/") + "/" + path.lstrip("/")


# --- Google Health API access ---
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_HEALTH_BASE = "https://health.googleapis.com/v4"
# In-memory access-token cache: {"token": str, "expires_at": epoch}.
_gh_token = {"token": None, "expires_at": 0}


def google_health_enabled():
    return bool(GOOGLE_HEALTH_CLIENT_ID and GOOGLE_HEALTH_CLIENT_SECRET
                and GOOGLE_HEALTH_REFRESH_TOKEN)


def google_health_access_token():
    """Return a valid access token, refreshing via the stored refresh token if
    the cached one is missing or within 60s of expiry. Returns None if the
    integration isn't configured or the refresh fails."""
    if not google_health_enabled():
        return None
    now = time.time()
    if _gh_token["token"] and now < _gh_token["expires_at"] - 60:
        return _gh_token["token"]
    data = urllib.parse.urlencode({
        "client_id": GOOGLE_HEALTH_CLIENT_ID,
        "client_secret": GOOGLE_HEALTH_CLIENT_SECRET,
        "refresh_token": GOOGLE_HEALTH_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    try:
        req = urllib.request.Request(
            GOOGLE_TOKEN_ENDPOINT, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.load(r)
        _gh_token["token"] = tok.get("access_token")
        _gh_token["expires_at"] = now + int(tok.get("expires_in", 3600))
        return _gh_token["token"]
    except Exception as e:
        print(f"Google Health token refresh failed: {e}")
        return None


def _civil(dt):
    """A CivilDateTime for the dailyRollUp range. CivilDateTime nests a
    required google.type.Date under `date`; `time` defaults to midnight when
    omitted, which is exactly the day boundary we want."""
    return {"date": {"year": dt.year, "month": dt.month, "day": dt.day}}


def _gh_daily_rollup_raw(data_type, token, day=None, source_family=None):
    """POST a 1-day dailyRollUp and return the full parsed JSON response (or a
    dict with an 'error' key). Used by both the normal fetch and the debug
    endpoint so we can see exactly what the API returns."""
    day = day or date.today()
    nextday = day + timedelta(days=1)
    url = f"{GOOGLE_HEALTH_BASE}/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp"
    payload = {
        "range": {"start": _civil(day), "end": _civil(nextday)},
        "windowSizeDays": 1,
    }
    if source_family:
        payload["dataSourceFamily"] = f"users/me/dataSourceFamilies/{source_family}"
    body = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"Google Health {data_type} rollup HTTP {e.code}: {detail[:300]}")
        return {"error": f"HTTP {e.code}", "detail": detail[:500]}
    except Exception as e:
        print(f"Google Health {data_type} rollup failed: {e}")
        return {"error": str(e)}


def _gh_daily_rollup(data_type, token, day=None):
    """Return the single rollup data point dict, or None."""
    resp = _gh_daily_rollup_raw(data_type, token, day)
    points = resp.get("rollupDataPoints", []) if isinstance(resp, dict) else []
    return points[0] if points else None


def _gh_sleep_raw(token, hours_back=48):
    """List sleep sessions whose end time falls within the last `hours_back`
    hours. Returns the parsed JSON (dataPoints ordered newest-first) or an
    error dict."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours_back)
    # RFC-3339 (UTC 'Z') timestamps for the sleep end_time filter.
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    flt = (f'sleep.interval.end_time >= "{start.strftime(fmt)}" AND '
           f'sleep.interval.end_time < "{now.strftime(fmt)}"')
    url = (f"{GOOGLE_HEALTH_BASE}/users/me/dataTypes/sleep/dataPoints?"
           + urllib.parse.urlencode({"filter": flt, "pageSize": 25}))
    try:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"Google Health sleep list HTTP {e.code}: {detail[:300]}")
        return {"error": f"HTTP {e.code}", "detail": detail[:500]}
    except Exception as e:
        print(f"Google Health sleep list failed: {e}")
        return {"error": str(e)}


def _parse_sleep_session(dp):
    """From a sleep DataPoint, return {bedtime, waketime, duration_min} using
    the session interval. Times are ISO strings; duration in minutes."""
    sleep = dp.get("sleep") or {}
    interval = sleep.get("interval") or {}
    start = interval.get("startTime")
    end = interval.get("endTime")
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    dur = int((e - s).total_seconds() // 60)
    if dur <= 0 or dur > 20 * 60:
        return None
    return {"bedtime": start, "waketime": end, "duration_min": dur}


def sleep_last_night(token):
    """Return the most recent sleep session as {bedtime, waketime,
    duration_min} (ISO times), or None."""
    resp = _gh_sleep_raw(token)
    points = resp.get("dataPoints", []) if isinstance(resp, dict) else []
    # Ordered newest-first; take the first parseable session.
    for dp in points:
        parsed = _parse_sleep_session(dp)
        if parsed:
            return parsed
    return None


# Cache health data ~10 min to respect API rate limits.
_gh_data = {"data": None, "fetched_at": 0}
GH_CACHE_SEC = 600


def health_data(force=False, day=None):
    """Return {steps, steps_goal, resting_hr, active_minutes} for `day`
    (default today) from Google Health. Today's result is cached; a specific
    past date bypasses the cache. None if the integration is disabled."""
    if not google_health_enabled():
        return None
    now = time.time()
    is_today = day is None
    if (is_today and not force and _gh_data["data"] is not None
            and now < _gh_data["fetched_at"] + GH_CACHE_SEC):
        return _gh_data["data"]
    token = google_health_access_token()
    if not token:
        return _gh_data["data"] if is_today else None  # serve stale on auth fail

    result = {"steps": None, "steps_goal": STEPS_GOAL,
              "resting_hr": None, "active_minutes": None, "sleep": None}

    # Last night's sleep session (bedtime / wake / duration). Only fetched for
    # the "today" view (it looks back over the last ~48h regardless).
    if is_today:
        result["sleep"] = sleep_last_night(token)

    steps_pt = _gh_daily_rollup("steps", token, day)
    if steps_pt and "steps" in steps_pt:
        result["steps"] = int(steps_pt["steps"].get("countSum", 0))

    hr_pt = _gh_daily_rollup("daily-resting-heart-rate", token, day)
    if hr_pt and "restingHeartRatePersonalRange" in hr_pt:
        rng = hr_pt["restingHeartRatePersonalRange"]
        lo = rng.get("beatsPerMinuteMin")
        hi = rng.get("beatsPerMinuteMax")
        if lo is not None and hi is not None:
            result["resting_hr"] = int(round((lo + hi) / 2))
        elif lo is not None:
            result["resting_hr"] = int(round(lo))

    am_pt = _gh_daily_rollup("active-minutes", token, day)
    if am_pt and "activeMinutes" in am_pt:
        # ActiveMinutesRollupValue aggregates minutes; sum field name varies,
        # so pull the first numeric *_sum value present.
        am = am_pt["activeMinutes"]
        for k, v in am.items():
            if k.endswith("_sum") or k.endswith("Sum"):
                try:
                    result["active_minutes"] = int(round(float(v)))
                    break
                except (ValueError, TypeError):
                    pass

    if is_today:
        _gh_data["data"] = result
        _gh_data["fetched_at"] = now
    return result


def load_log():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_log(entries):
    with open(LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"is_awake": True}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def persist_runtime_state():
    """Write current runtime state (awake + next check-in time) so the
    pygame display, a separate process, can show the countdown."""
    save_state({"is_awake": is_awake, "next_checkin_time": next_checkin_time})


def log_entry(text, extra=None, category=None):
    entries = load_log()
    e = {"timestamp": datetime.now().isoformat(), "text": text}
    if category:
        e["category"] = category
    if extra:
        e.update(extra)
    entries.append(e)
    save_log(entries)


# ---------- state ----------

notification_pending = Event()

# Restore timer state across restarts so the auto-updater relaunching the app
# does NOT reset your check-in countdown. Resume the saved next check-in time
# if it's still in the future; otherwise start a fresh interval.
_saved = load_state()
is_awake = _saved.get("is_awake", True)
_saved_next = _saved.get("next_checkin_time")
if isinstance(_saved_next, (int, float)) and _saved_next > time.time():
    next_checkin_time = _saved_next
else:
    next_checkin_time = time.time() + INTERVAL


# ---------- background threads ----------

def timer_thread():
    global next_checkin_time, is_awake
    while True:
        while not is_awake:
            time.sleep(5)
        sleep_secs = next_checkin_time - time.time()
        for _ in range(max(0, int(sleep_secs))):
            if not is_awake:
                break
            time.sleep(1)
        if not is_awake:
            continue
        notification_pending.set()
        # Include a tappable link so you can jump straight to logging. If a
        # base URL is configured, deep-link to the page; otherwise no URL.
        cin_url = (PI_BASE_URL.rstrip("/") + "/") if PI_BASE_URL else None
        send_pushover("Check-in time! What have you been up to the last "
                      f"{INTERVAL // 60} minutes?",
                      url=cin_url, url_title="Log a check-in")
        next_checkin_time = time.time() + INTERVAL
        persist_runtime_state()
        for _ in range(FOLLOWUP):
            if not is_awake:
                break
            time.sleep(1)
        if notification_pending.is_set() and is_awake:
            send_pushover("Still waiting - what have you been up to?",
                          title="Time Tracker (follow-up)")


def midnight_email_thread():
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        time.sleep((next_midnight - now).total_seconds())
        send_daily_email()


def unchecked_habits_today():
    """Return the names of habits NOT marked done today. [] if none / no DB."""
    if not os.path.exists(DB_PATH):
        return []
    today = date.today().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        habits = conn.execute(
            "SELECT id, name FROM habits ORDER BY position, id"
        ).fetchall()
        done_ids = {
            r["habit_id"] for r in conn.execute(
                "SELECT habit_id FROM habit_log WHERE day = ?", (today,)
            ).fetchall()
        }
        conn.close()
        return [h["name"] for h in habits if h["id"] not in done_ids]
    except sqlite3.Error:
        return []


def habit_reminder_thread():
    """Once each evening at HABIT_REMINDER_HOUR, ping about unchecked habits."""
    if HABIT_REMINDER_HOUR < 0:
        return  # disabled
    last_sent_day = None
    while True:
        now = datetime.now()
        today = now.date().isoformat()
        if now.hour == HABIT_REMINDER_HOUR and last_sent_day != today:
            last_sent_day = today
            pending = unchecked_habits_today()
            if pending:
                names = ", ".join(pending)
                send_pushover(
                    f"{len(pending)} habit(s) left today: {names}",
                    title="Habit reminder",
                )
        time.sleep(60)  # check every minute


def send_daily_email():
    if not cfg.GMAIL_FROM or not cfg.GMAIL_PASS or not cfg.RECIPIENT:
        return  # email disabled
    today = datetime.now().strftime("%A, %B %d, %Y")
    today_str = datetime.now().date().isoformat()
    entries = load_log()
    today_entries = [e for e in entries if e["timestamp"].startswith(today_str)]
    lines = []
    for e in today_entries:
        t = datetime.fromisoformat(e["timestamp"]).strftime("%I:%M %p")
        lines.append(f"{t}  -  {e['text']}")
    body = "\n".join(lines) if lines else "No entries logged today."
    subject = f"Time Tracker - Daily Log for {today}"
    full_body = (f"Daily Summary for {today}\n{'=' * 40}\n\n"
                 f"Activity Log:\n{body}\n\n- Sent by Time Tracker")
    try:
        msg = MIMEMultipart()
        msg["From"] = cfg.GMAIL_FROM
        msg["To"] = cfg.RECIPIENT
        msg["Subject"] = subject
        msg.attach(MIMEText(full_body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(cfg.GMAIL_FROM, cfg.GMAIL_PASS)
            server.sendmail(cfg.GMAIL_FROM, [cfg.RECIPIENT], msg.as_string())
        print("Daily email sent!")
    except Exception as e:
        print(f"Email error: {e} - falling back to Pushover")
        send_pushover(body[:800], title=f"Time Tracker - {today}")


# ---------- routes ----------

@app.route("/")
def index():
    return render_template("tracker.html")


@app.route("/usage")
def usage_page():
    """Screen-usage clock page: 24 hourly pies, 60 minute-segments each."""
    return render_template("usage.html")


@app.route("/health")
def health_page():
    """Dedicated health dashboard (Fitbit/Google Health data)."""
    return render_template("health.html")


# Distinct colors per tracked app for the usage clock. Unknown/older entries
# (recorded before per-app tracking) fall back to USAGE_DEFAULT_COLOR.
USAGE_APP_COLORS = {
    "YouTube": "#e94560",   # red
    "TikTok": "#00d4ff",    # cyan
    "Instagram": "#c13584", # magenta
    "Twitter": "#1da1f2",
    "X": "#1da1f2",
    "Reddit": "#ff5700",
    "Snapchat": "#f5d90a",
}
USAGE_DEFAULT_COLOR = "#9b6dff"  # violet: used, but app unknown

# A rotating palette for Mac apps (assigned dynamically to the top apps of the
# day; the rest fall into "Other").
MAC_PALETTE = [
    "#00d4ff", "#2ecc71", "#f5a623", "#e94560", "#c13584",
    "#1da1f2", "#ff8c42", "#9b6dff",
]
MAC_TOP_N = 6  # distinct colors; apps beyond this become "Other"


@app.route("/usage-data")
def usage_data():
    """Return per-minute app usage for a date (default today) plus the color
    map. Usage: /usage-data[?date=YYYY-MM-DD][&source=phone|mac].
    'minutes' maps minute-of-day (as string) -> app name (may be '')."""
    day = request.args.get("date") or date.today().isoformat()
    source = (request.args.get("source") or "phone").lower()
    is_mac = source == "mac"

    path = MAC_USAGE_MIN_FILE if is_mac else USAGE_MIN_FILE
    raw = load_usage_minutes(path).get(day, {})
    # Support the legacy list format (bare minute indices, no app).
    if isinstance(raw, list):
        minute_map = {str(m): "" for m in raw}
    elif isinstance(raw, dict):
        minute_map = dict(raw)
    else:
        minute_map = {}

    if is_mac:
        # Strip the "Mac:" prefix for display, tally, then assign the top-N
        # apps distinct palette colors and lump the rest into "Other".
        clean = {}
        for k, v in minute_map.items():
            clean[k] = (v or "").replace("Mac:", "") or "Other"
        minute_map = clean
        tally = {}
        for a in minute_map.values():
            tally[a] = tally.get(a, 0) + 1
        ranked = sorted(tally, key=lambda a: -tally[a])
        colors = {}
        for i, a in enumerate(ranked[:MAC_TOP_N]):
            colors[a] = MAC_PALETTE[i % len(MAC_PALETTE)]
        colors["Other"] = USAGE_DEFAULT_COLOR
        # Fold non-top apps into "Other" in both the map and the tally.
        top_set = set(ranked[:MAC_TOP_N])
        per_app = {}
        for k, a in minute_map.items():
            key = a if a in top_set else "Other"
            minute_map[k] = key
            per_app[key] = per_app.get(key, 0) + 1
    else:
        per_app = {}
        for app_name in minute_map.values():
            key = app_name or "Other"
            per_app[key] = per_app.get(key, 0) + 1
        colors = dict(USAGE_APP_COLORS)
        colors["Other"] = USAGE_DEFAULT_COLOR

    # Extra screen/Mac info for the summary cards (only for today's view).
    app_today = None
    focus = None
    mac_week = None
    if day == date.today().isoformat():
        app_today = app_usage_today()
        focus = focus_distraction_today()
        week = app_usage_week()
        mac_apps = [a for a in week["apps"] if a["app"].startswith("Mac:")][:6]
        mac_week = {"days": week["days"], "apps": mac_apps}

    return jsonify({
        "date": day,
        "source": source,
        "minutes": minute_map,            # {"636": "Kiro"/"YouTube", ...}
        "total_minutes": len(minute_map),
        "per_app": per_app,               # {"Kiro": 40, ...}
        "colors": colors,                 # {"Kiro": "#00d4ff", ...}
        "default_color": USAGE_DEFAULT_COLOR,
        "app_today": app_today,           # [{app, seconds, opens, over_limit}]
        "focus": focus,                   # {focus_sec, distraction_sec, longest_focus_sec}
        "mac_week": mac_week,             # {days, apps:[{app, seconds, daily}]}
    })


@app.route("/health-data")
def health_data_route():
    """Google Health summary (steps/resting HR/active minutes). Defaults to
    today; ?date=YYYY-MM-DD queries a specific day (useful for testing since
    past days are fully synced). Returns {enabled: false} if not configured."""
    if not google_health_enabled():
        return jsonify({"enabled": False})
    day = None
    date_arg = request.args.get("date")
    if date_arg:
        try:
            day = datetime.strptime(date_arg, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"enabled": True, "error": "bad date, use YYYY-MM-DD"}), 400
    # Debug: return the raw API responses (kept for future troubleshooting).
    if request.args.get("debug") == "1":
        token = google_health_access_token()
        return jsonify({
            "enabled": True,
            "date": (day or date.today()).isoformat(),
            "token_ok": bool(token),
            "steps_all_sources": _gh_daily_rollup_raw("steps", token, day),
            "sleep_raw": _gh_sleep_raw(token),
        })
    d = health_data(force=request.args.get("force") == "1", day=day) or {}
    return jsonify({"enabled": True, "date": (day or date.today()).isoformat(), **d})


@app.route("/log", methods=["GET"])
def get_log():
    return jsonify(load_log())


@app.route("/log", methods=["POST"])
def add_entry():
    data = request.json or {}
    text = data.get("text", "").strip()
    category = (data.get("category") or "").strip() or None
    # A quick-pick with no free-text uses the category name as the text.
    if not text and category:
        text = category
    if not text:
        return jsonify({"error": "text required"}), 400
    log_entry(text, category=category)
    notification_pending.clear()
    return jsonify({"status": "ok"})


# Skip a duplicate quick-log of the same text within this many seconds
# (guards against iOS automations firing the request twice on app open).
QUICKLOG_DEBOUNCE_SEC = 60


def _recently_logged(text):
    """True if an identical check-in text was logged within the debounce window."""
    entries = load_log()
    if not entries:
        return False
    last = entries[-1]
    if last.get("text") != text:
        return False
    try:
        ts = datetime.fromisoformat(last["timestamp"])
        return (datetime.now() - ts).total_seconds() < QUICKLOG_DEBOUNCE_SEC
    except (ValueError, KeyError):
        return False


@app.route("/quicklog")
def quick_log():
    """GET endpoint so Pushover notification links / quick bookmarks can log a
    check-in in one tap: /quicklog?category=Work  (text defaults to category).
    De-duplicates rapid repeats so a double-firing automation logs only once."""
    category = (request.args.get("category") or "").strip() or None
    text = (request.args.get("text") or "").strip() or category
    if not text:
        return "Nothing logged (no text/category).", 400
    duplicate = _recently_logged(text)
    if not duplicate:
        log_entry(text, category=category)
        notification_pending.clear()
    status = "Already logged" if duplicate else "Logged"
    # Return a tiny friendly page since this opens in a browser.
    return (f"<html><body style='font-family:sans-serif;background:#1a1a2e;"
            f"color:#eaeaea;text-align:center;padding-top:3rem'>"
            f"<h2 style='color:#00d4ff'>{status}: {text}</h2>"
            f"<p>You can close this.</p></body></html>")


# --- App usage timing (open -> close = time spent in app) ---
# Fire /appstart?app=TikTok when the app opens, /appstop?app=TikTok when it
# closes. The server computes the duration and tallies today's total per app.
# Structure: {"open": {"TikTok": <start_epoch>}, "totals": {"YYYY-MM-DD": {"TikTok": seconds}}}
MAX_SESSION_SEC = 2 * 60 * 60  # a single sitting caps here; a session left
#                                open longer than this is auto-closed by the
#                                minute-crediting thread (forgotten /appstop).


def load_appuse():
    if os.path.exists(APPUSE_FILE):
        try:
            with open(APPUSE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"open": {}, "totals": {}}


def save_appuse(data):
    with open(APPUSE_FILE, "w") as f:
        json.dump(data, f)


def _tiny_page(msg):
    return (f"<html><body style='font-family:sans-serif;background:#1a1a2e;"
            f"color:#eaeaea;text-align:center;padding-top:3rem'>"
            f"<h2 style='color:#00d4ff'>{msg}</h2>"
            f"<p>You can close this.</p></body></html>")


# Map incoming ?app= names to a canonical category, tolerating casing and
# stray spaces so an iOS Shortcut sending "youtube" or "YouTube " still matches
# the exact-name checks used by nudges, limits, and focus/distraction.
_CANON_APPS = {c.lower(): c for c in CATEGORIES}


def normalize_app_name(raw):
    key = (raw or "").strip()
    return _CANON_APPS.get(key.lower(), key)


def load_usage_minutes(path=USAGE_MIN_FILE):
    """Return {'YYYY-MM-DD': {minute: app}} for the given usage-minutes file."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_usage_minutes(data, path=USAGE_MIN_FILE):
    with open(path, "w") as f:
        json.dump(data, f)


def mark_usage_minute(app, when=None, path=USAGE_MIN_FILE):
    """Record that `app` was used during the given datetime's minute-of-day
    (0..1439) for that date. Stored as {date: {minute: app}} with last-wins on
    same-minute app switches. Keeps ~60 days of history. No-op-safe on errors.
    `path` selects which usage file (phone default, or the Mac one)."""
    when = when or datetime.now()
    day = when.date().isoformat()
    minute = str(when.hour * 60 + when.minute)  # JSON keys must be strings
    try:
        data = load_usage_minutes(path)
        day_map = data.get(day)
        # Migrate an old-format day (a bare list of minutes) to the new map.
        if isinstance(day_map, list):
            day_map = {str(m): "" for m in day_map}
        elif not isinstance(day_map, dict):
            day_map = {}
        day_map[minute] = app  # last-wins
        data[day] = day_map
        # prune old days
        if len(data) > 60:
            for old in sorted(data)[:-60]:
                data.pop(old, None)
        save_usage_minutes(data, path)
    except (OSError, json.JSONDecodeError):
        pass


def check_app_limit(data, app_name, today):
    """Fire one Pushover per app per day when today's total crosses the limit.
    Returns True if an alert was sent. Mutates `data` (records 'alerted')."""
    if APP_TIME_LIMIT_MIN <= 0:
        return False
    total = data.get("totals", {}).get(today, {}).get(app_name, 0)
    if total < APP_TIME_LIMIT_MIN * 60:
        return False
    alerted = data.setdefault("alerted", {}).setdefault(today, [])
    if app_name in alerted:
        return False
    alerted.append(app_name)
    send_pushover(
        f"You've used {app_name} for {total // 60} min today "
        f"(limit {APP_TIME_LIMIT_MIN} min).",
        title="Screen-time limit reached",
        url=_page_url("/usage"), url_title="See screen usage")
    return True


# Ignore a repeat /appstart for the same app within this window (guards against
# iOS "Is Opened" automations firing the request twice). Kept short so genuine
# quick re-opens are still counted -- only true instant double-fires are dropped.
APPSTART_DEBOUNCE_SEC = 3


@app.route("/appstart")
def app_start():
    app_name = normalize_app_name(request.args.get("app"))
    if not app_name:
        return "Missing ?app=", 400
    data = load_appuse()
    now = time.time()
    prev_open = data["open"].get(app_name)
    today = date.today().isoformat()

    if prev_open is not None:
        gap = now - prev_open
        # A truly instant repeat (same fire twice) is a duplicate: ignore it.
        if gap < APPSTART_DEBOUNCE_SEC:
            save_appuse(data)
            return _tiny_page(f"{app_name}: already open")
        # A session was left open (missed /appstop). The minute-crediting thread
        # has already been banking its time while it was open, so we DON'T
        # re-bank the gap here (that used to double-count / inflate). Just start
        # a fresh session below.

    data["open"][app_name] = now
    # Track how many seconds of this open session we've already credited so the
    # minute-thread and /appstop never double-count. Starts at 0 for a new open.
    data.setdefault("credited", {})[app_name] = 0
    data.setdefault("opens", {}).setdefault(today, {})
    data["opens"][today][app_name] = data["opens"][today].get(app_name, 0) + 1
    save_appuse(data)
    # Stamp this minute as phone-used (for the screen-usage clock page).
    if classify_app(app_name) == "distraction":
        mark_usage_minute(app_name)
    return _tiny_page(f"Started: {app_name}")


@app.route("/appstop")
def app_stop():
    app_name = normalize_app_name(request.args.get("app"))
    if not app_name:
        return "Missing ?app=", 400
    data = load_appuse()
    start = data["open"].pop(app_name, None)
    already = data.get("credited", {}).pop(app_name, 0)
    if start is None:
        save_appuse(data)
        return _tiny_page(f"{app_name}: no open session")
    elapsed = int(time.time() - start)
    # The minute-thread already banked `already` seconds of this session; only
    # add whatever remains uncredited (avoids double-counting).
    remaining = elapsed - int(already)
    crossed_limit = False
    total_today = 0
    today = date.today().isoformat()
    if remaining > 0 and elapsed <= MAX_SESSION_SEC:
        data.setdefault("totals", {}).setdefault(today, {})
        data["totals"][today][app_name] = \
            data["totals"][today].get(app_name, 0) + remaining
    total_today = data.get("totals", {}).get(today, {}).get(app_name, 0)
    crossed_limit = check_app_limit(data, app_name, today)
    save_appuse(data)
    # Stamp every minute this session spanned as phone-used (covers short
    # sessions the minute-thread may not have caught).
    if classify_app(app_name) == "distraction" and 0 < elapsed <= MAX_SESSION_SEC:
        span = min(elapsed, MAX_SESSION_SEC)
        step = start
        while step <= start + span:
            mark_usage_minute(app_name, datetime.fromtimestamp(step))
            step += 60
    mins = max(1, elapsed // 60)
    suffix = "  (limit reached!)" if crossed_limit else ""
    return _tiny_page(f"{app_name}: +{mins}m{suffix}")


@app.route("/activeminute")
def active_minute():
    """Add a slice of active usage time for an app. Called by the Mac watcher
    while you're active in a given frontmost app.
    Usage: /activeminute?app=Mac:Chrome&seconds=10  (seconds defaults to 60)."""
    app_name = (request.args.get("app") or "").strip()
    if not app_name:
        return "Missing ?app=", 400
    try:
        secs = int(request.args.get("seconds", "60"))
    except ValueError:
        secs = 60
    secs = max(1, min(secs, 300))  # sanity clamp
    data = load_appuse()
    today = date.today().isoformat()
    data.setdefault("totals", {}).setdefault(today, {})
    data["totals"][today][app_name] = data["totals"][today].get(app_name, 0) + secs
    save_appuse(data)
    return "ok"


@app.route("/activebatch", methods=["POST", "GET"])
def active_batch():
    """Add several apps' active seconds in ONE request (one disk write).
    Body/params: JSON {"App": seconds, ...} via POST, or ?data=App:secs,App2:secs.
    Used by the Mac watcher to batch a minute of 10s samples."""
    tallies = {}
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        for k, v in payload.items():
            try:
                tallies[str(k)] = int(v)
            except (ValueError, TypeError):
                pass
    else:
        # ?data=Mac:Chrome:40,Mac:Kiro:20
        raw = request.args.get("data", "")
        for part in raw.split(","):
            if not part:
                continue
            name, _, secs = part.rpartition(":")
            try:
                tallies[name] = int(secs)
            except ValueError:
                pass
    if not tallies:
        return "No data", 400
    data = load_appuse()
    today = date.today().isoformat()
    day = data.setdefault("totals", {}).setdefault(today, {})
    focus_secs = 0
    distraction_secs = 0
    for name, secs in tallies.items():
        secs = max(1, min(secs, 3600))
        day[name] = day.get(name, 0) + secs
        kind = classify_app(name)
        if kind == "focus":
            focus_secs += secs
        elif kind == "distraction":
            distraction_secs += secs
    # Track the current + longest continuous focus streak today. A batch that
    # had focus time and no distraction extends the streak; distraction breaks it.
    streaks = data.setdefault("focus_streak", {}).setdefault(
        today, {"current": 0, "longest": 0})
    if distraction_secs > 0:
        streaks["current"] = 0
    elif focus_secs > 0:
        streaks["current"] += focus_secs
        streaks["longest"] = max(streaks["longest"], streaks["current"])
    save_appuse(data)  # single write for the whole batch
    # Stamp this minute with the dominant Mac app in the batch (most active
    # seconds), for the Mac clock/ribbon view on the usage page.
    mac_tallies = {n: s for n, s in tallies.items() if classify_app(n) == "focus"}
    if mac_tallies:
        top_mac = max(mac_tallies, key=mac_tallies.get)
        mark_usage_minute(top_mac, path=MAC_USAGE_MIN_FILE)
    return "ok"


def load_moods():
    if os.path.exists(MOOD_FILE):
        try:
            with open(MOOD_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def moods_today():
    today = date.today().isoformat()
    return [m for m in load_moods()
            if str(m.get("timestamp", "")).startswith(today)]


@app.route("/mood", methods=["GET", "POST"])
def log_mood():
    """Log a mood 1-5. GET /mood?value=4 (so a bookmark/Shortcut works) or POST."""
    if request.method == "POST":
        value = (request.get_json(silent=True) or {}).get("value")
    else:
        value = request.args.get("value")
    try:
        value = int(value)
    except (ValueError, TypeError):
        return jsonify({"error": "value 1-5 required"}), 400
    if not 1 <= value <= 5:
        return jsonify({"error": "value must be 1-5"}), 400
    moods = load_moods()
    moods.append({"timestamp": datetime.now().isoformat(), "value": value})
    with open(MOOD_FILE, "w") as f:
        json.dump(moods, f)
    return jsonify({"status": "ok", "value": value})


def load_water():
    """Return {'YYYY-MM-DD': bottles} dict (bottles may be fractional)."""
    if os.path.exists(WATER_FILE):
        try:
            with open(WATER_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_water(data):
    with open(WATER_FILE, "w") as f:
        json.dump(data, f)


def water_today():
    """Return {'bottles', 'goal', 'percent'} for today (bottles may be .5)."""
    data = load_water()
    bottles = float(data.get(date.today().isoformat(), 0) or 0)
    pct = min(100, int(round((bottles / WATER_GOAL) * 100))) if WATER_GOAL else 0
    return {"bottles": round(bottles, 1), "goal": WATER_GOAL, "percent": pct}


@app.route("/water", methods=["GET", "POST"])
def log_water():
    """Adjust today's water in 1 L bottles. /water?delta=0.5 adds half a bottle,
    delta=-0.5 undoes. GET so a bookmark/Shortcut works; POST also accepted."""
    if request.method == "POST":
        delta = (request.get_json(silent=True) or {}).get("delta", 0.5)
    else:
        delta = request.args.get("delta", 0.5)
    try:
        delta = float(delta)
    except (ValueError, TypeError):
        delta = 0.5
    data = load_water()
    today = date.today().isoformat()
    current = float(data.get(today, 0) or 0)
    data[today] = round(max(0, current + delta), 1)
    save_water(data)
    return jsonify(water_today())


def load_metric():
    if os.path.exists(METRIC_FILE):
        try:
            with open(METRIC_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def metric_recent(days=30):
    """Return the most recent numeric entries (last `days`), one per day
    (latest wins), oldest-first, plus label/unit and latest value."""
    entries = load_metric()
    by_day = {}
    for e in entries:
        d = str(e.get("date") or str(e.get("timestamp", ""))[:10])
        if d:
            by_day[d] = e.get("value")
    ordered = sorted(by_day.items())[-days:]
    points = [{"date": d, "value": v} for d, v in ordered]
    latest = points[-1]["value"] if points else None
    prev = points[-2]["value"] if len(points) >= 2 else None
    change = (round(latest - prev, 2) if (latest is not None and prev is not None)
              else None)
    return {"points": points, "latest": latest, "change": change,
            "label": METRIC_LABEL, "unit": METRIC_UNIT}


@app.route("/metric", methods=["GET", "POST"])
def log_metric():
    """Log today's numeric metric (e.g. weight). /metric?value=182.4"""
    if request.method == "POST":
        value = (request.get_json(silent=True) or {}).get("value")
    else:
        value = request.args.get("value")
    try:
        value = round(float(value), 2)
    except (ValueError, TypeError):
        return jsonify({"error": "numeric value required"}), 400
    entries = load_metric()
    today = date.today().isoformat()
    # replace today's entry if it exists, else append
    entries = [e for e in entries
               if str(e.get("date") or str(e.get("timestamp", ""))[:10]) != today]
    entries.append({"date": today, "value": value,
                    "timestamp": datetime.now().isoformat()})
    if len(entries) > 1000:
        entries = entries[-1000:]
    with open(METRIC_FILE, "w") as f:
        json.dump(entries, f)
    return jsonify(metric_recent())


def load_journal():
    """Return {'YYYY-MM-DD': 'text'} of one-line-a-day entries."""
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def journal_recent(days=7):
    """Return {'today': text, 'recent': [{date, text}], } newest-first recent."""
    data = load_journal()
    today = date.today().isoformat()
    ordered = sorted(data.items(), reverse=True)[:days]
    return {"today": data.get(today, ""),
            "recent": [{"date": d, "text": tx} for d, tx in ordered]}


@app.route("/journal", methods=["GET", "POST"])
def journal():
    """GET returns today's line + recent entries. POST/GET with ?text= saves
    (replaces) today's line."""
    if request.method == "POST":
        text = (request.get_json(silent=True) or {}).get("text")
    else:
        text = request.args.get("text")
    if text is not None:
        text = str(text).strip()[:280]
        data = load_journal()
        today = date.today().isoformat()
        if text:
            data[today] = text
        else:
            data.pop(today, None)  # empty text clears today's entry
        with open(JOURNAL_FILE, "w") as f:
            json.dump(data, f)
    return jsonify(journal_recent())


def load_sleep():
    if os.path.exists(SLEEP_FILE):
        try:
            with open(SLEEP_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def record_sleep_event(kind):
    """kind is 'sleep' or 'wake'. Append a timestamped event."""
    events = load_sleep()
    events.append({"timestamp": datetime.now().isoformat(), "kind": kind})
    # keep it bounded (last ~90 days of events is plenty)
    if len(events) > 2000:
        events = events[-2000:]
    with open(SLEEP_FILE, "w") as f:
        json.dump(events, f)


def _minutes_since_midnight(dt):
    return dt.hour * 60 + dt.minute


def _circular_mean_minutes(minute_values):
    """Average clock times treating them as points on a 24h circle so that
    bedtimes like 23:50 and 00:10 average to midnight, not noon."""
    if not minute_values:
        return None
    xs = ys = 0.0
    for m in minute_values:
        ang = (m / 1440.0) * 2 * math.pi
        xs += math.cos(ang)
        ys += math.sin(ang)
    ang = math.atan2(ys / len(minute_values), xs / len(minute_values))
    if ang < 0:
        ang += 2 * math.pi
    return int(round((ang / (2 * math.pi)) * 1440)) % 1440


def _circular_std_minutes(minute_values):
    """Spread of clock times in minutes (circular stddev). Lower = more
    regular. Returns None if fewer than 2 points."""
    if len(minute_values) < 2:
        return None
    xs = ys = 0.0
    for m in minute_values:
        ang = (m / 1440.0) * 2 * math.pi
        xs += math.cos(ang)
        ys += math.sin(ang)
    r = math.sqrt(xs * xs + ys * ys) / len(minute_values)
    r = min(max(r, 1e-9), 1.0)
    std_rad = math.sqrt(-2 * math.log(r))
    return int(round((std_rad / (2 * math.pi)) * 1440))


def _fmt_clock(minutes):
    if minutes is None:
        return "--"
    h, m = divmod(minutes % 1440, 60)
    return f"{h:02d}:{m:02d}"


def sleep_summary_week():
    """Pair each 'sleep' with the next 'wake' into nights, over the last 7
    completed nights. Returns per-night rows + regularity stats.

    A night is attributed to the date the person WOKE UP on.
    """
    events = sorted(load_sleep(), key=lambda e: e.get("timestamp", ""))
    nights = []
    pending_sleep = None
    for e in events:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except (ValueError, KeyError, TypeError):
            continue
        if e.get("kind") == "sleep":
            pending_sleep = ts
        elif e.get("kind") == "wake" and pending_sleep is not None:
            dur_min = int((ts - pending_sleep).total_seconds() // 60)
            # ignore nonsense (negative or > 20h) pairings
            if 0 < dur_min <= 20 * 60:
                nights.append({
                    "date": ts.date().isoformat(),
                    "bedtime": pending_sleep.isoformat(),
                    "waketime": ts.isoformat(),
                    "bed_min": _minutes_since_midnight(pending_sleep),
                    "wake_min": _minutes_since_midnight(ts),
                    "duration_min": dur_min,
                })
            pending_sleep = None
    # last 7 nights
    recent = nights[-7:]
    bed_mins = [n["bed_min"] for n in recent]
    wake_mins = [n["wake_min"] for n in recent]
    durs = [n["duration_min"] for n in recent]
    return {
        "nights": recent,
        "avg_bedtime_min": _circular_mean_minutes(bed_mins),
        "avg_waketime_min": _circular_mean_minutes(wake_mins),
        "bedtime_regularity_min": _circular_std_minutes(bed_mins),
        "waketime_regularity_min": _circular_std_minutes(wake_mins),
        "avg_duration_min": int(sum(durs) / len(durs)) if durs else None,
        "last_duration_min": recent[-1]["duration_min"] if recent else None,
        "in_bed": bool(pending_sleep),
    }


def focus_distraction_today():
    """Return {'focus_sec', 'distraction_sec', 'longest_focus_sec'} for today."""
    data = load_appuse()
    today = date.today().isoformat()
    totals = data.get("totals", {}).get(today, {})
    focus = sum(s for a, s in totals.items() if classify_app(a) == "focus")
    distraction = sum(s for a, s in totals.items()
                      if classify_app(a) == "distraction")
    longest = data.get("focus_streak", {}).get(today, {}).get("longest", 0)
    return {"focus_sec": focus, "distraction_sec": distraction,
            "longest_focus_sec": longest}


def _fmt_dur(secs):
    m = secs // 60
    h, mm = divmod(m, 60)
    return f"{h}h {mm}m" if h else f"{mm}m"


COMPLIANCE_FILE = os.path.join(BASE_DIR, "compliance.json")


def load_compliance_state():
    if os.path.exists(COMPLIANCE_FILE):
        try:
            with open(COMPLIANCE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"streak": 0, "last_eval_day": None}


def save_compliance_state(s):
    with open(COMPLIANCE_FILE, "w") as f:
        json.dump(s, f)


def compliance_streak():
    return load_compliance_state().get("streak", 0)


def compliance_today():
    """How well you've kept up with check-ins today, within waking hours.
    expected = number of check-in intervals elapsed so far in the compliance
    window; done = check-ins logged today; missed = expected - done."""
    now = datetime.now()
    today = now.date().isoformat()
    interval_min = max(1, INTERVAL // 60)

    # Minutes of the compliance window that have elapsed so far today.
    start_min = COMPLIANCE_START_HOUR * 60
    end_min = COMPLIANCE_END_HOUR * 60
    now_min = now.hour * 60 + now.minute
    elapsed_min = max(0, min(now_min, end_min) - start_min)
    expected = elapsed_min // interval_min

    done = len([e for e in load_log()
                if str(e.get("timestamp", "")).startswith(today)])
    missed = max(0, expected - done)
    pct = int(round((done / expected) * 100)) if expected > 0 else 100
    pct = min(pct, 100)
    behind = expected > 0 and (done / expected) < COMPLIANCE_BEHIND_BELOW
    before_window = now_min < start_min
    return {"expected": expected, "done": done, "missed": missed,
            "percent": pct, "behind": behind and not before_window,
            "active": not before_window and now_min < end_min,
            "streak": compliance_streak()}


def build_daily_summary():
    """Compose the end-of-day summary text from all tracked data."""
    today = date.today().isoformat()
    parts = []

    # App time (top few by time)
    apps = app_usage_today()
    if apps:
        top = [f"{a['app']} {_fmt_dur(a['seconds'])}"
               + ("!" if a.get("over_limit") else "")
               for a in apps[:3] if a["seconds"] > 0]
        if top:
            parts.append("Apps: " + ", ".join(top))

    # Focus vs distraction
    fd = focus_distraction_today()
    if fd["focus_sec"] or fd["distraction_sec"]:
        parts.append(
            f"Focus {_fmt_dur(fd['focus_sec'])} / "
            f"Distraction {_fmt_dur(fd['distraction_sec'])}")
        if fd["longest_focus_sec"]:
            parts.append(f"Longest focus streak {_fmt_dur(fd['longest_focus_sec'])}")

    # Habits done
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        habits = conn.execute("SELECT id, name FROM habits").fetchall()
        done_ids = {r["habit_id"] for r in conn.execute(
            "SELECT habit_id FROM habit_log WHERE day = ?", (today,)).fetchall()}
        conn.close()
        if habits:
            done = sum(1 for h in habits if h["id"] in done_ids)
            parts.append(f"Habits {done}/{len(habits)}")
    except sqlite3.Error:
        pass

    # Check-ins
    todays = [e for e in load_log() if str(e.get("timestamp", "")).startswith(today)]
    if todays:
        parts.append(f"{len(todays)} check-ins")

    # Average mood
    mt = moods_today()
    if mt:
        avg = sum(m["value"] for m in mt) / len(mt)
        parts.append(f"Mood {avg:.1f}/5")

    return "  |  ".join(parts) if parts else "No activity tracked today."


def daily_summary_push_thread():
    """Send the end-of-day summary once per day at DAILY_SUMMARY_HOUR."""
    if DAILY_SUMMARY_HOUR < 0:
        return
    last_sent = None
    while True:
        now = datetime.now()
        today = now.date().isoformat()
        if now.hour == DAILY_SUMMARY_HOUR and last_sent != today:
            last_sent = today
            send_pushover(build_daily_summary(), title="Daily summary",
                          url=_page_url("/usage"), url_title="See screen usage")
        time.sleep(60)


def build_weekly_review():
    """Compose a 7-day recap: focus/distraction, sleep, habits, check-ins,
    mood, water goal hit-rate, and weight change."""
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    day_set = set(days)
    lines = ["Weekly review"]

    # Focus vs distraction over the week.
    au = load_appuse()
    totals_by_day = au.get("totals", {})
    focus_sec = distraction_sec = 0
    for d in days:
        for a, s in totals_by_day.get(d, {}).items():
            kind = classify_app(a)
            if kind == "focus":
                focus_sec += s
            elif kind == "distraction":
                distraction_sec += s
    if focus_sec or distraction_sec:
        lines.append(f"Focus {_fmt_dur(focus_sec)} / "
                     f"Distraction {_fmt_dur(distraction_sec)}")

    # Sleep: average duration + bedtime regularity from the weekly summary.
    sl = sleep_summary_week()
    if sl.get("nights"):
        avg_dur = sl.get("avg_duration_min")
        if avg_dur:
            h, m = divmod(avg_dur, 60)
            reg = sl.get("bedtime_regularity_min")
            reg_txt = f", bedtime +/-{reg}m" if reg is not None else ""
            lines.append(f"Sleep avg {h}h {m}m/night{reg_txt} "
                         f"({len(sl['nights'])} nights)")

    # Habits: completions vs opportunities across the week.
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        habit_count = conn.execute("SELECT COUNT(*) c FROM habits").fetchone()["c"]
        done = 0
        for r in conn.execute("SELECT day FROM habit_log").fetchall():
            if r["day"] in day_set:
                done += 1
        conn.close()
        opportunities = habit_count * 7
        if opportunities:
            pct = int(round((done / opportunities) * 100))
            lines.append(f"Habits {done}/{opportunities} ({pct}%)")
    except sqlite3.Error:
        pass

    # Check-ins logged this week.
    checkins = sum(1 for e in load_log()
                   if str(e.get("timestamp", ""))[:10] in day_set)
    if checkins:
        lines.append(f"{checkins} check-ins")

    # Average mood across the week.
    week_moods = [m["value"] for m in load_moods()
                  if str(m.get("timestamp", ""))[:10] in day_set]
    if week_moods:
        lines.append(f"Mood avg {sum(week_moods) / len(week_moods):.1f}/5")

    # Water: days the goal was met.
    water = load_water()
    hit = sum(1 for d in days if float(water.get(d, 0) or 0) >= WATER_GOAL)
    logged = sum(1 for d in days if float(water.get(d, 0) or 0) > 0)
    if logged:
        lines.append(f"Water goal hit {hit}/7 days")

    # Weight change over the week (first vs last entry within the window).
    m = metric_recent(days=30)
    wk_points = [p for p in m.get("points", []) if p["date"] in day_set]
    if len(wk_points) >= 2:
        change = round(wk_points[-1]["value"] - wk_points[0]["value"], 2)
        arrow = "+" if change > 0 else ""
        lines.append(f"{m['label']} {arrow}{change}{m['unit']} this week")

    if len(lines) == 1:
        return "Weekly review: not much tracked this week."
    return "\n".join(lines)


def weekly_review_push_thread():
    """Send the weekly review once, on WEEKLY_REVIEW_DOW at WEEKLY_REVIEW_HOUR."""
    if WEEKLY_REVIEW_HOUR < 0:
        return
    last_sent = None
    while True:
        now = datetime.now()
        stamp = now.date().isoformat()
        if (now.weekday() == WEEKLY_REVIEW_DOW
                and now.hour == WEEKLY_REVIEW_HOUR
                and last_sent != stamp):
            last_sent = stamp
            send_pushover(build_weekly_review(), title="Weekly review",
                          url=_page_url("/usage"), url_title="See screen usage")
        time.sleep(60)


# Files worth backing up: all runtime logs/state plus the lists database.
BACKUP_FILES = [
    LOG_FILE, STATE_FILE, APPUSE_FILE, MOOD_FILE, SLEEP_FILE,
    WATER_FILE, METRIC_FILE, COMPLIANCE_FILE, DB_PATH,
]


def run_backup():
    """Copy existing data files into backups/<YYYY-MM-DD_HHMMSS>/ and prune to
    the most recent BACKUP_KEEP snapshots. Returns the snapshot path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, stamp)
    os.makedirs(dest, exist_ok=True)
    copied = 0
    for path in BACKUP_FILES:
        if os.path.exists(path):
            try:
                shutil.copy2(path, os.path.join(dest, os.path.basename(path)))
                copied += 1
            except OSError:
                pass
    # If nothing was copied, don't leave an empty snapshot behind.
    if copied == 0:
        try:
            os.rmdir(dest)
        except OSError:
            pass
        return None
    # Prune old snapshots (keep the newest BACKUP_KEEP).
    try:
        snaps = sorted(d for d in os.listdir(BACKUP_DIR)
                       if os.path.isdir(os.path.join(BACKUP_DIR, d)))
        for old in snaps[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
            shutil.rmtree(os.path.join(BACKUP_DIR, old), ignore_errors=True)
    except OSError:
        pass
    return dest


def backup_thread():
    """Run a backup once a day at BACKUP_HOUR."""
    if BACKUP_HOUR < 0:
        return
    last_done = None
    while True:
        now = datetime.now()
        today = now.date().isoformat()
        if now.hour == BACKUP_HOUR and last_done != today:
            last_done = today
            run_backup()
        time.sleep(60)


def session_credit_thread():
    """Every minute, credit elapsed time to each currently-open app session so
    usage accrues live and doesn't depend on a perfect /appstop. Tracks how much
    each session has been credited (data['credited']) so /appstop only adds the
    remainder. Auto-closes a session once it hits MAX_SESSION_SEC (a forgotten
    /appstop can't run all night)."""
    while True:
        time.sleep(60)
        try:
            data = load_appuse()
            open_sessions = data.get("open", {})
            if not open_sessions:
                continue
            now = time.time()
            today = date.today().isoformat()
            data.setdefault("totals", {}).setdefault(today, {})
            credited = data.setdefault("credited", {})
            changed = False
            for app_name in list(open_sessions):
                start = open_sessions[app_name]
                elapsed = int(now - start)
                if elapsed <= 0:
                    continue
                capped = min(elapsed, MAX_SESSION_SEC)
                already = int(credited.get(app_name, 0))
                delta = capped - already
                if delta > 0:
                    data["totals"][today][app_name] = \
                        data["totals"][today].get(app_name, 0) + delta
                    credited[app_name] = capped
                    check_app_limit(data, app_name, today)
                    changed = True
                    # Stamp the current minute as phone-used for the usage page.
                    if classify_app(app_name) == "distraction":
                        mark_usage_minute(app_name)
                # Auto-close a session that has run past the cap.
                if elapsed >= MAX_SESSION_SEC:
                    open_sessions.pop(app_name, None)
                    credited.pop(app_name, None)
                    changed = True
            if changed:
                save_appuse(data)
        except (OSError, json.JSONDecodeError):
            pass


def _mins_before(target_min, now_min):
    """Minutes from now_min until target_min on a 24h circle (0..1439).
    e.g. now 22:40, target 23:00 -> 20; now 23:10, target 23:00 -> 1430."""
    return (target_min - now_min) % 1440


def winddown_nudge_thread():
    """When it's within WINDDOWN_WINDOW_MIN before your average bedtime and a
    distraction app is currently open, send one 'wind down' nudge per night."""
    if WINDDOWN_WINDOW_MIN <= 0:
        return
    last_nudge_date = None
    while True:
        try:
            sleep = sleep_summary_week()
            avg_bed = sleep.get("avg_bedtime_min")
            # Need enough history for a meaningful average.
            if avg_bed is not None and len(sleep.get("nights", [])) >= 3:
                now = datetime.now()
                now_min = now.hour * 60 + now.minute
                # How long until average bedtime (wrapping midnight).
                until_bed = _mins_before(avg_bed, now_min)
                # Fire in the window just before bedtime (and a little after).
                in_window = (until_bed <= WINDDOWN_WINDOW_MIN
                             or until_bed >= 1440 - 15)
                # "Tonight" key rolls at noon so a post-midnight bedtime still
                # counts as the same night.
                night_key = ((now - timedelta(hours=12)).date().isoformat())
                if in_window and last_nudge_date != night_key:
                    data = load_appuse()
                    open_distraction = [a for a in data.get("open", {})
                                        if classify_app(a) == "distraction"]
                    if open_distraction:
                        app = open_distraction[0]
                        bstr = f"{(avg_bed // 60) % 24:02d}:{avg_bed % 60:02d}"
                        send_pushover(
                            f"It's near your usual bedtime ({bstr}) and you're "
                            f"in {app}. Time to wind down for sleep.",
                            title="Wind down", priority=1,
                            url=_page_url("/usage"), url_title="See screen usage")
                        last_nudge_date = night_key
        except Exception:
            pass
        time.sleep(60)


def minutes_since_last_checkin():
    """Minutes since your most recent check-in today (None if none today)."""
    today = date.today().isoformat()
    todays = [e for e in load_log() if str(e.get("timestamp", "")).startswith(today)]
    if not todays:
        return None
    try:
        last = datetime.fromisoformat(todays[-1]["timestamp"])
        return (datetime.now() - last).total_seconds() / 60
    except (ValueError, KeyError):
        return None


def nag_interval_for(silent_min):
    """How often to nag (minutes), based on how long since the last check-in.
    Aggressive: ramps up fast and gets relentless the longer you're silent."""
    if silent_min is None or silent_min >= 60:
        return 3     # 1h+ silent (or nothing today) -> every 3 min (relentless)
    if silent_min >= 45:
        return 4
    if silent_min >= 30:
        return 5     # half hour -> already nagging hard
    return 10        # behind but recent -> every 10 min


def app_open_nudge_thread():
    """Watch open app sessions. If a tracked app (NUDGE_APPS) stays open longer
    than APP_OPEN_NUDGE_MIN, send a 'close it' nudge, re-nudging every
    APP_OPEN_RENUDGE_MIN while it's still open. Clears when the app closes."""
    if APP_OPEN_NUDGE_MIN <= 0:
        return
    last_nudge = {}  # app -> last nudge epoch (this open session)
    while True:
        data = load_appuse()
        open_sessions = data.get("open", {})
        now = time.time()
        active_apps = set()
        for app_name, start in open_sessions.items():
            if app_name not in NUDGE_APPS:
                continue
            active_apps.add(app_name)
            open_min = (now - start) / 60
            if open_min < APP_OPEN_NUDGE_MIN:
                continue
            due = (app_name not in last_nudge or
                   (now - last_nudge[app_name]) >= APP_OPEN_RENUDGE_MIN * 60)
            if due:
                send_pushover(
                    f"You've been in {app_name} for {int(open_min)} min. "
                    f"Close it and get back to it.",
                    title="Close the app", priority=1,
                    url=_page_url("/usage"), url_title="See screen usage")
                last_nudge[app_name] = now
        # Forget nudge state for apps that are no longer open (session ended).
        for a in list(last_nudge):
            if a not in active_apps:
                del last_nudge[a]
        time.sleep(30)


def compliance_nag_thread():
    """While you're behind on check-ins during waking hours, send nags that get
    MORE frequent and harsher the longer you've gone silent. Also evaluate the
    daily compliance streak at end of day."""
    last_nag = 0
    while True:
        now = datetime.now()
        c = compliance_today()
        silent = minutes_since_last_checkin()

        if c["behind"] and is_awake:
            interval = nag_interval_for(silent)
            if (time.time() - last_nag) >= interval * 60:
                missed = c["missed"]
                sm = int(silent) if silent is not None else None
                # Wording + priority escalate with how long you've been silent.
                # priority 1 = high (louder, bypasses quiet hours).
                prio = 0
                if sm is None:
                    msg = (f"You haven't checked in AT ALL today. "
                           f"{c['done']}/{c['expected']} - LOG SOMETHING NOW.")
                    prio = 1
                elif sm >= 90:
                    msg = (f"{sm} min silent. This is embarrassing - "
                           f"{missed} missed ({c['percent']}%). LOG. RIGHT. NOW.")
                    prio = 1
                elif sm >= 60:
                    msg = (f"Over an hour ({sm} min), no check-in. {missed} missed. "
                           f"Your streak is on the line. Log now.")
                    prio = 1
                elif sm >= 30:
                    msg = (f"{sm} min since last check-in - you're falling behind "
                           f"({c['done']}/{c['expected']}). Log now.")
                else:
                    msg = (f"Behind on check-ins ({c['done']}/{c['expected']}). "
                           f"Last one {sm} min ago. Tap to log.")
                cin_url = (PI_BASE_URL.rstrip("/") + "/") if PI_BASE_URL else None
                send_pushover(msg, title="Check-in compliance", url=cin_url,
                              url_title="Log a check-in", priority=prio)
                last_nag = time.time()

        # End-of-day streak evaluation (once, after the compliance window).
        st = load_compliance_state()
        today = now.date().isoformat()
        if now.hour >= COMPLIANCE_END_HOUR and st.get("last_eval_day") != today:
            met = c["expected"] == 0 or (c["done"] / c["expected"]) >= COMPLIANCE_BEHIND_BELOW
            st["streak"] = st.get("streak", 0) + 1 if met else 0
            st["last_eval_day"] = today
            save_compliance_state(st)
            if met:
                send_pushover(f"Check-in goal met! Compliance streak: {st['streak']} days.",
                              title="Compliance")
            else:
                send_pushover(f"Check-in goal missed ({c['percent']}%). Streak reset to 0.",
                              title="Compliance")
        time.sleep(60)


def app_usage_today():
    """Return today's per-app usage as [{'app', 'seconds', 'opens'}], desc by time."""
    data = load_appuse()
    today = date.today().isoformat()
    totals = data.get("totals", {}).get(today, {})
    opens = data.get("opens", {}).get(today, {})
    # Include apps that have either time or opens recorded today.
    names = set(totals) | set(opens)
    limit_sec = APP_TIME_LIMIT_MIN * 60
    return sorted(
        ({"app": a, "seconds": totals.get(a, 0), "opens": opens.get(a, 0),
          "over_limit": bool(limit_sec and totals.get(a, 0) >= limit_sec)}
         for a in names),
        key=lambda r: -r["seconds"],
    )


def app_usage_week():
    """Return the last 7 days of app usage:
      {"days": ["Mon", ...],                 # oldest -> today, short labels
       "apps": [{"app", "seconds", "opens",  # 7-day totals per app
                 "daily": [sec_day0, ...]}]}  # per-day seconds for a mini chart
    """
    data = load_appuse()
    totals = data.get("totals", {})
    opens = data.get("opens", {})
    days = [(date.today() - timedelta(days=i)) for i in range(6, -1, -1)]
    day_keys = [d.isoformat() for d in days]
    day_labels = [d.strftime("%a") for d in days]

    apps = set()
    for dk in day_keys:
        apps |= set(totals.get(dk, {}))
        apps |= set(opens.get(dk, {}))

    result = []
    for a in apps:
        daily = [totals.get(dk, {}).get(a, 0) for dk in day_keys]
        total_sec = sum(daily)
        total_opens = sum(opens.get(dk, {}).get(a, 0) for dk in day_keys)
        result.append({"app": a, "seconds": total_sec, "opens": total_opens,
                       "daily": daily})
    result.sort(key=lambda r: -r["seconds"])
    return {"days": day_labels, "apps": result}


@app.route("/categories")
def categories():
    return jsonify({"categories": CATEGORIES})


def _today_entries():
    today = date.today().isoformat()
    return [e for e in load_log()
            if str(e.get("timestamp", "")).startswith(today)]


def daily_summary(entries):
    """Estimate time per category today. Each check-in represents roughly one
    INTERVAL of activity, so time = count * interval_minutes."""
    per_min = INTERVAL // 60
    counts = {}
    for e in entries:
        cat = e.get("category") or "Other"
        counts[cat] = counts.get(cat, 0) + 1
    # Sorted by most first. count_only categories show a count, not est. time.
    summary = [
        {"category": c, "count": n, "minutes": n * per_min,
         "count_only": c in COUNT_ONLY_CATEGORIES}
        for c, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return summary


@app.route("/status")
def status():
    remaining = max(0, next_checkin_time - time.time())
    todays = _today_entries()
    return jsonify({
        "notification_pending": notification_pending.is_set(),
        "next_checkin_in": int(remaining),
        "is_awake": is_awake,
        "recent": todays[-5:],
        "categories": CATEGORIES,
        "summary": daily_summary(todays),
        "total_checkins": len(todays),
        "app_usage": app_usage_today(),
        "app_week": app_usage_week(),
        "app_limit_min": APP_TIME_LIMIT_MIN,
        "focus": focus_distraction_today(),
        "moods": moods_today(),
        "compliance": compliance_today(),
        "sleep": sleep_summary_week(),
        "water": water_today(),
        "metric": metric_recent(),
        "journal": journal_recent(),
    })


@app.route("/wake", methods=["POST"])
def wake():
    global is_awake, next_checkin_time
    is_awake = True
    next_checkin_time = time.time() + INTERVAL
    notification_pending.clear()
    persist_runtime_state()
    record_sleep_event("wake")
    log_entry("Woke up")
    return jsonify({"status": "awake"})


@app.route("/sleep", methods=["POST"])
def sleep():
    global is_awake
    is_awake = False
    notification_pending.clear()
    persist_runtime_state()
    record_sleep_event("sleep")
    log_entry("Went to sleep")
    return jsonify({"status": "sleeping"})


@app.route("/trigger", methods=["POST"])
def trigger():
    global next_checkin_time
    notification_pending.set()
    next_checkin_time = time.time() + INTERVAL
    persist_runtime_state()
    cin_url = (PI_BASE_URL.rstrip("/") + "/") if PI_BASE_URL else None
    send_pushover("Test check-in! What have you been up to?",
                  url=cin_url, url_title="Log a check-in")
    return jsonify({"status": "triggered"})


@app.route("/habit-reminder-test", methods=["GET", "POST"])
def habit_reminder_test():
    """Fire the evening habit reminder right now, for testing."""
    pending = unchecked_habits_today()
    if pending:
        names = ", ".join(pending)
        send_pushover(f"{len(pending)} habit(s) left today: {names}",
                      title="Habit reminder")
        return jsonify({"sent": True, "pending": pending})
    return jsonify({"sent": False, "pending": [],
                    "message": "All habits done today - nothing to send."})


@app.route("/daily-summary-test", methods=["GET", "POST"])
def daily_summary_test():
    """Send the end-of-day summary right now, for testing."""
    text = build_daily_summary()
    send_pushover(text, title="Daily summary",
                  url=_page_url("/usage"), url_title="See screen usage")
    return jsonify({"sent": True, "summary": text})


@app.route("/weekly-review-test", methods=["GET", "POST"])
def weekly_review_test():
    """Send the weekly review right now, for testing."""
    text = build_weekly_review()
    send_pushover(text, title="Weekly review",
                  url=_page_url("/usage"), url_title="See screen usage")
    return jsonify({"sent": True, "review": text})


@app.route("/backup-now", methods=["GET", "POST"])
def backup_now():
    """Run a backup immediately, for testing/manual snapshots."""
    dest = run_backup()
    return jsonify({"ok": bool(dest), "snapshot": os.path.basename(dest) if dest else None})


# ---------- main ----------

if __name__ == "__main__":
    persist_runtime_state()  # so the display shows a countdown right away
    Thread(target=timer_thread, daemon=True).start()
    Thread(target=midnight_email_thread, daemon=True).start()
    Thread(target=habit_reminder_thread, daemon=True).start()
    Thread(target=daily_summary_push_thread, daemon=True).start()
    Thread(target=weekly_review_push_thread, daemon=True).start()
    Thread(target=backup_thread, daemon=True).start()
    Thread(target=session_credit_thread, daemon=True).start()
    Thread(target=winddown_nudge_thread, daemon=True).start()
    Thread(target=compliance_nag_thread, daemon=True).start()
    Thread(target=app_open_nudge_thread, daemon=True).start()
    print("Time Tracker running on http://0.0.0.0:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
