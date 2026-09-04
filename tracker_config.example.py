# tracker_config.example.py
#
# Copy this file to tracker_config.py and fill in your real values.
# tracker_config.py is gitignored so your secrets never get committed
# (this repo is PUBLIC on GitHub).
#
#   cp tracker_config.example.py tracker_config.py
#   then edit tracker_config.py

# --- Pushover (phone push notifications for check-in reminders) ---
# Leave blank to disable push notifications.
PUSHOVER_USER = ""
PUSHOVER_TOKEN = ""

# --- Daily email summary (optional) ---
# For Gmail, use an App Password (not your normal password).
# Leave GMAIL_FROM blank to disable the daily email.
GMAIL_FROM = ""
GMAIL_PASS = ""
RECIPIENT = ""

# --- Timing ---
CHECKIN_INTERVAL_MIN = 30   # minutes between check-in reminders
FOLLOWUP_MIN = 5            # follow-up reminder if you haven't responded
