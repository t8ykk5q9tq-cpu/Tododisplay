#!/usr/bin/env python3
"""
display.py - Lightweight fullscreen list display for low-RAM Raspberry Pi boards
(e.g. Pi Zero 2 W). Draws the todo and shopping lists directly to the screen with
pygame, reading from the same SQLite database the Flask web app uses.

No browser or desktop environment required. Add/remove items from any device via
the Flask web interface at http://<pi-ip>:5000 -- changes appear here automatically.
"""
import json
import math
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from datetime import datetime

import pygame

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lists.db")
TRACKER_LOG = os.path.join(BASE_DIR, "tracker_log.json")
TRACKER_STATE = os.path.join(BASE_DIR, "tracker_state.json")
APPUSE_FILE = os.path.join(BASE_DIR, "app_usage.json")
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
# Minutes per check-in, used to estimate time-per-category in the summary.
# Should match tracker.py's CHECKIN_INTERVAL_MIN.
TRACKER_INTERVAL_MIN = int(os.environ.get("CHECKIN_INTERVAL_MIN", "30"))
# Categories shown as a count (e.g. "TikTok x9") instead of estimated time.
COUNT_ONLY_CATEGORIES = {"TikTok", "YouTube"}
# Per-app daily time limit (minutes); over-limit apps display in red.
APP_TIME_LIMIT_SEC = int(os.environ.get("APP_TIME_LIMIT_MIN", "60")) * 60
DISTRACTION_APPS = {"TikTok", "YouTube"}


def classify_app(name):
    """'focus' for Mac apps, 'distraction' for TikTok/YouTube, else 'neutral'."""
    if name in DISTRACTION_APPS:
        return "distraction"
    if name.startswith("Mac:"):
        return "focus"
    return "neutral"

# --- Weather (Open-Meteo: free, no API key needed) ---
# Set your location via env vars; defaults below can be edited.
WEATHER_LAT = os.environ.get("WEATHER_LAT", "44.9778")   # default: Minneapolis, MN
WEATHER_LON = os.environ.get("WEATHER_LON", "-93.2650")
WEATHER_UNITS = os.environ.get("WEATHER_UNITS", "fahrenheit")  # or "celsius"
_weather = {"text": None, "precip": None, "sun": None, "tomorrow": None}  # bg thread
_weather_lock = threading.Lock()

# Open-Meteo weather codes -> short description.
WEATHER_CODES = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
    61: "Rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Freezing rain", 71: "Snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow", 80: "Showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Snow showers", 95: "Thunderstorm",
    96: "Thunderstorm", 99: "Thunderstorm",
}


# Weather codes that count as precipitation for the alert line.
PRECIP_CODES = {51, 53, 55, 61, 63, 65, 66, 67, 71, 73, 75, 77,
                80, 81, 82, 85, 86, 95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}


def weather_thread():
    """Fetch weather every 15 minutes in the background so a slow or missing
    network never blocks the display. Also pulls sunrise/sunset and today's
    precipitation so the display can show a sun line and a precip alert."""
    unit = "fahrenheit" if WEATHER_UNITS.startswith("f") else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
        "&current=temperature_2m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,"
        "precipitation_probability_max,sunrise,sunset"
        f"&temperature_unit={unit}&timezone=auto&forecast_days=2"
    )
    deg = "F" if unit == "fahrenheit" else "C"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.load(resp)
            cur = data.get("current", {})
            daily = data.get("daily", {})
            temp = round(cur.get("temperature_2m"))
            code = cur.get("weather_code", 0)
            desc = WEATHER_CODES.get(code, "")
            hi = round(daily.get("temperature_2m_max", [None])[0])
            lo = round(daily.get("temperature_2m_min", [None])[0])
            text = f"{temp}\u00b0{deg}  {desc}   H:{hi}\u00b0  L:{lo}\u00b0"

            # Precip alert: use today's max precip probability + daily code.
            precip = None
            day_code = (daily.get("weather_code") or [None])[0]
            prob = (daily.get("precipitation_probability_max") or [None])[0]
            if day_code in PRECIP_CODES and prob and prob >= 30:
                kind = "Snow" if day_code in SNOW_CODES else "Rain"
                precip = f"{kind} likely today ({prob}%)"

            # Sunrise/sunset -> short "HH:MM AM" strings.
            sun = None
            try:
                sr = daily.get("sunrise", [None])[0]
                ss = daily.get("sunset", [None])[0]
                if sr and ss:
                    sr_t = datetime.fromisoformat(sr).strftime("%I:%M %p").lstrip("0")
                    ss_t = datetime.fromisoformat(ss).strftime("%I:%M %p").lstrip("0")
                    sun = f"Sunrise {sr_t}   Sunset {ss_t}"
            except (ValueError, TypeError):
                pass

            # Tomorrow's forecast (index 1 of the daily arrays).
            tomorrow = None
            try:
                t_hi = round(daily.get("temperature_2m_max", [None, None])[1])
                t_lo = round(daily.get("temperature_2m_min", [None, None])[1])
                t_code = daily.get("weather_code", [None, None])[1]
                t_desc = WEATHER_CODES.get(t_code, "")
                tomorrow = f"Tomorrow: {t_desc}  H:{t_hi}\u00b0  L:{t_lo}\u00b0"
            except (IndexError, TypeError):
                pass

            # Test override: set WEATHER_TEST_PRECIP to force the alert text,
            # e.g.  WEATHER_TEST_PRECIP="Snow likely today (80%)"
            test_precip = os.environ.get("WEATHER_TEST_PRECIP")
            if test_precip:
                precip = test_precip

            with _weather_lock:
                _weather["text"] = text
                _weather["precip"] = precip
                _weather["sun"] = sun
                _weather["tomorrow"] = tomorrow
            # Success: next refresh in 15 minutes.
            time.sleep(15 * 60)
            continue
        except Exception:
            # Fetch failed; still honor the test override so it's always visible.
            test_precip = os.environ.get("WEATHER_TEST_PRECIP")
            if test_precip:
                with _weather_lock:
                    _weather["precip"] = test_precip
            # Retry soon after a failure (e.g. Wi-Fi just came back) instead
            # of waiting a full 15 minutes.
            time.sleep(60)


def get_weather():
    """Return (text, precip_alert_or_None, sun_line_or_None, tomorrow_or_None)."""
    with _weather_lock:
        return (_weather["text"], _weather["precip"],
                _weather["sun"], _weather["tomorrow"])


# --- Under-voltage monitoring (Raspberry Pi power health) ---
# vcgencmd get_throttled returns a hex bitmask. Bit 0 = under-voltage NOW,
# bit 16 = under-voltage has occurred since boot.
_power = {"warn": None}  # None = ok/unknown, str = warning text
_power_lock = threading.Lock()


def power_thread():
    """Check the Pi's throttling status every 60s. If under-voltage is/has been
    detected, expose a short warning string for the display."""
    import subprocess
    while True:
        warn = None
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"],
                                 capture_output=True, text=True, timeout=5)
            # Output looks like: "throttled=0x50005"
            val = out.stdout.strip().split("=")[-1]
            bits = int(val, 16)
            if bits & 0x1:
                warn = "Low power - check power supply"
            elif bits & 0x10000:
                warn = "Under-voltage detected earlier"
        except Exception:
            warn = None  # vcgencmd not available (e.g. not on a Pi) -> no warning
        with _power_lock:
            _power["warn"] = warn
        time.sleep(60)


