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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "tracker_log.json")
STATE_FILE = os.path.join(BASE_DIR, "tracker_state.json")
DB_PATH = os.path.join(BASE_DIR, "lists.db")  # habits live in the list app's DB

INTERVAL = int(getattr(cfg, "CHECKIN_INTERVAL_MIN", 30)) * 60
FOLLOWUP = int(getattr(cfg, "FOLLOWUP_MIN", 5)) * 60
# Hour (0-23) to send the evening reminder about unchecked habits. -1 disables.
HABIT_REMINDER_HOUR = int(getattr(cfg, "HABIT_REMINDER_HOUR", 20))

app = Flask(__name__)


# ---------- helpers ----------

def send_pushover(message, title="Time Tracker"):
    if not cfg.PUSHOVER_USER or not cfg.PUSHOVER_TOKEN:
        return  # notifications disabled
    data = urllib.parse.urlencode({
        "token": cfg.PUSHOVER_TOKEN, "user": cfg.PUSHOVER_USER,
        "title": title, "message": message, "sound": "vibrate",
    }).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://api.pushover.net/1/messages.json", data=data),
            timeout=10,
        )
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


def log_entry(text, extra=None):
    entries = load_log()
    e = {"timestamp": datetime.now().isoformat(), "text": text}
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
        send_pushover("Check-in time! What have you been up to the last "
                      f"{INTERVAL // 60} minutes?")
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
    if not text:
        return jsonify({"error": "text required"}), 400
    log_entry(text)
    notification_pending.clear()
    return jsonify({"status": "ok"})


@app.route("/status")
def status():
    remaining = max(0, next_checkin_time - time.time())
    entries = load_log()
    return jsonify({
        "notification_pending": notification_pending.is_set(),
        "next_checkin_in": int(remaining),
        "is_awake": is_awake,
        "recent": entries[-5:],
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
    send_pushover("Test notification from Time Tracker!")
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


# ---------- main ----------

if __name__ == "__main__":
    persist_runtime_state()  # so the display shows a countdown right away
    Thread(target=timer_thread, daemon=True).start()
    Thread(target=midnight_email_thread, daemon=True).start()
    Thread(target=habit_reminder_thread, daemon=True).start()
    print("Time Tracker running on http://0.0.0.0:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
