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
import os
import smtplib
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
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
DB_PATH = os.path.join(BASE_DIR, "lists.db")  # habits live in the list app's DB

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


def classify_app(name):
    """Return 'focus', 'distraction', or 'neutral' for an app/category name."""
    if name in DISTRACTION_APPS:
        return "distraction"
    if name in FOCUS_APPS:
        return "focus"
    if name.startswith("Mac:"):
        return "focus"
    return "neutral"
# The Pi's reachable tracker URL, used so the Pushover reminder can deep-link
# to the check-in page. Defaults to the Pi 5's Tailscale address; override in
# tracker_config.py with PI_BASE_URL if it changes.
PI_BASE_URL = getattr(cfg, "PI_BASE_URL", "http://100.102.96.42:5050")

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
MAX_SESSION_SEC = 4 * 60 * 60  # ignore absurd sessions (e.g. phone slept 8h)


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


# Ignore a repeat /appstart for the same app within this window (guards against
# iOS "Is Opened" automations firing the request twice). Kept short so genuine
# quick re-opens are still counted -- only true instant double-fires are dropped.
APPSTART_DEBOUNCE_SEC = 3


@app.route("/appstart")
def app_start():
    app_name = (request.args.get("app") or "").strip()
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
        # Otherwise a previous session was left open (missed /appstop). Bank
        # its time now instead of losing it, then start the new session.
        if 0 < gap <= MAX_SESSION_SEC:
            data.setdefault("totals", {}).setdefault(today, {})
            data["totals"][today][app_name] = \
                data["totals"][today].get(app_name, 0) + int(gap)

    data["open"][app_name] = now
    data.setdefault("opens", {}).setdefault(today, {})
    data["opens"][today][app_name] = data["opens"][today].get(app_name, 0) + 1
    save_appuse(data)
    return _tiny_page(f"Started: {app_name}")


@app.route("/appstop")
def app_stop():
    app_name = (request.args.get("app") or "").strip()
    if not app_name:
        return "Missing ?app=", 400
    data = load_appuse()
    start = data["open"].pop(app_name, None)
    if start is None:
        save_appuse(data)
        return _tiny_page(f"{app_name}: no open session")
    elapsed = int(time.time() - start)
    crossed_limit = False
    total_today = 0
    if 0 < elapsed <= MAX_SESSION_SEC:
        today = date.today().isoformat()
        data.setdefault("totals", {}).setdefault(today, {})
        data["totals"][today][app_name] = \
            data["totals"][today].get(app_name, 0) + elapsed
        total_today = data["totals"][today][app_name]
        # Fire a limit alert once per app per day when crossing the threshold.
        if APP_TIME_LIMIT_MIN > 0 and total_today >= APP_TIME_LIMIT_MIN * 60:
            alerted = data.setdefault("alerted", {}).setdefault(today, [])
            if app_name not in alerted:
                alerted.append(app_name)
                mins = total_today // 60
                send_pushover(
                    f"You've used {app_name} for {mins} min today "
                    f"(limit {APP_TIME_LIMIT_MIN} min).",
                    title="Screen-time limit reached")
                crossed_limit = True
    save_appuse(data)
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
            send_pushover(build_daily_summary(), title="Daily summary")
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
                    title="Close the app", priority=1)
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
    })


@app.route("/wake", methods=["POST"])
def wake():
    global is_awake, next_checkin_time
    is_awake = True
    next_checkin_time = time.time() + INTERVAL
    notification_pending.clear()
    persist_runtime_state()
    log_entry("Woke up")
    return jsonify({"status": "awake"})


@app.route("/sleep", methods=["POST"])
def sleep():
    global is_awake
    is_awake = False
    notification_pending.clear()
    persist_runtime_state()
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
    send_pushover(text, title="Daily summary")
    return jsonify({"sent": True, "summary": text})


# ---------- main ----------

if __name__ == "__main__":
    persist_runtime_state()  # so the display shows a countdown right away
    Thread(target=timer_thread, daemon=True).start()
    Thread(target=midnight_email_thread, daemon=True).start()
    Thread(target=habit_reminder_thread, daemon=True).start()
    Thread(target=daily_summary_push_thread, daemon=True).start()
    Thread(target=compliance_nag_thread, daemon=True).start()
    Thread(target=app_open_nudge_thread, daemon=True).start()
    print("Time Tracker running on http://0.0.0.0:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