def get_power_warning():
    with _power_lock:
        return _power["warn"]


# --- Connectivity indicator ---
_net = {"online": False}
_net_lock = threading.Lock()


def net_thread():
    """Ping a reliable host every 20s to know if the Pi is online, so the
    display can show a Wi-Fi connected/disconnected indicator."""
    import subprocess
    while True:
        online = False
        for host in ("1.1.1.1", "8.8.8.8"):
            try:
                r = subprocess.run(["ping", "-c", "1", "-W", "2", host],
                                   capture_output=True, timeout=4)
                if r.returncode == 0:
                    online = True
                    break
            except Exception:
                pass
        with _net_lock:
            _net["online"] = online
        time.sleep(20)


def is_online():
    with _net_lock:
        return _net["online"]


def ram_usage_str():
    """Return a short RAM usage string like 'RAM 38%  (1.5/8.0 GB)' from
    /proc/meminfo. Returns '' if unavailable (e.g. not on Linux)."""
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    info[parts[0].strip()] = int(parts[1].strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if total <= 0:
            return ""
        used = total - avail
        pct = round(used / total * 100)
        used_gb = used / (1024 * 1024)
        total_gb = total / (1024 * 1024)
        return f"RAM {pct}%  ({used_gb:.1f}/{total_gb:.1f} GB)"
    except (OSError, ValueError):
        return ""

# --- Appearance ---
BG_COLOR = (26, 26, 46)        # dark navy
PANEL_COLOR = (22, 33, 62)     # slightly lighter panel
HEADER_COLOR = (0, 212, 255)   # cyan
TEXT_COLOR = (234, 234, 234)   # off-white
DONE_COLOR = (120, 120, 130)   # grey for completed
CLOCK_COLOR = (110, 110, 120)
WARN_COLOR = (233, 69, 96)     # red for warnings (under-voltage, etc.)

REFRESH_SECONDS = 5            # how often to re-read the database

# --- Daily Stoic quote (one per day, changes at midnight) ---
QUOTES = [
    "You have power over your mind - not outside events. - Marcus Aurelius",
    "We suffer more in imagination than in reality. - Seneca",
    "It's not what happens to you, but how you react that matters. - Epictetus",
    "Waste no more time arguing what a good man should be. Be one. - Marcus Aurelius",
    "Luck is what happens when preparation meets opportunity. - Seneca",
    "No man is free who is not master of himself. - Epictetus",
    "The happiness of your life depends on the quality of your thoughts. - Marcus Aurelius",
    "He who fears death will never do anything worthy of a living man. - Seneca",
    "First say to yourself what you would be; then do what you must do. - Epictetus",
    "The best revenge is not to be like your enemy. - Marcus Aurelius",
    "Difficulties strengthen the mind, as labor does the body. - Seneca",
    "Wealth consists not in having great possessions, but in having few wants. - Epictetus",
    "Confine yourself to the present. - Marcus Aurelius",
    "Begin at once to live, and count each separate day as a separate life. - Seneca",
    "Don't explain your philosophy. Embody it. - Epictetus",
    "The soul becomes dyed with the color of its thoughts. - Marcus Aurelius",
    "While we wait for life, life passes. - Seneca",
    "It is not death that a man should fear, but never beginning to live. - Marcus Aurelius",
    "Man conquers the world by conquering himself. - Zeno of Citium",
    "How long are you going to wait before you demand the best for yourself? - Epictetus",
    "If it is not right, do not do it; if it is not true, do not say it. - Marcus Aurelius",
    "We are more often frightened than hurt; our troubles spring more from supposition than reality. - Seneca",
    "Circumstances don't make the man, they only reveal him to himself. - Epictetus",
    "Very little is needed to make a happy life. - Marcus Aurelius",
    "As is a tale, so is life: not how long it is, but how good it is. - Seneca",
]


def current_quote():
    """Return today's Stoic quote. The same quote shows all day and rotates
    to the next one at midnight (deterministic from the date, no per-frame cost)."""
    day_number = int(time.time() // 86400)   # days since epoch
    return QUOTES[day_number % len(QUOTES)]

# Rotation in degrees: 0 (landscape), 90, 180, or 270.
# Set via env var, e.g.  ROTATE=90 ./start-lite.sh
# 90/270 give a PORTRAIT (tall) layout. Use 90 vs 270 depending on which way
# the monitor is physically turned.
try:
    ROTATE = int(os.environ.get("ROTATE", "0")) % 360
except ValueError:
    ROTATE = 0


def read_items(list_type):
    """Read items for a list from the database. Returns [] if DB not ready."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT text, done FROM items WHERE list_type = ? "
            "ORDER BY done ASC, created_at DESC",
            (list_type,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def _habit_streak(days_set):
    """Consecutive-day streak ending today (or yesterday if today not done)."""
    if not days_set:
        return 0
    from datetime import timedelta
    today = datetime.now().date()
    start = today if today.isoformat() in days_set else today - timedelta(days=1)
    streak, d = 0, start
    while d.isoformat() in days_set:
        streak += 1
        d -= timedelta(days=1)
    return streak


HABIT_GRID_DAYS = 14  # days shown on the display grid (7 cols x 2 rows per card)


def read_habits():
    """Read habits from the DB with today's done-state, current streak, and a
    short day-by-day history (for the on-screen grid). Returns [] if not ready."""
    if not os.path.exists(DB_PATH):
        return []
    from datetime import timedelta
    today_date = datetime.now().date()
    today = today_date.isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name FROM habits ORDER BY position, id"
        ).fetchall()
        result = []
        for r in rows:
            logs = conn.execute(
                "SELECT day FROM habit_log WHERE habit_id = ?", (r["id"],)
            ).fetchall()
            days = {lr["day"] for lr in logs}
            history = [
                (today_date - timedelta(days=i)).isoformat() in days
                for i in range(HABIT_GRID_DAYS - 1, -1, -1)  # oldest -> today
            ]
            result.append({
                "name": r["name"],
                "done": today in days,
                "streak": _habit_streak(days),
                "history": history,
            })
        conn.close()
        return result
    except sqlite3.Error:
        return []


def read_focus():
    """Return today's 'focus for today' text, or '' if none set today."""
    if not os.path.exists(DB_PATH):
        return ""
    today = datetime.now().date().isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value, day FROM settings WHERE key = 'focus'"
        ).fetchone()
        conn.close()
        if row and row["day"] == today and row["value"]:
            return row["value"]
    except sqlite3.Error:
        pass
    return ""


def read_journal_today():
    """Return today's one-line journal entry, or '' if none written today."""
    today = datetime.now().date().isoformat()
    try:
        with open(JOURNAL_FILE) as f:
            return str(json.load(f).get(today, "") or "")
    except (OSError, json.JSONDecodeError):
        return ""


# Health data lives in the tracker process (port 5050). Fetch it over localhost
# in a background thread so the draw loop never blocks on the network.
_health_data = {"data": None}
_health_lock = threading.Lock()


def _health_poll_thread():
    url = "http://127.0.0.1:5050/health-data"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                d = json.load(r)
            with _health_lock:
                _health_data["data"] = d if d.get("enabled") else None
        except Exception:
            pass  # keep last value on failure
        time.sleep(300)  # refresh every 5 min


def read_health():
    with _health_lock:
        return _health_data["data"]


def read_tracker():
    """Read the time-tracker log + state. Returns a dict with recent check-ins
    and seconds until the next check-in, or None if the tracker isn't set up."""
    if not os.path.exists(TRACKER_LOG) and not os.path.exists(TRACKER_STATE):
        return None
    recent = []
    summary = []
    app_opens = []
    total_today = 0
    try:
        with open(TRACKER_LOG) as f:
            entries = json.load(f)
        # Only show TODAY's check-ins, so the display resets at midnight.
        today = datetime.now().date().isoformat()
        todays = [e for e in entries
                  if str(e.get("timestamp", "")).startswith(today)]
        recent = todays[-8:]
        total_today = len(todays)
        # Per-category tallies.
        per_min = TRACKER_INTERVAL_MIN
        counts = {}
        for e in todays:
            cat = e.get("category") or "Other"
            counts[cat] = counts.get(cat, 0) + 1
        # Time-based categories (from check-ins). App categories are excluded
        # here; their real time comes from app_usage.json (open->close sessions).
        summary = sorted(
            ({"category": c, "minutes": n * per_min}
             for c, n in counts.items() if c not in COUNT_ONLY_CATEGORIES),
            key=lambda s: -s["minutes"],
        )
    except (OSError, json.JSONDecodeError):
        pass

    # App usage: real time spent + open count, from open/close sessions.
    app_opens = []
    focus_stats = None
    mac_week = []
    try:
        with open(APPUSE_FILE) as f:
            au = json.load(f)
        today = datetime.now().date().isoformat()
        totals = au.get("totals", {}).get(today, {})
        opens = au.get("opens", {}).get(today, {})
        names = set(totals) | set(opens)
        rows = sorted(
            ({"category": a, "seconds": totals.get(a, 0),
              "opens": opens.get(a, 0),
              "over_limit": bool(APP_TIME_LIMIT_SEC and
                                 totals.get(a, 0) >= APP_TIME_LIMIT_SEC)}
             for a in names),
            key=lambda r: -r["seconds"],
        )
        # Phone apps shown fully; Mac apps (prefixed "Mac:") capped to top 3.
        phone_rows = [r for r in rows if not r["category"].startswith("Mac:")]
        mac_rows = [r for r in rows if r["category"].startswith("Mac:")][:3]
        app_opens = phone_rows + mac_rows

        # Focus vs distraction + longest focus streak (today).
        focus_sec = sum(s for a, s in totals.items() if classify_app(a) == "focus")
        distraction_sec = sum(s for a, s in totals.items()
                              if classify_app(a) == "distraction")
        longest_focus = au.get("focus_streak", {}).get(today, {}).get("longest", 0)
        focus_stats = {"focus_sec": focus_sec, "distraction_sec": distraction_sec,
                       "longest_focus_sec": longest_focus}

        # Weekly Mac app trend: 7-day totals for Mac apps (top 3).
        from datetime import timedelta
        day_keys = [(datetime.now().date() - timedelta(days=i)).isoformat()
                    for i in range(6, -1, -1)]
        week_totals = {}
        for dk in day_keys:
            for a, s in au.get("totals", {}).get(dk, {}).items():
                if a.startswith("Mac:"):
                    week_totals[a] = week_totals.get(a, 0) + s
        mac_week = sorted(
            ({"app": a, "seconds": s} for a, s in week_totals.items()),
            key=lambda r: -r["seconds"])[:3]
    except (OSError, json.JSONDecodeError):
        focus_stats = None
        mac_week = []

    next_in = None
    is_awake = True
    try:
        with open(TRACKER_STATE) as f:
            state = json.load(f)
        is_awake = state.get("is_awake", True)
        nct = state.get("next_checkin_time")
        if nct is not None:
            next_in = max(0, int(nct - time.time()))
    except (OSError, json.JSONDecodeError):
        pass

    # Today's mood: latest value + average.
    mood = None
    try:
        with open(MOOD_FILE) as f:
            all_moods = json.load(f)
        today = datetime.now().date().isoformat()
        todays = [m for m in all_moods
                  if str(m.get("timestamp", "")).startswith(today)]
        if todays:
            avg = sum(m["value"] for m in todays) / len(todays)
            mood = {"latest": todays[-1]["value"], "avg": avg, "count": len(todays)}
    except (OSError, json.JSONDecodeError):
        pass

    # Check-in compliance (how well you've kept up today).
    compliance = None
    try:
        now = datetime.now()
        start_min = COMPLIANCE_START_HOUR * 60
        end_min = COMPLIANCE_END_HOUR * 60
        now_min = now.hour * 60 + now.minute
        interval_min = max(1, TRACKER_INTERVAL_MIN)
        elapsed_min = max(0, min(now_min, end_min) - start_min)
        expected = elapsed_min // interval_min
        done = total_today  # today's check-in count
        pct = min(100, int(round((done / expected) * 100))) if expected else 100
        before = now_min < start_min
        behind = (not before) and expected > 0 and (done / expected) < 0.7
        streak = 0
        try:
            with open(COMPLIANCE_FILE) as f:
                streak = json.load(f).get("streak", 0)
        except (OSError, json.JSONDecodeError):
            pass
        compliance = {"expected": expected, "done": done,
                      "missed": max(0, expected - done), "percent": pct,
                      "behind": behind, "streak": streak}
    except Exception:
        pass

    # Sleep: last night's duration + average bedtime over the last 7 nights.
    sleep = None
    try:
        with open(SLEEP_FILE) as f:
            events = sorted(json.load(f), key=lambda e: e.get("timestamp", ""))
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
                                   "dur": dur})
                pending = None
                in_bed = False
        recent_n = nights[-7:]
        if recent_n:
            # circular mean of bedtimes (handles times around midnight)
            xs = ys = 0.0
            for n in recent_n:
                ang = (n["bed_min"] / 1440.0) * 2 * math.pi
                xs += math.cos(ang)
                ys += math.sin(ang)
            ang = math.atan2(ys, xs)
            if ang < 0:
                ang += 2 * math.pi
            avg_bed = int(round((ang / (2 * math.pi)) * 1440)) % 1440
            sleep = {"last_dur_min": recent_n[-1]["dur"],
                     "avg_bed_min": avg_bed, "in_bed": in_bed}
        elif in_bed:
            sleep = {"last_dur_min": None, "avg_bed_min": None, "in_bed": True}
    except (OSError, json.JSONDecodeError):
        pass

    # Water: today's bottles (1 L) vs goal.
    water = None
    try:
        with open(WATER_FILE) as f:
            wdata = json.load(f)
        bottles = float(wdata.get(datetime.now().date().isoformat(), 0) or 0)
        water = {"bottles": round(bottles, 1), "goal": WATER_GOAL}
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    # Daily metric (e.g. weight): latest value + change vs previous day.
    metric = None
    try:
        with open(METRIC_FILE) as f:
            entries = json.load(f)
        by_day = {}
        for e in entries:
            d = str(e.get("date") or str(e.get("timestamp", ""))[:10])
            if d:
                by_day[d] = e.get("value")
        ordered = sorted(by_day.items())
        if ordered:
            latest = ordered[-1][1]
            prev = ordered[-2][1] if len(ordered) >= 2 else None
            change = (round(latest - prev, 2)
                      if (latest is not None and prev is not None) else None)
            metric = {"latest": latest, "change": change,
                      "label": METRIC_LABEL, "unit": METRIC_UNIT}
    except (OSError, json.JSONDecodeError, ValueError):
        pass

    return {"recent": recent, "next_in": next_in, "is_awake": is_awake,
            "summary": summary, "app_opens": app_opens,
            "total_today": total_today,
            "focus_stats": focus_stats, "mac_week": mac_week,
            "mood": mood, "compliance": compliance, "sleep": sleep,
            "water": water, "metric": metric}


