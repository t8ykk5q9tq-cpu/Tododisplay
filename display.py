#!/usr/bin/env python3
"""
display.py - Lightweight fullscreen list display for low-RAM Raspberry Pi boards
(e.g. Pi Zero 2 W). Draws the todo and shopping lists directly to the screen with
pygame, reading from the same SQLite database the Flask web app uses.

No browser or desktop environment required. Add/remove items from any device via
the Flask web interface at http://<pi-ip>:5000 -- changes appear here automatically.
"""
import json
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

# --- Weather (Open-Meteo: free, no API key needed) ---
# Set your location via env vars; defaults below can be edited.
WEATHER_LAT = os.environ.get("WEATHER_LAT", "40.7128")   # default: NYC
WEATHER_LON = os.environ.get("WEATHER_LON", "-74.0060")
WEATHER_UNITS = os.environ.get("WEATHER_UNITS", "fahrenheit")  # or "celsius"
_weather = {"text": None}  # updated by a background thread
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


def weather_thread():
    """Fetch weather every 15 minutes in the background so a slow or missing
    network never blocks the display."""
    unit = "fahrenheit" if WEATHER_UNITS.startswith("f") else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
        "&current=temperature_2m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min"
        f"&temperature_unit={unit}&timezone=auto&forecast_days=1"
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
            with _weather_lock:
                _weather["text"] = text
        except Exception:
            pass  # keep last value; try again next cycle
        time.sleep(15 * 60)


def get_weather_text():
    with _weather_lock:
        return _weather["text"]

# --- Appearance ---
BG_COLOR = (26, 26, 46)        # dark navy
PANEL_COLOR = (22, 33, 62)     # slightly lighter panel
HEADER_COLOR = (0, 212, 255)   # cyan
TEXT_COLOR = (234, 234, 234)   # off-white
DONE_COLOR = (120, 120, 130)   # grey for completed
CLOCK_COLOR = (110, 110, 120)

REFRESH_SECONDS = 5            # how often to re-read the database

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


def read_tracker():
    """Read the time-tracker log + state. Returns a dict with recent check-ins
    and seconds until the next check-in, or None if the tracker isn't set up."""
    if not os.path.exists(TRACKER_LOG) and not os.path.exists(TRACKER_STATE):
        return None
    recent = []
    try:
        with open(TRACKER_LOG) as f:
            entries = json.load(f)
        recent = entries[-8:]
    except (OSError, json.JSONDecodeError):
        pass

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

    return {"recent": recent, "next_in": next_in, "is_awake": is_awake}


def last_update_str():
    """Return a short 'updated' timestamp based on when git last fetched/pulled.
    Uses .git/FETCH_HEAD mtime (touched on every fetch/pull); falls back to the
    HEAD commit file mtime. Returns '' if unavailable."""
    candidates = [
        os.path.join(BASE_DIR, ".git", "FETCH_HEAD"),
        os.path.join(BASE_DIR, ".git", "HEAD"),
    ]
    for path in candidates:
        try:
            mtime = os.path.getmtime(path)
            return "updated " + datetime.fromtimestamp(mtime).strftime("%b %d %I:%M %p")
        except OSError:
            continue
    return ""


def draw_panel(screen, fonts, rect, title, items):
    """Draw one list panel (title + items) inside the given rect."""
    x, y, w, h = rect
    panel_rect = pygame.Rect(x, y, w, h)
    pygame.draw.rect(screen, PANEL_COLOR, panel_rect, border_radius=16)

    pad = 24
    # Title
    title_surf = fonts["title"].render(title, True, HEADER_COLOR)
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


