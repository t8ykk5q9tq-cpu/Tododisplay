// TrackerWidget.js — Scriptable (iOS) home-screen widget for the Time Tracker.
// Shows the time of your last check-in and recent check-ins, over Tailscale.
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

  // Time of the last check-in (more useful than a countdown that goes stale).
  const all = status.recent || [];
  const last = all.length ? all[all.length - 1] : null;

  let bigText, labelText, bigColor;
  if (status.notification_pending) {
    bigText = "Check in!";
    labelText = "tap to log";
    bigColor = ALERT;
  } else if (!status.is_awake) {
    bigText = last
      ? new Date(last.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "Sleeping";
    labelText = "sleeping - reminders paused";
    bigColor = MUTED;
  } else if (last) {
    bigText = new Date(last.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    labelText = "last check-in";
    bigColor = WHITE;
  } else {
    bigText = "--:--";
    labelText = "no check-ins yet";
    bigColor = MUTED;
  }

  const cd = w.addText(bigText);
  cd.font = Font.boldSystemFont(size === "small" ? 26 : 34);
  cd.textColor = bigColor;

  const label = w.addText(labelText);
  label.font = Font.systemFont(11);
  label.textColor = MUTED;

  // Show what the last check-in was (small widget has no room for the list below).
  if (last && !status.notification_pending) {
    const lastText = w.addText(last.text);
    lastText.font = Font.systemFont(size === "small" ? 11 : 12);
    lastText.textColor = ACCENT;
    lastText.lineLimit = size === "small" ? 2 : 1;
  }

  // Earlier check-ins (medium/large only). Skip the most recent one since it's
  // already shown as the "last check-in" above.
  if (size !== "small") {
    w.addSpacer(8);
    const earlier = (status.recent || []).slice(0, -1).reverse(); // newest first, excl. last
    const maxRows = size === "large" ? 5 : 2;
    const title = w.addText("Earlier");
    title.font = Font.systemFont(9);
    title.textColor = MUTED;
    w.addSpacer(3);
    if (earlier.length === 0) {
      const none = w.addText("—");
      none.font = Font.systemFont(11);
      none.textColor = MUTED;
    } else {
      for (const e of earlier.slice(0, maxRows)) {
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