def last_update_str():
    """Return a short 'updated' timestamp for when the code genuinely last
    changed -- the date of the current HEAD commit. This reflects real code
    changes, not just when git last checked/fetched. Returns '' if unavailable."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "-C", BASE_DIR, "log", "-1", "--format=%cd", "--date=format:%b %d %I:%M %p"],
            capture_output=True, text=True, timeout=5,
        )
        stamp = out.stdout.strip()
        if stamp:
            return "updated " + stamp
    except Exception:
        pass
    # Fallback: mtime of the commit pointer file.
    try:
        mtime = os.path.getmtime(os.path.join(BASE_DIR, ".git", "HEAD"))
        return "updated " + datetime.fromtimestamp(mtime).strftime("%b %d %I:%M %p")
    except OSError:
        return ""


def draw_panel(screen, fonts, rect, title, items):
    """Draw one list panel (title + items) inside the given rect."""
    x, y, w, h = rect
    panel_rect = pygame.Rect(x, y, w, h)
    pygame.draw.rect(screen, PANEL_COLOR, panel_rect, border_radius=16)

    pad = 24
    # Title (half-size header for the list panels)
    title_surf = fonts["panel_title"].render(title, True, HEADER_COLOR)
    title_x = x + (w - title_surf.get_width()) // 2
    screen.blit(title_surf, (title_x, y + pad))

    # Items
    item_font = fonts["item"]
    line_y = y + pad + title_surf.get_height() + 20
    line_height = item_font.get_height() + 10
    max_w = w - 2 * pad
    bottom = y + h - pad

    def wrap(text, indent_w):
        """Split text into lines that fit within (max_w - indent_w)."""
        avail = max_w - indent_w
        words = text.split(" ")
        lines = []
        cur = ""
        for word in words:
            trial = word if not cur else cur + " " + word
            if item_font.size(trial)[0] <= avail or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    for item in items:
        if line_y + line_height > bottom:
            break  # ran out of vertical space in this panel
        done = bool(item["done"])
        color = DONE_COLOR if done else TEXT_COLOR
        # Use a plain ASCII prefix ("- ") so it always renders. Done items get
        # crossed off with a strikethrough line drawn across the text below.
        prefix = "- "
        prefix_w = item_font.size(prefix)[0]

        wrapped = wrap(item["text"], prefix_w)
        for i, ln in enumerate(wrapped):
            if line_y + line_height > bottom:
                break
            row_start_x = x + pad
            if i == 0:
                prefix_surf = item_font.render(prefix, True, color)
                screen.blit(prefix_surf, (row_start_x, line_y))
            text_x = x + pad + prefix_w
            text_surf = item_font.render(ln, True, color)
            screen.blit(text_surf, (text_x, line_y))

            # Cross it off: draw a line through the whole row for done items.
            if done:
                text_h = item_font.get_height()
                strike_y = line_y + text_h // 2
                # First line: strike from the prefix; wrapped lines: from text.
                strike_x1 = row_start_x if i == 0 else text_x
                strike_x2 = text_x + text_surf.get_width()
                pygame.draw.line(screen, DONE_COLOR,
                                 (strike_x1, strike_y), (strike_x2, strike_y), 2)

            line_y += line_height

    if not items:
        empty_surf = fonts["item"].render("No items yet", True, DONE_COLOR)
        screen.blit(empty_surf, (x + pad, line_y))


CARD_COLOR = (15, 52, 96)     # #0f3460 - matches the phone habit cards
STREAK_COLOR = (255, 183, 3)  # amber, like the phone's fire streak
# Heatmap palette: gray for missed, bright cyan shades for done (by intensity).
# Brightened so lit squares pop; higher intensities push toward near-white cyan.
GH_EMPTY = (60, 64, 72)       # #3c4048 - gray empty cell
GH_GREENS = [
    (0, 200, 240),    # already-bright base cyan
    (80, 225, 255),
    (150, 240, 255),
    (210, 250, 255),  # near-white cyan (hottest)
]


def draw_habits(screen, fonts, rect, habits):
    """Draw habits as CARDS, matching the phone layout: each card has a header
    row (checkbox + name + streak) with a GitHub-style 7-wide day grid below.
    Cards are laid out left-to-right and wrap onto multiple rows."""
    x, y, w, h = rect
    pygame.draw.rect(screen, PANEL_COLOR, pygame.Rect(x, y, w, h), border_radius=12)
    if not habits:
        return

    font = fonts["clock"]
    small = fonts["tiny"]
    pad = 16               # outer padding inside the band
    cpad = 10              # padding inside each card
    cols = 3               # cards per row (like the phone's wrapping cards)
    rows = (len(habits) + cols - 1) // cols
    gap = 12

    card_w = (w - 2 * pad - (cols - 1) * gap) // cols
    card_h = (h - 2 * pad - (rows - 1) * gap) // rows if rows else (h - 2 * pad)

    # Grid: 7 columns x 2 rows (14 days) filling the card width below the header.
    gcols = 7
    ncells = len(habits[0].get("history", [])) if habits else HABIT_GRID_DAYS
    grows = (ncells + gcols - 1) // gcols
    cell_gap = 4
    # Cell size fills the card width across 7 columns.
    cell = max(8, (card_w - 2 * cpad - (gcols - 1) * cell_gap) // gcols)

    for i, hb in enumerate(habits):
        r, c = divmod(i, cols)
        cx = x + pad + c * (card_w + gap)
        cy = y + pad + r * (card_h + gap)
        pygame.draw.rect(screen, CARD_COLOR,
                         pygame.Rect(cx, cy, card_w, card_h), border_radius=10)

        done = hb["done"]

        # --- Centered header on top: checkbox + name (+ streak) ---
        box = font.get_height() - 6
        name_color = DONE_COLOR if done else TEXT_COLOR
        name_surf = font.render(hb["name"], True, name_color)
        streak = hb.get("streak", 0)
        streak_surf = small.render(f"{streak}d", True, STREAK_COLOR) if streak > 0 else None

        # Compute total header width to center it.
        hdr_w = box + 8 + name_surf.get_width()
        if streak_surf:
            hdr_w += 8 + streak_surf.get_width()
        hx = cx + (card_w - hdr_w) // 2
        hy = cy + cpad

        box_rect = pygame.Rect(hx, hy, box, box)
        if done:
            pygame.draw.rect(screen, HEADER_COLOR, box_rect, border_radius=4)
            pygame.draw.lines(screen, BG_COLOR, False, [
                (box_rect.left + box * 0.22, box_rect.top + box * 0.52),
                (box_rect.left + box * 0.42, box_rect.top + box * 0.72),
                (box_rect.left + box * 0.78, box_rect.top + box * 0.28),
            ], 3)
        else:
            pygame.draw.rect(screen, DONE_COLOR, box_rect, width=2, border_radius=4)
        screen.blit(name_surf, (hx + box + 8, hy + (box - name_surf.get_height()) // 2))
        if streak_surf:
            screen.blit(streak_surf,
                        (hx + box + 8 + name_surf.get_width() + 8, hy + 2))

        # --- Grid below the header: 7 cols x 2 rows, centered, filling width ---
        grid_w = gcols * cell + (gcols - 1) * cell_gap
        gx = cx + (card_w - grid_w) // 2
        gy = hy + box + 10
        history = hb.get("history", [])
        run = 0
        for j, on in enumerate(history):
            gr, gc = divmod(j, gcols)
            px = gx + gc * (cell + cell_gap)
            py = gy + gr * (cell + cell_gap)
            if on:
                run += 1
                color = GH_GREENS[min(run - 1, len(GH_GREENS) - 1)]
            else:
                run = 0
                color = GH_EMPTY
            pygame.draw.rect(screen, color, pygame.Rect(px, py, cell, cell),
                             border_radius=3)


def _fmt_hm(secs):
    m = secs // 60
    hh, mm = divmod(m, 60)
    return f"{hh}h {mm}m" if hh else f"{mm}m"


def draw_app_opens(screen, fonts, rect, tracker):
    """Draw the 'App Time' box: focus/distraction summary, today's per-app time,
    and a compact weekly Mac trend."""
    x, y, w, h = rect
    pygame.draw.rect(screen, PANEL_COLOR, pygame.Rect(x, y, w, h), border_radius=16)
    pad = 20
    title_surf = fonts["clock"].render("App Time", True, HEADER_COLOR)
    screen.blit(title_surf, (x + pad, y + pad))

    name_font = fonts["item"]
    val_font = fonts["tiny"]
    bottom = y + h - pad
    line_y = y + pad + title_surf.get_height() + 10
    max_w = w - 2 * pad

    # --- Focus vs distraction summary line + longest streak ---
    fs = tracker.get("focus_stats")
    if fs and (fs["focus_sec"] or fs["distraction_sec"]):
        fd_surf = val_font.render(
            f"Focus {_fmt_hm(fs['focus_sec'])}  /  Distract {_fmt_hm(fs['distraction_sec'])}",
            True, HEADER_COLOR)
        screen.blit(fd_surf, (x + pad, line_y))
        line_y += val_font.get_height() + 3
        if fs["longest_focus_sec"]:
            ls_surf = val_font.render(
                f"Longest focus: {_fmt_hm(fs['longest_focus_sec'])}", True, DONE_COLOR)
            screen.blit(ls_surf, (x + pad, line_y))
            line_y += val_font.get_height() + 8

    # --- Today's per-app time (name, then time + opens) ---
    app_opens = tracker.get("app_opens", [])
    row_h = name_font.get_height() + val_font.get_height() + 12
    if not app_opens:
        empty = name_font.render("None today", True, DONE_COLOR)
        screen.blit(empty, (x + pad, line_y))
        line_y += row_h
    for a in app_opens:
        if line_y + row_h > bottom:
            break
        tstr = _fmt_hm(a.get("seconds", 0))
        opens = a.get("opens", 0)
        over = a.get("over_limit")
        name_color = WARN_COLOR if over else TEXT_COLOR
        val_color = WARN_COLOR if over else HEADER_COLOR
        name = a["category"]
        name_surf = name_font.render(name, True, name_color)
        while name_surf.get_width() > max_w and len(name) > 3:
            name = name[:-2]
            name_surf = name_font.render(name + "\u2026", True, name_color)
        screen.blit(name_surf, (x + pad, line_y))
        val_surf = val_font.render(f"{tstr}    {opens}\u00d7", True, val_color)
        screen.blit(val_surf, (x + pad, line_y + name_font.get_height() + 2))
        line_y += row_h

    # --- Weekly Mac trend (if room) ---
    mac_week = tracker.get("mac_week") or []
    if mac_week and line_y + val_font.get_height() * (len(mac_week) + 1) + 10 < bottom:
        line_y += 6
        hdr = val_font.render("This week (Mac)", True, DONE_COLOR)
        screen.blit(hdr, (x + pad, line_y))
        line_y += val_font.get_height() + 4
        for m in mac_week:
            if line_y + val_font.get_height() > bottom:
                break
            nm = m["app"].replace("Mac:", "")
            row = val_font.render(nm, True, TEXT_COLOR)
            tv = val_font.render(_fmt_hm(m["seconds"]), True, HEADER_COLOR)
            screen.blit(row, (x + pad, line_y))
            screen.blit(tv, (x + w - pad - tv.get_width(), line_y))
            line_y += val_font.get_height() + 3


def draw_health(screen, fonts, rect, health):
    """Draw the health band: steps (with goal bar), sleep, resting HR, active
    minutes as evenly-spaced columns, each with a label, a big value, and a
    consistent third line so the columns align."""
    x, y, w, h = rect
    pygame.draw.rect(screen, PANEL_COLOR, pygame.Rect(x, y, w, h), border_radius=16)
    pad = 20

    val_font = fonts["clock"]   # bigger values (matches the tracker band)
    lab_font = fonts["tiny"]
    sub_font = fonts["tiny"]

    cols = 4
    col_w = (w - 2 * pad) // cols
    # Row layout inside a column: label, value, third line (bar/sub).
    lab_h = lab_font.get_height()
    val_h = val_font.get_height()
    third_h = max(sub_font.get_height(), 12)
    block_h = lab_h + 6 + val_h + 8 + third_h
    # No title now: center the stat block vertically across the whole card.
    cy = y + max(pad, (h - block_h) // 2)

    def draw_stat(ci, label, value, sub=None, bar=None, value_color=HEADER_COLOR):
        cx = x + pad + ci * col_w
        lab = lab_font.render(label, True, DONE_COLOR)
        screen.blit(lab, (cx, cy))
        val = val_font.render(value, True, value_color)
        screen.blit(val, (cx, cy + lab_h + 6))
        third_y = cy + lab_h + 6 + val_h + 8
        if bar is not None:
            bw = col_w - 16
            bh = 10
            pygame.draw.rect(screen, (15, 52, 96),
                             pygame.Rect(cx, third_y, bw, bh), border_radius=5)
            fillw = int(bw * max(0, min(1, bar)))
            fill_col = (46, 204, 113) if bar >= 1 else HEADER_COLOR
            if fillw > 0:
                pygame.draw.rect(screen, fill_col,
                                 pygame.Rect(cx, third_y, fillw, bh), border_radius=5)
        elif sub:
            s = sub_font.render(sub, True, TEXT_COLOR)
            screen.blit(s, (cx, third_y))

    # Steps
    steps = health.get("steps")
    goal = health.get("steps_goal") or 10000
    if steps is not None:
        frac = steps / goal if goal else 0
        draw_stat(0, "STEPS", f"{steps:,}", bar=frac)
    else:
        draw_stat(0, "STEPS", "--")

    # Sleep
    sl = health.get("sleep")
    if sl and sl.get("duration_min") is not None:
        dm = sl["duration_min"]
        dur = f"{dm // 60}h {dm % 60}m"

        def _t(iso):
            try:
                d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
                return d.strftime("%H:%M")
            except (ValueError, AttributeError):
                return "--"
        sub = f"{_t(sl.get('bedtime'))} \u2192 {_t(sl.get('waketime'))}"
        draw_stat(1, "SLEEP", dur, sub=sub)
    else:
        draw_stat(1, "SLEEP", "--")

    # Resting HR
    hr = health.get("resting_hr")
    draw_stat(2, "RESTING HR",
              f"{hr}" if hr is not None else "--",
              sub="bpm" if hr is not None else None)

    # Active minutes
    am = health.get("active_minutes")
    draw_stat(3, "ACTIVE",
              f"{am}" if am is not None else "--",
              sub="min" if am is not None else None)


def draw_tracker(screen, fonts, rect, tracker):
    """Draw the time-tracker band: next check-in countdown + recent check-ins."""
    x, y, w, h = rect
    pygame.draw.rect(screen, PANEL_COLOR, pygame.Rect(x, y, w, h), border_radius=16)
    pad = 20
    item_font = fonts["item"]
    small_font = fonts["clock"]

    # Header row: "Time Tracker" + mood (if logged) + countdown on the right.
    title_surf = fonts["clock"].render("Time Tracker", True, HEADER_COLOR)
    screen.blit(title_surf, (x + pad, y + pad))

    mood = tracker.get("mood")
    if mood:
        # Bundled font has no emoji, so show a worded mood: "Mood 4/5".
        mood_surf = fonts["tiny"].render(
            f"Mood {mood['latest']}/5  (avg {mood['avg']:.1f})", True, DONE_COLOR)
        screen.blit(mood_surf, (x + pad + title_surf.get_width() + 16, y + pad + 6))

    # Compliance line: check-ins done/expected, red + warning when behind.
    comp = tracker.get("compliance")
    if comp and comp["expected"] > 0:
        if comp["behind"]:
            ctext = (f"\u26a0 BEHIND: {comp['done']}/{comp['expected']} check-ins "
                     f"({comp['percent']}%)  -  {comp['missed']} missed")
            ccolor = WARN_COLOR
        else:
            ctext = (f"Check-ins {comp['done']}/{comp['expected']} ({comp['percent']}%)"
                     f"   streak {comp['streak']}d")
            ccolor = HEADER_COLOR if comp["percent"] >= 90 else TEXT_COLOR
        c_surf = fonts["tiny"].render(ctext, True, ccolor)
        # Draw just under the title line.
        screen.blit(c_surf, (x + pad, y + pad + title_surf.get_height() + 4))

    # Sleep line: last night's duration + average bedtime (second sub-line).
    sleep = tracker.get("sleep")
    comp_has_line = bool(comp and comp["expected"] > 0)
    if sleep:
        sub = fonts["tiny"].get_height() + 6
        sleep_y = y + pad + title_surf.get_height() + 4 + (sub if comp_has_line else 0)
        if sleep.get("in_bed") and sleep.get("last_dur_min") is None:
            stext = "Sleep: in bed now"
            scolor = DONE_COLOR
        else:
            dur = sleep.get("last_dur_min")
            hh, mm = divmod(dur, 60) if dur is not None else (0, 0)
            dstr = f"{hh}h {mm}m" if hh else f"{mm}m"
            bm = sleep.get("avg_bed_min")
            bstr = f"{(bm // 60) % 24:02d}:{bm % 60:02d}" if bm is not None else "--"
            inbed = "  (in bed)" if sleep.get("in_bed") else ""
            stext = f"Sleep: last {dstr}, avg bed {bstr}{inbed}"
            scolor = HEADER_COLOR
        s_surf = fonts["tiny"].render(stext, True, scolor)
        screen.blit(s_surf, (x + pad, sleep_y))

    # Water + daily metric line (third sub-line under the title).
    water = tracker.get("water")
    metric = tracker.get("metric")
    if water or metric:
        sub = fonts["tiny"].get_height() + 6
        n_above = (1 if comp_has_line else 0) + (1 if sleep else 0)
        wm_y = y + pad + title_surf.get_height() + 4 + sub * n_above
        parts = []
        if water:
            b = water['bottles']
            b_str = f"{b:.1f}".rstrip("0").rstrip(".")
            parts.append(f"Water {b_str}/{water['goal']}L")
        if metric and metric.get("latest") is not None:
            chg = ""
            if metric.get("change"):
                arrow = "\u2191" if metric["change"] > 0 else "\u2193"
                chg = f" {arrow}{abs(metric['change'])}"
            parts.append(f"{metric['label']} {metric['latest']}{metric['unit']}{chg}")
        if parts:
            wm_surf = fonts["tiny"].render("   ".join(parts), True, HEADER_COLOR)
            screen.blit(wm_surf, (x + pad, wm_y))

    next_in = tracker.get("next_in")
    awake = tracker.get("is_awake", True)
    if not awake:
        cd_text = "Sleeping"
        cd_color = DONE_COLOR
    elif next_in is None:
        cd_text = ""
        cd_color = TEXT_COLOR
    else:
        m, s = divmod(int(next_in), 60)
        cd_text = f"next: {m:02d}:{s:02d}"
        cd_color = TEXT_COLOR
    if cd_text:
        cd_surf = fonts["clock"].render(cd_text, True, cd_color)
        screen.blit(cd_surf, (x + w - pad - cd_surf.get_width(), y + pad))

    # Two columns below the header: recent check-ins (left) + today summary (right).
    # Leave extra room if the compliance line was drawn under the title.
    comp = tracker.get("compliance")
    sub_h = fonts["tiny"].get_height() + 6
    comp_offset = sub_h if (comp and comp["expected"] > 0) else 0
    sleep_offset = sub_h if tracker.get("sleep") else 0
    wm_offset = sub_h if (tracker.get("water") or tracker.get("metric")) else 0
    content_y = (y + pad + title_surf.get_height() + 12
                 + comp_offset + sleep_offset + wm_offset)
    line_h = item_font.get_height() + 8
    bottom = y + h - pad
    summary = tracker.get("summary") or []
    # Right column reserved for the summary only if there is category data.
    right_w = int(w * 0.38) if summary else 0
    left_right = x + w - pad - right_w

    recent = tracker.get("recent") or []
    line_y = content_y
    if not recent:
        empty = small_font.render("No check-ins yet", True, DONE_COLOR)
        screen.blit(empty, (x + pad, line_y))
    else:
        for e in reversed(recent):  # newest first
            if line_y + line_h > bottom:
                break
            try:
                t = datetime.fromisoformat(e["timestamp"]).strftime("%I:%M %p")
            except (ValueError, KeyError):
                t = ""
            time_surf = small_font.render(t, True, HEADER_COLOR)
            screen.blit(time_surf, (x + pad, line_y + 2))
            time_w = time_surf.get_width() + 12

            text = e.get("text", "")
            max_w = left_right - (x + pad) - time_w - 12
            rendered = item_font.render(text, True, TEXT_COLOR)
            if rendered.get_width() > max_w:
                while rendered.get_width() > max_w and len(text) > 3:
                    text = text[:-2]
                    rendered = item_font.render(text + "\u2026", True, TEXT_COLOR)
            screen.blit(rendered, (x + pad + time_w, line_y))
            line_y += line_h

    # Right column: today's per-category summary.
    if summary:
        sx = left_right + 12
        sy = content_y
        hdr = small_font.render("Today", True, DONE_COLOR)
        screen.blit(hdr, (sx, sy))
        sy += hdr.get_height() + 6
        for s in summary:
            if sy + line_h > bottom:
                break
            hh, mm = divmod(s["minutes"], 60)
            vstr = f"{hh}h {mm}m" if hh else f"{mm}m"
            cat_surf = item_font.render(s["category"], True, TEXT_COLOR)
            val_surf = item_font.render(vstr, True, HEADER_COLOR)
            screen.blit(cat_surf, (sx, sy))
            screen.blit(val_surf, (x + w - pad - val_surf.get_width(), sy))
            sy += line_h


def main():
    pygame.init()
    pygame.mouse.set_visible(False)

    # Start background monitors (non-blocking): weather, power, connectivity.
    threading.Thread(target=weather_thread, daemon=True).start()
    threading.Thread(target=power_thread, daemon=True).start()
    threading.Thread(target=net_thread, daemon=True).start()
    threading.Thread(target=_health_poll_thread, daemon=True).start()

    # Fullscreen at the display's native resolution
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("List Display")
    screen_w, screen_h = screen.get_size()

    # When rotating 90/270, we draw onto an off-screen "canvas" with swapped
    # dimensions (portrait), then rotate it onto the physical screen at flip time.
    if ROTATE in (90, 270):
        canvas = pygame.Surface((screen_h, screen_w))  # portrait: tall & narrow
    elif ROTATE == 180:
        canvas = pygame.Surface((screen_w, screen_h))
    else:
        canvas = screen  # no rotation: draw straight to the screen

    # All layout math uses the CANVAS dimensions (portrait when rotated).
    sw, sh = canvas.get_size()

    # Scale fonts to the screen height so it's readable on any monitor size.
    # Use pygame's bundled font (pygame.font.Font(None, ...)) instead of a system
    # font: it needs no fontconfig/fc-list lookup, which is slow and can time out
    # on low-power boards like the Pi Zero 2 W.
    def make_font(size, bold=False):
        f = pygame.font.Font(None, size)
        f.set_bold(bold)
        return f

    fonts = {
        # Base font sizes on the SMALLER screen dimension so text fits the
        # (often narrow) columns instead of overflowing and getting truncated.
        # Sizes reduced 25% from the earlier defaults (larger divisor = smaller text).
        # Override any size with env vars, e.g. TITLE_PT=60 ITEM_PT=40 CLOCK_PT=28
        "title": make_font(int(os.environ.get("TITLE_PT", max(26, min(sw, sh) // 12))), bold=True),
        "item": make_font(int(os.environ.get("ITEM_PT", max(18, min(sw, sh) // 19)))),
        "clock": make_font(int(os.environ.get("CLOCK_PT", max(14, min(sw, sh) // 27)))),
        "tiny": make_font(max(12, min(sw, sh) // 40)),
        # List panel headers (Todo / Shopping): 0.6x the full title size
        # (half size, then +20%).
        "panel_title": make_font(int(os.environ.get("TITLE_PT", max(26, min(sw, sh) // 12)) * 0.6), bold=True),
    }

    clock = pygame.time.Clock()
    last_refresh = 0.0
    last_tick = time.time()
    todo_items = []
    shopping_items = []
    tracker_data = None
    habits_data = []
    focus_text = ""
    journal_text = ""
    health_data_val = None
    update_str = last_update_str()
    ram_str = ram_usage_str()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                # Press Esc or Q to quit
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

        # Refresh data periodically
        now = time.time()
        if now - last_refresh >= REFRESH_SECONDS:
            todo_items = read_items("todo")
            shopping_items = read_items("shopping")
            tracker_data = read_tracker()
            habits_data = read_habits()
            focus_text = read_focus()
            journal_text = read_journal_today()
            health_data_val = read_health()
            update_str = last_update_str()
            ram_str = ram_usage_str()
            last_tick = now
            last_refresh = now

        # Tick the countdown down smoothly between data refreshes.
        if tracker_data is not None and tracker_data.get("next_in") is not None \
                and tracker_data.get("is_awake", True):
            elapsed = now - last_tick
            if elapsed >= 1:
                tracker_data["next_in"] = max(0, tracker_data["next_in"] - int(elapsed))
                last_tick = now

        # --- Draw (onto canvas) ---
        canvas.fill(BG_COLOR)

        gap = 24
        margin = 24
        # Bottom area holds a rotating quote panel above the clock/date.
        quote_h = fonts["clock"].get_height() + 20     # its own panel/box
        clock_line_h = fonts["clock"].get_height() + 20
        clock_h = clock_line_h + quote_h + gap

        # Weather bar across the top. Main line always shows; a sun line and
        # (when expected) a highlighted precip alert line appear below it.
        weather_text, precip_alert, sun_line, tomorrow_line = get_weather()
        line_h = fonts["clock"].get_height()
        tiny_h = fonts["tiny"].get_height()
        weather_bar_h = line_h + 16
        if sun_line:
            weather_bar_h += tiny_h + 4
        if tomorrow_line:
            weather_bar_h += tiny_h + 4
        if precip_alert:
            weather_bar_h += tiny_h + 4
        wbar = pygame.Rect(margin, margin, sw - 2 * margin, weather_bar_h)
        pygame.draw.rect(canvas, PANEL_COLOR, wbar, border_radius=12)

        wy = margin + 8
        w_surf = fonts["clock"].render(
            weather_text if weather_text else "Weather unavailable",
            True, TEXT_COLOR if weather_text else DONE_COLOR)
        canvas.blit(w_surf, (margin + (wbar.width - w_surf.get_width()) // 2, wy))
        wy += line_h + 4
        if sun_line:
            s_surf = fonts["tiny"].render(sun_line, True, DONE_COLOR)
            canvas.blit(s_surf, (margin + (wbar.width - s_surf.get_width()) // 2, wy))
            wy += tiny_h + 4
        if tomorrow_line:
            tm_surf = fonts["tiny"].render(tomorrow_line, True, DONE_COLOR)
            canvas.blit(tm_surf, (margin + (wbar.width - tm_surf.get_width()) // 2, wy))
            wy += tiny_h + 4
        if precip_alert:
            p_surf = fonts["tiny"].render(precip_alert, True, HEADER_COLOR)
            canvas.blit(p_surf, (margin + (wbar.width - p_surf.get_width()) // 2, wy))
            wy += tiny_h + 4

        # Under-voltage warning: a red flag in the weather bar's top-left corner.
        power_warn = get_power_warning()
        if power_warn:
            pw_surf = fonts["tiny"].render("\u26a0 " + power_warn, True, WARN_COLOR)
            canvas.blit(pw_surf, (margin + 12, margin + 6))

        # Connectivity + RAM usage in the weather bar's top-right corner.
        online = is_online()
        net_txt = "Connected" if online else "Offline"
        net_color = HEADER_COLOR if online else WARN_COLOR
        net_surf = fonts["tiny"].render(net_txt, True, net_color)
        canvas.blit(net_surf, (sw - margin - net_surf.get_width() - 8, margin + 6))
        if ram_str:
            ram_surf = fonts["tiny"].render(ram_str, True, DONE_COLOR)
            canvas.blit(ram_surf,
                        (sw - margin - ram_surf.get_width() - 8,
                         margin + 6 + tiny_h + 3))

        # Everything below the weather bar starts here.
        top = margin + weather_bar_h + gap

        # "Focus for today" banner (only if set), just under the weather bar.
        if focus_text:
            focus_h = fonts["clock"].get_height() + 18
            fbar = pygame.Rect(margin, top, sw - 2 * margin, focus_h)
            pygame.draw.rect(canvas, PANEL_COLOR, fbar, border_radius=12)
            label_surf = fonts["tiny"].render("FOCUS", True, HEADER_COLOR)
            canvas.blit(label_surf, (margin + 14, top + 8))
            ftext = focus_text
            fsurf = fonts["clock"].render(ftext, True, TEXT_COLOR)
            avail = fbar.width - 28 - label_surf.get_width() - 12
            if fsurf.get_width() > avail:
                while fsurf.get_width() > avail and len(ftext) > 4:
                    ftext = ftext[:-2]
                    fsurf = fonts["clock"].render(ftext + "\u2026", True, TEXT_COLOR)
            canvas.blit(fsurf, (margin + 14 + label_surf.get_width() + 12,
                                top + (focus_h - fsurf.get_height()) // 2))
            top += focus_h + gap

        # "Journal" banner (today's one-line entry), just under FOCUS.
        if journal_text:
            jrn_h = fonts["clock"].get_height() + 18
            jbar = pygame.Rect(margin, top, sw - 2 * margin, jrn_h)
            pygame.draw.rect(canvas, PANEL_COLOR, jbar, border_radius=12)
            jlabel_surf = fonts["tiny"].render("JOURNAL", True, HEADER_COLOR)
            canvas.blit(jlabel_surf, (margin + 14, top + 8))
            jtext = journal_text
            jsurf = fonts["clock"].render(jtext, True, TEXT_COLOR)
            javail = jbar.width - 28 - jlabel_surf.get_width() - 12
            if jsurf.get_width() > javail:
                while jsurf.get_width() > javail and len(jtext) > 4:
                    jtext = jtext[:-2]
                    jsurf = fonts["clock"].render(jtext + "\u2026", True, TEXT_COLOR)
            canvas.blit(jsurf, (margin + 14 + jlabel_surf.get_width() + 12,
                                top + (jrn_h - jsurf.get_height()) // 2))
            top += jrn_h + gap

        # Reserve height for the habit cards band (matches phone-style cards:
        # header + a 7-wide, ~2-row day grid). Cards are 3 per row and wrap.
        habits_h = 0
        if habits_data:
            hcols = 3
            hrows = (len(habits_data) + hcols - 1) // hcols
            # Estimate the grid cell size from the expected card width so the
            # reserved height matches the 7x2 grid drawn inside each card.
            band_w = sw - 2 * 24
            est_card_w = (band_w - 2 * 16 - (hcols - 1) * 12) // hcols
            est_cell = max(8, (est_card_w - 2 * 10 - 6 * 4) // 7)
            hdr = fonts["clock"].get_height() + 6
            grid_block = 2 * est_cell + 4              # 2 grid rows + gap
            card_h = hdr + 10 + grid_block + 18
            habits_h = hrows * card_h + (hrows - 1) * 12 + 2 * 16

        tracker_h = 0
        if tracker_data is not None:
            # ~75% taller than the original so more check-ins are visible.
            tracker_h = int(((fonts["item"].get_height() + 8) * 4
                             + fonts["clock"].get_height() + 44) * 1.75)

        # Health band (steps/sleep/HR/active) — one row of stat columns, no title.
        health_h = 0
        if health_data_val:
            health_h = (fonts["tiny"].get_height() + 6        # label row
                        + fonts["clock"].get_height() + 8     # value row (big)
                        + max(fonts["tiny"].get_height(), 12) # third line (bar/sub)
                        + 2 * 20)                             # top+bottom padding

        # Side-by-side full-height columns: Todo left, Shopping right. Their
        # height shrinks to leave room for the health band, habit row,
        # tracker band + clock.
        below = clock_h
        below += (health_h + gap) if health_h else 0
        below += (habits_h + gap) if habits_h else 0
        below += (tracker_h + gap) if tracker_h else 0
        panel_w = (sw - 2 * margin - gap) // 2
        panel_h = sh - top - margin - below
        draw_panel(canvas, fonts,
                   (margin, top, panel_w, panel_h),
                   "Todo List", todo_items)
        draw_panel(canvas, fonts,
                   (margin + panel_w + gap, top, panel_w, panel_h),
                   "Shopping List", shopping_items)

        # Stack below the lists: health band, habit row, then time-tracker band.
        cursor_y = top + panel_h + gap
        if health_h:
            draw_health(canvas, fonts,
                        (margin, cursor_y, sw - 2 * margin, health_h),
                        health_data_val)
            cursor_y += health_h + gap
        if habits_h:
            draw_habits(canvas, fonts,
                        (margin, cursor_y, sw - 2 * margin, habits_h), habits_data)
            cursor_y += habits_h + gap
        if tracker_data is not None:
            band_w = sw - 2 * margin
            # Split the band: tracker on the left, App Opens box on the right.
            apps_w = int(band_w * 0.30)
            tracker_w = band_w - apps_w - gap
            draw_tracker(canvas, fonts,
                         (margin, cursor_y, tracker_w, tracker_h),
                         tracker_data)
            draw_app_opens(canvas, fonts,
                           (margin + tracker_w + gap, cursor_y, apps_w, tracker_h),
                           tracker_data)

        # Daily Stoic quote in its own panel (above the clock). Stoic quotes can
        # be long, so shrink the font to fit the panel width on one line.
        quote_y = sh - clock_h + 4
        qbar = pygame.Rect(margin, quote_y, sw - 2 * margin, quote_h)
        pygame.draw.rect(canvas, PANEL_COLOR, qbar, border_radius=12)
        qtext = current_quote()
        avail_w = qbar.width - 24
        qfont = fonts["clock"]
        quote_surf = qfont.render(qtext, True, HEADER_COLOR)
        if quote_surf.get_width() > avail_w:
            # Fall back to the smaller "tiny" font; still too wide -> truncate.
            qfont = fonts["tiny"]
            quote_surf = qfont.render(qtext, True, HEADER_COLOR)
            if quote_surf.get_width() > avail_w:
                t = qtext
                while quote_surf.get_width() > avail_w and len(t) > 6:
                    t = t[:-2]
                    quote_surf = qfont.render(t + "\u2026", True, HEADER_COLOR)
        canvas.blit(quote_surf,
                    (margin + (qbar.width - quote_surf.get_width()) // 2,
                     quote_y + (quote_h - quote_surf.get_height()) // 2))

        # Clock / date at the very bottom (centered)
        clock_y = quote_y + quote_h + gap
        stamp = time.strftime("%A, %B %d   -   %I:%M %p")
        clock_surf = fonts["clock"].render(stamp, True, CLOCK_COLOR)
        clock_x = (sw - clock_surf.get_width()) // 2
        canvas.blit(clock_surf, (clock_x, clock_y + 2))

        # "Last updated" timestamp in the bottom-right corner (cached on refresh).
        if update_str:
            upd_surf = fonts["tiny"].render(update_str, True, DONE_COLOR)
            canvas.blit(upd_surf, (sw - margin - upd_surf.get_width(), clock_y + 4))

        # Rotate the canvas onto the physical screen if needed.
        if canvas is not screen:
            rotated = pygame.transform.rotate(canvas, ROTATE)
            screen.blit(rotated, (0, 0))

        pygame.display.flip()
        clock.tick(30)  # smooth on the Pi 5 (was capped at 10 for the Zero 2 W)

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