def draw_tracker(screen, fonts, rect, tracker):
    """Draw the time-tracker band: next check-in countdown + recent check-ins."""
    x, y, w, h = rect
    pygame.draw.rect(screen, PANEL_COLOR, pygame.Rect(x, y, w, h), border_radius=16)
    pad = 20
    item_font = fonts["item"]
    small_font = fonts["clock"]

    # Header row: "Time Tracker" + countdown on the right.
    title_surf = fonts["clock"].render("Time Tracker", True, HEADER_COLOR)
    screen.blit(title_surf, (x + pad, y + pad))

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

    # Recent check-ins below the header.
    line_y = y + pad + title_surf.get_height() + 12
    line_h = item_font.get_height() + 8
    bottom = y + h - pad
    recent = tracker.get("recent") or []
    if not recent:
        empty = small_font.render("No check-ins yet", True, DONE_COLOR)
        screen.blit(empty, (x + pad, line_y))
        return

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
        # Truncate to fit on one line.
        max_w = w - 2 * pad - time_w
        rendered = item_font.render(text, True, TEXT_COLOR)
        if rendered.get_width() > max_w:
            while rendered.get_width() > max_w and len(text) > 3:
                text = text[:-2]
                rendered = item_font.render(text + "\u2026", True, TEXT_COLOR)
        screen.blit(rendered, (x + pad + time_w, line_y))
        line_y += line_h


def main():
    pygame.init()
    pygame.mouse.set_visible(False)

    # Start fetching weather in the background (non-blocking).
    threading.Thread(target=weather_thread, daemon=True).start()

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
    }

    clock = pygame.time.Clock()
    last_refresh = 0.0
    last_tick = time.time()
    todo_items = []
    shopping_items = []
    tracker_data = None
    update_str = last_update_str()

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
            update_str = last_update_str()
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
        clock_h = fonts["clock"].get_height() + 20

        # Weather bar across the top.
        weather_text = get_weather_text()
        weather_bar_h = fonts["clock"].get_height() + 20
        wbar = pygame.Rect(margin, margin, sw - 2 * margin, weather_bar_h)
        pygame.draw.rect(canvas, PANEL_COLOR, wbar, border_radius=12)
        w_surf = fonts["clock"].render(
            weather_text if weather_text else "Weather unavailable",
            True, TEXT_COLOR if weather_text else DONE_COLOR)
        canvas.blit(w_surf, (margin + (wbar.width - w_surf.get_width()) // 2,
                             margin + (weather_bar_h - w_surf.get_height()) // 2))

        # Everything below the weather bar starts here.
        top = margin + weather_bar_h + gap

        # Reserve a band for the time tracker (only if it's set up). Its height
        # scales with the screen and fits the header + recent check-ins.
        # (~75% taller than the original so more check-ins are visible.)
        tracker_h = 0
        if tracker_data is not None:
            tracker_h = int(((fonts["item"].get_height() + 8) * 4
                             + fonts["clock"].get_height() + 44) * 1.75)

        # Side-by-side full-height columns: Todo left, Shopping right. Their
        # height shrinks to leave room for the weather bar, tracker band + clock.
        panel_w = (sw - 2 * margin - gap) // 2
        panel_h = sh - top - margin - clock_h - tracker_h - (gap if tracker_h else 0)
        draw_panel(canvas, fonts,
                   (margin, top, panel_w, panel_h),
                   "Todo List", todo_items)
        draw_panel(canvas, fonts,
                   (margin + panel_w + gap, top, panel_w, panel_h),
                   "Shopping List", shopping_items)

        # Time-tracker band, between the lists and the clock.
        if tracker_data is not None:
            tracker_y = top + panel_h + gap
            draw_tracker(canvas, fonts,
                         (margin, tracker_y, sw - 2 * margin, tracker_h),
                         tracker_data)

        # Clock / date at the bottom (centered)
        stamp = time.strftime("%A, %B %d   -   %I:%M %p")
        clock_surf = fonts["clock"].render(stamp, True, CLOCK_COLOR)
        clock_x = (sw - clock_surf.get_width()) // 2
        canvas.blit(clock_surf, (clock_x, sh - clock_h + 4))

        # "Last updated" timestamp in the bottom-right corner (cached on refresh).
        if update_str:
            upd_surf = fonts["tiny"].render(update_str, True, DONE_COLOR)
            canvas.blit(upd_surf, (sw - margin - upd_surf.get_width(),
                                   sh - clock_h + 6))

        # Rotate the canvas onto the physical screen if needed.
        if canvas is not screen:
            rotated = pygame.transform.rotate(canvas, ROTATE)
            screen.blit(rotated, (0, 0))

        pygame.display.flip()
        clock.tick(10)  # 10 FPS is plenty; keeps CPU/RAM usage low

    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()
