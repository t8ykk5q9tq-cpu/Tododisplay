import os
import json
import sqlite3
import time
import urllib.request
from datetime import date, datetime, timedelta
from threading import Lock, Thread
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lists.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TRACKER_LOG = os.path.join(BASE_DIR, "tracker_log.json")
TRACKER_STATE = os.path.join(BASE_DIR, "tracker_state.json")
APPUSE_FILE = os.path.join(BASE_DIR, "app_usage.json")

# Live-reload: enabled when LIVE_RELOAD=1 in the environment.
LIVE_RELOAD = os.environ.get("LIVE_RELOAD") == "1"


def static_version():
    """Return a fingerprint of the frontend files based on their newest mtime.
    The browser polls this; when it changes, the page reloads itself."""
    latest = 0.0
    for root, _dirs, files in os.walk(STATIC_DIR):
        for name in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, name))
                if mtime > latest:
                    latest = mtime
            except OSError:
                pass
    return str(latest)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


DEFAULT_HABITS = ["Gym", "Water", "Work", "Breakfast", "Lunch", "Dinner"]
# The original placeholder set, used to detect a fresh install that still has
# the old untouched defaults so we can migrate it to the new list.
OLD_DEFAULT_HABITS = ["Gym", "Water", "Read", "Meds"]


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_type TEXT NOT NULL CHECK(list_type IN ('todo', 'shopping')),
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Habits: each has a name and the last date it was marked done (YYYY-MM-DD).
    # A habit shows "done" only if last_done == today, so it auto-resets daily.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            last_done TEXT,
            position INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # History: one row per (habit, day-completed). Powers the GitHub-style
    # grid and streak calculations.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS habit_log (
            habit_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY (habit_id, day)
        )
        """
    )
    # Simple key/value settings (used for the daily "focus for today" banner).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            day TEXT
        )
        """
    )
    # Seed default habits if the table is empty. Also migrate an existing
    # install that still has ONLY the old untouched placeholder set, so the
    # new default list takes effect without wiping any habits you've added.
    existing = [r["name"] for r in
                conn.execute("SELECT name FROM habits ORDER BY position, id").fetchall()]
    if not existing or existing == OLD_DEFAULT_HABITS:
        conn.execute("DELETE FROM habits")
        for i, name in enumerate(DEFAULT_HABITS):
            conn.execute(
                "INSERT INTO habits (name, position) VALUES (?, ?)", (name, i)
            )
    # One-time reorder: put Work top-right and Breakfast bottom-left.
    # Only applies if the habit set matches, so it won't disturb custom setups.
    desired_order = ["Gym", "Water", "Work", "Breakfast", "Lunch", "Dinner"]
    current = [r["name"] for r in
               conn.execute("SELECT name FROM habits ORDER BY position, id").fetchall()]
    if sorted(current) == sorted(desired_order) and current != desired_order:
        for i, name in enumerate(desired_order):
            conn.execute("UPDATE habits SET position = ? WHERE name = ?", (i, name))
    conn.commit()
    conn.close()


init_db()


# --- API Routes ---


@app.route("/api/items/<list_type>", methods=["GET"])
def get_items(list_type):
    if list_type not in ("todo", "shopping"):
        return jsonify({"error": "Invalid list type"}), 400
    conn = get_db()
    rows = conn.execute(
        "SELECT id, text, done FROM items WHERE list_type = ? ORDER BY done ASC, created_at DESC",
        (list_type,),
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/items/<list_type>", methods=["POST"])
def add_item(list_type):
    if list_type not in ("todo", "shopping"):
        return jsonify({"error": "Invalid list type"}), 400
    data = request.get_json()
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Text is required"}), 400
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO items (list_type, text) VALUES (?, ?)", (list_type, text)
    )
    conn.commit()
    item_id = cursor.lastrowid
    conn.close()
    return jsonify({"id": item_id, "text": text, "done": 0}), 201


