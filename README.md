# Todo Display

A self-contained information board for a Raspberry Pi, shown fullscreen on a
connected monitor (portrait). It shows a **todo list**, **shopping list**, a
**daily habit tracker** with streaks, a **time tracker**, **weather**, and a
**daily Stoic quote** — all in a dark theme. You interact with it from any
device (phone/laptop) over your network or Tailscale.

Built for a low-RAM board (Pi Zero 2 W): it draws directly to the screen with
pygame — no browser, no desktop chrome needed. It self-updates from GitHub,
recovers dropped Wi-Fi, and restarts any service that crashes.

## Features

- **Todo + Shopping lists** — add / complete (tap to toggle, strike-through) / delete
- **Habit tracker** — daily habits with a GitHub-style contribution heatmap and streak counts; auto-resets each day
- **Time tracker** — 30-min check-in reminders via Pushover; recent check-ins + next-check-in countdown on screen
- **Weather bar** — current temp/conditions, high/low, sunrise/sunset, and a precip alert when rain/snow is likely
- **Daily Stoic quote** — one per day, changes at midnight (offline, built-in)
- **Clock + "last updated"** timestamp (from the last code change)
- **Portrait layout** via screen rotation
- **Self-updating** — pulls new code from GitHub every 10 minutes (only restarts when files that run on the Pi change)
- **Wi-Fi watchdog** — reconnects automatically if the network drops
- **Service supervisor** — relaunches the server / tracker / display if any crashes
- **Under-voltage warning** — flags low power on screen
- SQLite + JSON storage — no external database

## Requirements

- Raspberry Pi (developed on a Pi Zero 2 W) running Raspberry Pi OS with desktop
- `python3`, `python3-flask`, `python3-pygame` (installed via apt — lighter than pip on a Zero 2 W)
- A connected monitor (mounted portrait)

## Quick Start

### 1. Get the project onto the Pi

```bash
git clone https://github.com/t8ykk5q9tq-cpu/Tododisplay.git ~/Tododisplay
```

### 2. Install dependencies (once)

```bash
sudo apt update
sudo apt install -y python3-flask python3-pygame
```

### 3. Run it

Launch detached so it survives closing SSH (and Ctrl+C):

```bash
cd ~/Tododisplay
DISPLAY=:0 ROTATE=90 nohup bash start-lite.sh > run.log 2>&1 &
```

`ROTATE=90` gives the portrait layout (use `270` if the monitor is turned the
other way). Stop everything with `bash stop.sh`.

## Auto-Start on Boot

So the board comes up on its own after a reboot/power blip (and isn't tied to
your SSH session):

```bash
mkdir -p ~/.config/autostart
cp ~/Tododisplay/tododisplay.desktop ~/.config/autostart/
```

The `.desktop` entry runs `ROTATE=90 bash start-lite.sh` on desktop login.

## Managing It From Your Phone / Laptop

Lists + habits:  `http://<pi>:5000`
Time tracker:    `http://<pi>:5050`

On the same Wi-Fi use the Pi's LAN IP (or `raspberrypi.local`). From anywhere,
use **Tailscale** — install it on the Pi and your phone, then use the Pi's
Tailscale address (e.g. `http://100.67.122.101:5000`). Add either page to your
phone's home screen for one-tap access. There are also Scriptable widgets and
Shortcuts in the `scriptable/` folder.

## Configuration (environment variables)

Set these on the launch line, e.g. `ROTATE=90 ITEM_PT=34 bash start-lite.sh`:

| Var | Default | Purpose |
|-----|---------|---------|
| `ROTATE` | 0 | Screen rotation: 0/90/180/270 (90 = portrait) |
| `TITLE_PT` / `ITEM_PT` / `CLOCK_PT` | auto | Font sizes |
| `WEATHER_LAT` / `WEATHER_LON` | Minneapolis | Weather location |
| `WEATHER_UNITS` | fahrenheit | `fahrenheit` or `celsius` |
| `WEATHER_TEST_PRECIP` | (unset) | Force a precip alert for testing |
| `AUTO_UPDATE` | 1 | Auto-pull from GitHub every 10 min |
| `UPDATE_INTERVAL` | 600 | Seconds between update checks |
| `WIFI_WATCHDOG` | 1 | Auto-reconnect Wi-Fi if it drops |
| `WATCHDOG_INTERVAL` | 120 | Seconds between connectivity checks |
| `WIFI_CONN` | netplan-wlan0-S232 Home Wifi | NetworkManager connection name |
| `AUTO_RESTART` | 1 | Relaunch crashed services |

