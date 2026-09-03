# Todo Display

A fullscreen display board for a Raspberry Pi that shows a **todo list** and **shopping list** side by side on a connected monitor. Designed to run as a kiosk — always on, always visible.

![Dark themed two-panel layout with clock]

## Features

- Two-panel layout: Todo list + Shopping list
- Add, complete (tap to toggle), and delete items
- Dark theme optimized for always-on displays
- Date and time display
- Auto-refreshes every 30 seconds
- Launches Chromium in kiosk mode (fullscreen, no browser chrome)
- Systemd service for auto-start on boot
- SQLite storage — no external database needed

## Requirements

- Raspberry Pi (any model with a desktop environment)
- Raspberry Pi OS with desktop (Bookworm or Bullseye)
- Python 3.9+
- Chromium browser (pre-installed on Pi OS)
- A connected monitor/TV

## Quick Start

### 1. Copy the project to your Pi

```bash
# From your development machine:
scp -r Tododisplay/ pi@raspberrypi.local:~/Tododisplay/

# Or clone from your repo:
git clone <your-repo-url> ~/Tododisplay
```

### 2. Run it

```bash
cd ~/Tododisplay
./start.sh
```

This will:
- Create a Python virtual environment
- Install Flask
- Start the web server on port 5000
- Open Chromium in fullscreen kiosk mode

### 3. Stop it

```bash
./stop.sh
```

## Auto-Start on Boot

To have the display launch automatically when the Pi boots:

```bash
# Copy the service file
sudo cp tododisplay.service /etc/systemd/system/

# Enable and start
sudo systemctl enable tododisplay
sudo systemctl start tododisplay
```

**Note:** The service file assumes the project lives at `/home/pi/Tododisplay` and runs as user `pi`. Edit `tododisplay.service` if your setup differs.

## Managing Items from Another Device

Since the server runs on port 5000, you can access it from any device on the same network:

```
http://raspberrypi.local:5000
```

Or use the Pi's IP address:

```
http://192.168.x.x:5000
```

This lets you add/remove items from your phone or laptop while the Pi displays them on the monitor.

## Project Structure

```
Tododisplay/
├── app.py                 # Flask server + REST API
├── requirements.txt       # Python dependencies
├── start.sh               # Launch script (server + kiosk browser)
├── stop.sh                # Stop script
├── tododisplay.service    # Systemd unit for auto-start
├── lists.db               # SQLite database (created on first run)
└── static/
    ├── index.html         # Frontend page
    ├── style.css          # Dark theme styles
    └── app.js             # Client-side logic
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/items/todo` | Get all todo items |
| GET | `/api/items/shopping` | Get all shopping items |
| POST | `/api/items/todo` | Add a todo item (`{"text": "..."}`) |
| POST | `/api/items/shopping` | Add a shopping item (`{"text": "..."}`) |
| PATCH | `/api/items/:id/toggle` | Toggle done state |
| DELETE | `/api/items/:id` | Delete an item |
| DELETE | `/api/items/todo/clear-done` | Remove completed todos |
| DELETE | `/api/items/shopping/clear-done` | Remove completed shopping items |

## Tips

- **Hide the cursor:** Install `unclutter` to hide the mouse pointer after inactivity:
  ```bash
  sudo apt install unclutter
  ```
  Add `@unclutter -idle 3` to `~/.config/lxsession/LXDE-pi/autostart`.

- **Disable screen blanking:** Prevent the monitor from sleeping:
  ```bash
  sudo raspi-config
  # Navigate to: Display Options > Screen Blanking > Off
  ```

- **Rotate display:** If your monitor is mounted vertically, add to `/boot/config.txt`:
  ```
  display_rotate=1
  ```