@app.route("/api/items/<int:item_id>/toggle", methods=["PATCH"])
def toggle_item(item_id):
    conn = get_db()
    conn.execute("UPDATE items SET done = 1 - done WHERE id = ?", (item_id,))
    conn.commit()
    row = conn.execute("SELECT id, text, done FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "Item not found"}), 404
    return jsonify(dict(row))


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/items/<list_type>/clear-done", methods=["DELETE"])
def clear_done(list_type):
    if list_type not in ("todo", "shopping"):
        return jsonify({"error": "Invalid list type"}), 400
    conn = get_db()
    conn.execute("DELETE FROM items WHERE list_type = ? AND done = 1", (list_type,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --- Habits ---

HISTORY_DAYS = 14  # how many days the grid shows


def _completed_days(conn, habit_id):
    """Set of ISO date strings this habit was completed."""
    rows = conn.execute(
        "SELECT day FROM habit_log WHERE habit_id = ?", (habit_id,)
    ).fetchall()
    return {r["day"] for r in rows}


def _streak(days_set):
    """Current consecutive-day streak ending today (or yesterday if not yet
    done today). Counts backward from today while each day is present."""
    if not days_set:
        return 0
    today = date.today()
    # Allow the streak to be "alive" if today isn't done yet but yesterday was.
    start = today if today.isoformat() in days_set else today - timedelta(days=1)
    streak = 0
    d = start
    while d.isoformat() in days_set:
        streak += 1
        d -= timedelta(days=1)
    return streak


def _history(days_set, n=HISTORY_DAYS):
    """List of {day, done} for the last n days, oldest first (for the grid)."""
    today = date.today()
    out = []
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        out.append({"day": d, "done": d in days_set})
    return out


@app.route("/api/habits", methods=["GET"])
def get_habits():
    today = date.today().isoformat()
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name FROM habits ORDER BY position, id"
    ).fetchall()
    result = []
    for r in rows:
        days = _completed_days(conn, r["id"])
        result.append({
            "id": r["id"],
            "name": r["name"],
            "done": today in days,
            "streak": _streak(days),
            "history": _history(days),
        })
    conn.close()
    return jsonify(result)


@app.route("/api/habits/<int:habit_id>/toggle", methods=["PATCH"])
def toggle_habit(habit_id):
    today = date.today().isoformat()
    conn = get_db()
    row = conn.execute("SELECT id FROM habits WHERE id = ?", (habit_id,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"error": "Habit not found"}), 404
    done_today = conn.execute(
        "SELECT 1 FROM habit_log WHERE habit_id = ? AND day = ?", (habit_id, today)
    ).fetchone() is not None
    if done_today:
        conn.execute("DELETE FROM habit_log WHERE habit_id = ? AND day = ?",
                     (habit_id, today))
        conn.execute("UPDATE habits SET last_done = NULL WHERE id = ?", (habit_id,))
    else:
        conn.execute("INSERT OR IGNORE INTO habit_log (habit_id, day) VALUES (?, ?)",
                     (habit_id, today))
        conn.execute("UPDATE habits SET last_done = ? WHERE id = ?", (today, habit_id))
    days = _completed_days(conn, habit_id)
    conn.commit()
    conn.close()
    return jsonify({"id": habit_id, "done": not done_today, "streak": _streak(days)})


@app.route("/api/habits", methods=["POST"])
def add_habit():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    conn = get_db()
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM habits").fetchone()[0]
    cursor = conn.execute(
        "INSERT INTO habits (name, position) VALUES (?, ?)", (name, pos)
    )
    conn.commit()
    hid = cursor.lastrowid
    conn.close()
    return jsonify({"id": hid, "name": name, "done": False}), 201


@app.route("/api/habits/<int:habit_id>", methods=["DELETE"])
def delete_habit(habit_id):
    conn = get_db()
    conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
    conn.execute("DELETE FROM habit_log WHERE habit_id = ?", (habit_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --- Focus for today ---


@app.route("/api/focus", methods=["GET"])
def get_focus():
    """Return today's focus, or empty if none set today (resets at midnight)."""
    today = date.today().isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT value, day FROM settings WHERE key = 'focus'"
    ).fetchone()
    conn.close()
    if row and row["day"] == today:
        return jsonify({"focus": row["value"]})
    return jsonify({"focus": ""})


@app.route("/api/focus", methods=["POST"])
def set_focus():
    today = date.today().isoformat()
    text = (request.get_json() or {}).get("focus", "").strip()
    conn = get_db()
    conn.execute(
        "INSERT INTO settings (key, value, day) VALUES ('focus', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, day = excluded.day",
        (text, today),
    )
    conn.commit()
    conn.close()
    return jsonify({"focus": text})


# --- Live Reload ---


@app.route("/api/version")
def version():
    """Frontend polls this to detect when static files change."""
    return jsonify({"version": static_version(), "live_reload": LIVE_RELOAD})


# --- Board view (landscape web version of the physical display) ---

WEATHER_LAT = os.environ.get("WEATHER_LAT", "44.9778")
WEATHER_LON = os.environ.get("WEATHER_LON", "-93.2650")
WEATHER_UNITS = os.environ.get("WEATHER_UNITS", "fahrenheit")
COUNT_ONLY = {"TikTok", "YouTube"}
APP_LIMIT_SEC = int(os.environ.get("APP_TIME_LIMIT_MIN", "60")) * 60
MOOD_FILE = os.path.join(BASE_DIR, "mood_log.json")
SLEEP_FILE = os.path.join(BASE_DIR, "sleep_log.json")
WATER_FILE = os.path.join(BASE_DIR, "water_log.json")
METRIC_FILE = os.path.join(BASE_DIR, "metric_log.json")
JOURNAL_FILE = os.path.join(BASE_DIR, "journal_log.json")
WATER_GOAL = int(os.environ.get("WATER_GOAL", "3"))
METRIC_LABEL = os.environ.get("METRIC_LABEL", "Weight")
METRIC_UNIT = os.environ.get("METRIC_UNIT", "lb")
COMPLIANCE_FILE = os.path.join(BASE_DIR, "compliance.json")
COMPLIANCE_START_HOUR = int(os.environ.get("COMPLIANCE_START_HOUR", "8"))
COMPLIANCE_END_HOUR = int(os.environ.get("COMPLIANCE_END_HOUR", "22"))
TRACKER_INTERVAL_MIN = int(os.environ.get("CHECKIN_INTERVAL_MIN", "30"))
DISTRACTION_APPS = {"TikTok", "YouTube"}
_wx = {"data": None}
_wx_lock = Lock()


def _classify_app(name):
    """'focus' for Mac apps, 'distraction' for TikTok/YouTube, else 'neutral'."""
    if name in DISTRACTION_APPS:
        return "distraction"
    if name.startswith("Mac:"):
        return "focus"
    return "neutral"


def _board_focus(au, today):
    """Today's focus vs distraction seconds + longest focus streak."""
    totals = au.get("totals", {}).get(today, {})
    focus = sum(s for a, s in totals.items() if _classify_app(a) == "focus")
    distraction = sum(s for a, s in totals.items()
                      if _classify_app(a) == "distraction")
    longest = au.get("focus_streak", {}).get(today, {}).get("longest", 0)
    return {"focus_sec": focus, "distraction_sec": distraction,
            "longest_focus_sec": longest}


def _board_mac_week(au):
    """7-day totals for Mac apps (top 5) with per-day breakdown."""
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    totals_by_day = au.get("totals", {})
    per_app = {}
    for d in days:
        for a, s in totals_by_day.get(d, {}).items():
            if _classify_app(a) == "focus":
                bucket = per_app.setdefault(a, {})
                bucket[d] = bucket.get(d, 0) + s
    rows = []
    for a, byday in per_app.items():
        daily = [byday.get(d, 0) for d in days]
        rows.append({"app": a.replace("Mac:", ""), "seconds": sum(daily),
                     "daily": daily})
    rows.sort(key=lambda r: -r["seconds"])
    # Weekday label per day (Python weekday(): Mon=0..Sun=6 -> our labels index)
    py_to_lbl = {0: "Mo", 1: "Tu", 2: "We", 3: "Th", 4: "Fr", 5: "Sa", 6: "Su"}
    day_labels = [py_to_lbl[datetime.fromisoformat(d).weekday()] for d in days]
    return {"apps": rows[:5], "days": day_labels}


def _board_mood(today):
    all_moods = _read_json(MOOD_FILE, [])
    todays = [m for m in all_moods
              if str(m.get("timestamp", "")).startswith(today)]
    if not todays:
        return None
    avg = sum(m["value"] for m in todays) / len(todays)
    return {"latest": todays[-1]["value"], "avg": round(avg, 1),
            "count": len(todays)}


def _board_compliance(today, done_count):
    now = datetime.now()
    start_min = COMPLIANCE_START_HOUR * 60
    end_min = COMPLIANCE_END_HOUR * 60
    now_min = now.hour * 60 + now.minute
    interval = max(1, TRACKER_INTERVAL_MIN)
    elapsed = max(0, min(now_min, end_min) - start_min)
    expected = elapsed // interval
    pct = min(100, int(round((done_count / expected) * 100))) if expected else 100
    before = now_min < start_min
    behind = (not before) and expected > 0 and (done_count / expected) < 0.7
    streak = _read_json(COMPLIANCE_FILE, {}).get("streak", 0)
    return {"expected": expected, "done": done_count,
            "missed": max(0, expected - done_count), "percent": pct,
            "behind": behind, "streak": streak}


def _board_sleep():
    """Last night's duration + weekly averages/regularity from sleep_log."""
    import math
    events = sorted(_read_json(SLEEP_FILE, []),
                    key=lambda e: e.get("timestamp", ""))
    nights = []
    pending = None
    in_bed = False
    for e in events:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except (ValueError, KeyError, TypeError):
            continue
        if e.get("kind") == "sleep":
            pending = ts
            in_bed = True
        elif e.get("kind") == "wake" and pending is not None:
            dur = int((ts - pending).total_seconds() // 60)
            if 0 < dur <= 20 * 60:
                nights.append({"bed_min": pending.hour * 60 + pending.minute,
                               "wake_min": ts.hour * 60 + ts.minute,
                               "dur": dur})
            pending = None
            in_bed = False
    recent = nights[-7:]
    if not recent:
        return {"in_bed": in_bed} if in_bed else None

    def circ_mean(vals):
        xs = ys = 0.0
        for m in vals:
            ang = (m / 1440.0) * 2 * math.pi
            xs += math.cos(ang)
            ys += math.sin(ang)
        ang = math.atan2(ys, xs)
        if ang < 0:
            ang += 2 * math.pi
        return int(round((ang / (2 * math.pi)) * 1440)) % 1440

    def circ_std(vals):
        if len(vals) < 2:
            return None
        xs = ys = 0.0
        for m in vals:
            ang = (m / 1440.0) * 2 * math.pi
            xs += math.cos(ang)
            ys += math.sin(ang)
        r = min(max(math.sqrt(xs * xs + ys * ys) / len(vals), 1e-9), 1.0)
        return int(round((math.sqrt(-2 * math.log(r)) / (2 * math.pi)) * 1440))

    beds = [n["bed_min"] for n in recent]
    durs = [n["dur"] for n in recent]
    return {
        "in_bed": in_bed,
        "last_dur_min": recent[-1]["dur"],
        "avg_bed_min": circ_mean(beds),
        "avg_dur_min": int(sum(durs) / len(durs)),
        "regularity_min": circ_std(beds),
    }


def _board_water(today):
    data = _read_json(WATER_FILE, {})
    glasses = int(data.get(today, 0))
    pct = min(100, int(round((glasses / WATER_GOAL) * 100))) if WATER_GOAL else 0
    return {"glasses": glasses, "goal": WATER_GOAL, "percent": pct}


def _board_journal():
    data = _read_json(JOURNAL_FILE, {})
    today = date.today().isoformat()
    ordered = sorted(data.items(), reverse=True)[:5]
    return {"today": data.get(today, ""),
            "recent": [{"date": d, "text": tx} for d, tx in ordered]}


def _board_metric():
    entries = _read_json(METRIC_FILE, [])
    by_day = {}
    for e in entries:
        d = str(e.get("date") or str(e.get("timestamp", ""))[:10])
        if d:
            by_day[d] = e.get("value")
    ordered = sorted(by_day.items())[-30:]
    points = [{"date": d, "value": v} for d, v in ordered]
    latest = points[-1]["value"] if points else None
    prev = points[-2]["value"] if len(points) >= 2 else None
    change = (round(latest - prev, 2) if (latest is not None and prev is not None)
              else None)
    return {"points": points, "latest": latest, "change": change,
            "label": METRIC_LABEL, "unit": METRIC_UNIT}

WEATHER_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy rain", 71: "Snow", 73: "Snow",
    75: "Heavy snow", 80: "Showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
    96: "Thunderstorm", 99: "Thunderstorm",
}


def _board_weather_thread():
    unit = "fahrenheit" if WEATHER_UNITS.startswith("f") else "celsius"
    url = ("https://api.open-meteo.com/v1/forecast?"
           f"latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
           "&current=temperature_2m,weather_code"
           "&daily=temperature_2m_max,temperature_2m_min,weather_code"
           f"&temperature_unit={unit}&timezone=auto&forecast_days=1")
    deg = "F" if unit == "fahrenheit" else "C"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.load(r)
            cur, daily = d.get("current", {}), d.get("daily", {})
            txt = (f"{round(cur.get('temperature_2m'))}\u00b0{deg}  "
                   f"{WEATHER_CODES.get(cur.get('weather_code', 0), '')}   "
                   f"H:{round(daily.get('temperature_2m_max',[0])[0])}\u00b0  "
                   f"L:{round(daily.get('temperature_2m_min',[0])[0])}\u00b0")
            with _wx_lock:
                _wx["data"] = txt
            time.sleep(15 * 60)
        except Exception:
            time.sleep(60)


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _board_habits():
    today = date.today().isoformat()
    conn = get_db()
    rows = conn.execute("SELECT id, name FROM habits ORDER BY position, id").fetchall()
    out = []
    for r in rows:
        logs = {x["day"] for x in conn.execute(
            "SELECT day FROM habit_log WHERE habit_id = ?", (r["id"],)).fetchall()}
        out.append({"name": r["name"], "done": today in logs})
    conn.close()
    return out


@app.route("/api/board")
def api_board():
    today = date.today().isoformat()
    # Lists
    conn = get_db()
    todo = [dict(x) for x in conn.execute(
        "SELECT text, done FROM items WHERE list_type='todo' ORDER BY done, created_at DESC")]
    shopping = [dict(x) for x in conn.execute(
        "SELECT text, done FROM items WHERE list_type='shopping' ORDER BY done, created_at DESC")]
    frow = conn.execute("SELECT value, day FROM settings WHERE key='focus'").fetchone()
    conn.close()
    focus = frow["value"] if frow and frow["day"] == today else ""

    # Tracker (today's check-ins + countdown)
    log = _read_json(TRACKER_LOG, [])
    todays = [e for e in log if str(e.get("timestamp", "")).startswith(today)]
    state = _read_json(TRACKER_STATE, {})
    nct = state.get("next_checkin_time")
    next_in = max(0, int(nct - time.time())) if nct else None

    # App usage today (time + opens, over-limit flag)
    au = _read_json(APPUSE_FILE, {})
    totals = au.get("totals", {}).get(today, {})
    opens = au.get("opens", {}).get(today, {})
    apps = sorted(
        ({"app": a, "seconds": totals.get(a, 0), "opens": opens.get(a, 0),
          "over_limit": bool(APP_LIMIT_SEC and totals.get(a, 0) >= APP_LIMIT_SEC)}
         for a in (set(totals) | set(opens))),
        key=lambda r: -r["seconds"])

    with _wx_lock:
        weather = _wx["data"]

    return jsonify({
        "todo": todo, "shopping": shopping, "focus": focus,
        "habits": _board_habits(),
        "checkins": todays[-6:],
        "next_in": next_in, "is_awake": state.get("is_awake", True),
        "apps": apps,
        "weather": weather,
        "focus_stats": _board_focus(au, today),
        "mac_week": _board_mac_week(au),
        "mood": _board_mood(today),
        "compliance": _board_compliance(today, len(todays)),
        "sleep": _board_sleep(),
        "water": _board_water(today),
        "metric": _board_metric(),
        "journal": _board_journal(),
    })


@app.route("/board")
def board():
    return send_from_directory("static", "board.html")


# --- Serve Frontend ---


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    Thread(target=_board_weather_thread, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=LIVE_RELOAD, use_reloader=LIVE_RELOAD)