## Project Structure

```
Tododisplay/
├── app.py                    # Flask server: lists + habits API (port 5000)
├── tracker.py                # Flask time-tracker service (port 5050)
├── display.py                # pygame fullscreen display (the board itself)
├── start-lite.sh             # Launches server + tracker + display; update loop,
│                             #   Wi-Fi watchdog, and service supervisor
├── stop.sh                   # Stops everything
├── update.sh                 # (Standalone) pull + restart if changed
├── tododisplay.desktop       # Boot autostart entry
├── tracker_config.example.py # Copy to tracker_config.py and fill in secrets
├── templates/tracker.html    # Tracker check-in page
├── static/                   # Lists + habits web page (index.html, app.js, style.css)
├── scriptable/               # iOS Scriptable widgets + Shortcuts docs + icon
├── lists.db                  # Lists + habits (gitignored)
└── tracker_log.json/.json    # Tracker data (gitignored)
```

## Time Tracker Setup

Copy the example config and fill in your details (kept out of git):

```bash
cp tracker_config.example.py tracker_config.py
nano tracker_config.py
```

- `PUSHOVER_USER` / `PUSHOVER_TOKEN` — phone push reminders (blank = disabled)
- `GMAIL_FROM` / `GMAIL_PASS` / `RECIPIENT` — optional daily email (Gmail App Password)
- `CHECKIN_INTERVAL_MIN` — minutes between check-ins (default 30)
- `HABIT_REMINDER_HOUR` — evening reminder about unchecked habits (default 20 / 8pm; -1 to disable)

**`tracker_config.py` is gitignored** — this repo is public, so never put keys elsewhere.

## API

Lists (port 5000):

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/items/<todo\|shopping>` | List items |
| POST | `/api/items/<todo\|shopping>` | Add item `{"text": "..."}` |
| PATCH | `/api/items/:id/toggle` | Toggle done |
| DELETE | `/api/items/:id` | Delete item |
| DELETE | `/api/items/<type>/clear-done` | Clear completed |
| GET | `/api/habits` | Habits with done/streak/history |
| POST | `/api/habits` | Add habit `{"name": "..."}` |
| PATCH | `/api/habits/:id/toggle` | Toggle today's completion |
| DELETE | `/api/habits/:id` | Delete habit |

Tracker (port 5050):

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Countdown, awake state, recent check-ins |
| POST | `/log` | Log a check-in `{"text": "..."}` |
| POST | `/wake` / `/sleep` | Resume / pause reminders |
| POST | `/trigger` | Test check-in notification |
| GET/POST | `/habit-reminder-test` | Fire the habit reminder now (testing) |

## Troubleshooting

- **Wi-Fi keeps dropping / asks for password again:** disable Wi-Fi power saving
  (the watchdog recovers drops, but this prevents them):
  ```bash
  echo -e "[connection]\nwifi.powersave = 2" | sudo tee /etc/NetworkManager/conf.d/wifi-powersave.conf
  sudo systemctl restart NetworkManager
  iw dev wlan0 get power_save   # should say: off
  ```
- **Weather says "unavailable":** the Pi can't reach the internet — check Wi-Fi.
  It retries every 60s once back online.
- **Screen blanks after a while:** disable blanking via `sudo raspi-config`
  (Display Options → Screen Blanking → Off).
- **Under-voltage warning on screen:** the Pi needs a stronger 5V/2.5A supply.
- **Check logs:** `update.log` (updates), `watchdog.log` (Wi-Fi + crashes), `run.log` (launch).

## Updating

Edit code on your dev machine and `git push`. The Pi pulls it within 10 minutes
and restarts only if a file that runs on the Pi changed. To force an update now:

```bash
cd ~/Tododisplay
git reset --hard && git pull
bash stop.sh
DISPLAY=:0 ROTATE=90 nohup bash start-lite.sh > run.log 2>&1 &
```
