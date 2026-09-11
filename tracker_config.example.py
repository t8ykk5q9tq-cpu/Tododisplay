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

# --- Habit reminder ---
# Hour (0-23) to send an evening Pushover about habits not yet checked today.
# Set to -1 to disable. Example: 20 = 8pm.
HABIT_REMINDER_HOUR = 20

# --- Check-in categories (quick-pick buttons + tags + daily summary) ---
CATEGORIES = ["Work", "Break", "Meal", "Errands", "Health", "Personal",
              "TikTok", "YouTube"]
# These show as a count ("TikTok x9") instead of estimated time in the summary
# (good for app-opens, which don't imply a full interval of activity).
COUNT_ONLY_CATEGORIES = ["TikTok", "YouTube"]

# Daily per-app screen-time limit in minutes. When an app's total for the day
# crosses this, you get one Pushover alert. Set to 0 to disable.
APP_TIME_LIMIT_MIN = 60

# --- Focus vs distraction (for the productive/distracting ratio) ---
# By default Mac apps (prefix "Mac:") count as focus and TikTok/YouTube as
# distraction. Add exact app names here to override/extend.
FOCUS_APPS = []                       # e.g. ["Mac:Kiro", "Mac:Terminal"]
DISTRACTION_APPS = ["TikTok", "YouTube"]

# --- End-of-day summary push ---
# Hour (0-23) to send a Pushover recap of the day. Set to -1 to disable.
DAILY_SUMMARY_HOUR = 21

# --- Weekly review push (7-day recap) ---
# WEEKLY_REVIEW_DOW: weekday to send (0=Mon .. 6=Sun). WEEKLY_REVIEW_HOUR: hour.
# Set WEEKLY_REVIEW_HOUR = -1 to disable.
WEEKLY_REVIEW_DOW = 6          # Sunday
WEEKLY_REVIEW_HOUR = 19        # 7pm

# --- Nightly backups (of JSON logs + lists.db into backups/) ---
# Runs once a day at BACKUP_HOUR and keeps the last BACKUP_KEEP snapshots.
# Set BACKUP_HOUR = -1 to disable.
BACKUP_HOUR = 3                # 3am
BACKUP_KEEP = 14               # keep two weeks of daily snapshots

# --- Bedtime wind-down nudge ---
# When it's within this many minutes before your average bedtime (learned from
# logged sleep) and you're in a distraction app, get one "wind down" Pushover
# per night. Set to 0 to disable. Needs at least 3 logged nights to activate.
WINDDOWN_WINDOW_MIN = 30

# --- Check-in compliance (nags you when you skip check-ins) ---
COMPLIANCE_START_HOUR = 8      # start expecting check-ins at this hour
COMPLIANCE_END_HOUR = 22       # stop expecting after this hour
COMPLIANCE_BEHIND_BELOW = 0.7  # "behind" when you've done < 70% of expected

# --- "Close the app" nudge (anti-doomscroll) ---
# If a tracked app stays open longer than APP_OPEN_NUDGE_MIN in one sitting,
# get a Pushover telling you to close it, repeating every APP_OPEN_RENUDGE_MIN.
APP_OPEN_NUDGE_MIN = 5         # minutes before the first "close it" nudge
APP_OPEN_RENUDGE_MIN = 5       # keep nudging this often while still open
NUDGE_APPS = ["TikTok", "YouTube"]

# --- Health: hydration + a daily numeric metric ---
WATER_GOAL = 8                 # glasses of water per day (progress bar goal)
METRIC_LABEL = "Weight"        # label for the daily number you log
METRIC_UNIT = "lb"             # unit shown next to it (e.g. "lb", "kg")

# --- Pi tracker URL (so Pushover reminders can deep-link to logging) ---
# Set to the tracker's reachable address, e.g. your Tailscale URL:
#   PI_BASE_URL = "http://100.102.96.42:5050"
# Leave blank to omit the link.
PI_BASE_URL = ""
