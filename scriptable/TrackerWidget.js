// TrackerWidget.js — Scriptable (iOS) home-screen widget for the Time Tracker.
// Shows the next check-in countdown and your recent check-ins, over Tailscale.
//
// SETUP:
//   1. PI_HOST is set to your Pi's Tailscale address + tracker port (5050).
//   2. In Scriptable, create a new script, paste this in, name it "Tracker Widget".
//   3. Add a Scriptable widget to your home screen (small or medium),
//      long-press it -> "Edit Widget" -> select this script.
//
// Tapping the widget opens the check-in page so you can log an entry.

const PI_HOST = "100.67.122.101:5050"; // Pi's Tailscale address + tracker port
const BASE_URL = `http://${PI_HOST}`;

// --- Colors (match the app theme) ---
const BG = new Color("#1a1a2e");
const ACCENT = new Color("#00d4ff");
const WHITE = new Color("#eaeaea");
const MUTED = new Color("#8a8a9a");
const ALERT = new Color("#e94560");

async function fetchStatus() {
  try {
    const req = new Request(`${BASE_URL}/status`);
    req.timeoutInterval = 8;
    return await req.loadJSON();
  } catch (e) {
    return null; // couldn't reach the Pi
  }
}

function fmtCountdown(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

async function buildWidget() {
  const size = config.widgetFamily || "medium";
  const w = new ListWidget();
  w.backgroundColor = BG;
  w.setPadding(14, 16, 14, 16);
  w.url = BASE_URL; // tap to open the check-in page

  const status = await fetchStatus();

  // Header
  const header = w.addText("Time Tracker");
  header.font = Font.boldSystemFont(13);
  header.textColor = ACCENT;
  w.addSpacer(6);

  if (status === null) {
    const err = w.addText("Can't reach Pi");
    err.font = Font.systemFont(12);
    err.textColor = MUTED;
    const hint = w.addText("Is Tailscale on?");
    hint.font = Font.systemFont(10);
    hint.textColor = MUTED;
    return w;
  }

  // Countdown / status line
  const cd = w.addText(
    !status.is_awake ? "Sleeping"
      : status.notification_pending ? "Check in!"
      : fmtCountdown(Math.max(0, status.next_checkin_in))
  );
  cd.font = Font.boldSystemFont(size === "small" ? 26 : 34);
  cd.textColor = status.notification_pending ? ALERT : WHITE;

  const label = w.addText(
    !status.is_awake ? "reminders paused"
      : status.notification_pending ? "tap to log"
      : "until next check-in"
  );
  label.font = Font.systemFont(11);
  label.textColor = MUTED;

  // Recent check-ins (medium/large only — small has no room)
  if (size !== "small") {
    w.addSpacer(8);
    const recent = (status.recent || []).slice().reverse(); // newest first
    const maxRows = size === "large" ? 6 : 3;
    if (recent.length === 0) {
      const none = w.addText("No check-ins yet");
      none.font = Font.systemFont(11);
      none.textColor = MUTED;
    } else {
      for (const e of recent.slice(0, maxRows)) {
        const row = w.addStack();
        row.spacing = 6;
        const d = new Date(e.timestamp);
        const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        const tEl = row.addText(t);
        tEl.font = Font.systemFont(11);
        tEl.textColor = ACCENT;
        const xEl = row.addText(e.text);
        xEl.font = Font.systemFont(11);
        xEl.textColor = WHITE;
        xEl.lineLimit = 1;
        w.addSpacer(2);
      }
    }
  }

  return w;
}

const widget = await buildWidget();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
