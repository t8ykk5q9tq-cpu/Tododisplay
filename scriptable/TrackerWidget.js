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
  w.addSpacer(8);

  if (status === null) {
    const err = w.addText("Can't reach Pi");
    err.font = Font.systemFont(12);
    err.textColor = MUTED;
    const hint = w.addText("Is Tailscale on?");
    hint.font = Font.systemFont(10);
    hint.textColor = MUTED;
    return w;
  }

  const fmtTime = (ts) =>
    new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  const all = status.recent || [];
  const last = all.length ? all[all.length - 1] : null;

  // Determine the big "last check-in" display.
  let bigText, labelText, bigColor;
  if (status.notification_pending) {
    bigText = "Check in!";
    labelText = "tap to log";
    bigColor = ALERT;
  } else if (last) {
    bigText = fmtTime(last.timestamp);
    labelText = status.is_awake ? "last check-in" : "last (sleeping)";
    bigColor = status.is_awake ? WHITE : MUTED;
  } else {
    bigText = "--:--";
    labelText = status.is_awake ? "no check-ins yet" : "sleeping";
    bigColor = MUTED;
  }

  // --- Two-column body: left = last check-in, right = past check-ins ---
  const body = w.addStack();
  body.topAlignContent();

  // LEFT column
  const left = body.addStack();
  left.layoutVertically();

  const cd = left.addText(bigText);
  cd.font = Font.boldSystemFont(size === "small" ? 24 : 32);
  cd.textColor = bigColor;

  const label = left.addText(labelText);
  label.font = Font.systemFont(11);
  label.textColor = MUTED;

  if (last && !status.notification_pending) {
    left.addSpacer(4);
    const lastText = left.addText(last.text);
    lastText.font = Font.mediumSystemFont(size === "small" ? 12 : 13);
    lastText.textColor = ACCENT;
    lastText.lineLimit = 2;
  }

  // RIGHT column (medium/large only — small has no room)
  if (size !== "small") {
    body.addSpacer(14);
    const right = body.addStack();
    right.layoutVertically();

    const rTitle = right.addText("Recent");
    rTitle.font = Font.systemFont(9);
    rTitle.textColor = MUTED;
    right.addSpacer(4);

    // Past check-ins, newest first, excluding the one shown on the left.
    const earlier = all.slice(0, -1).reverse();
    const maxRows = size === "large" ? 6 : 4;
    if (earlier.length === 0) {
      const none = right.addText("—");
      none.font = Font.systemFont(11);
      none.textColor = MUTED;
    } else {
      for (const e of earlier.slice(0, maxRows)) {
        const t = right.addText(`${fmtTime(e.timestamp)}  ${e.text}`);
        t.font = Font.systemFont(11);
        t.textColor = WHITE;
        t.lineLimit = 1;
        right.addSpacer(3);
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
