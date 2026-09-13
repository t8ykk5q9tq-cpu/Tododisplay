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

const PI_HOST = "100.102.96.42:5050"; // Pi's Tailscale address + tracker port
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

function bgGradient() {
  const g = new LinearGradient();
  g.colors = [new Color("#1e2340"), new Color("#161a2e")];
  g.locations = [0, 1];
  return g;
}

async function buildWidget() {
  const size = config.widgetFamily || "medium";
  const w = new ListWidget();
  w.backgroundGradient = bgGradient();
  w.setPadding(14, 16, 14, 16);
  w.url = BASE_URL; // tap to open the check-in page

  const status = await fetchStatus();

  // Header row: title left, awake/asleep chip right.
  const head = w.addStack();
  head.centerAlignContent();
  const header = head.addText("Time Tracker");
  header.font = Font.boldSystemFont(14);
  header.textColor = ACCENT;
  head.addSpacer();
  if (status) {
    const chip = head.addText(status.is_awake ? "awake" : "sleeping");
    chip.font = Font.systemFont(10);
    chip.textColor = status.is_awake ? MUTED : new Color("#6a7290");
  }
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

  // --- Top status band: big time on the left, current activity on the right,
  // using the full width instead of a narrow left column. ---
  const band = w.addStack();
  band.centerAlignContent();

  const cd = band.addText(bigText);
  cd.font = Font.boldSystemFont(size === "small" ? 24 : 30);
  cd.textColor = bigColor;

  band.addSpacer(12);

  // Right side of the band: label on top, current activity below it.
  const bandR = band.addStack();
  bandR.layoutVertically();
  const label = bandR.addText(labelText.toUpperCase());
  label.font = Font.semiboldSystemFont(9);
  label.textColor = MUTED;
  if (last && !status.notification_pending) {
    bandR.addSpacer(2);
    const lastText = bandR.addText(last.text);
    lastText.font = Font.mediumSystemFont(size === "small" ? 13 : 15);
    lastText.textColor = ACCENT;
    lastText.lineLimit = 2;
  }
  band.addSpacer();

  // Small widget stops here (no room for the recent list).
  if (size === "small") return w;

  // Full-width divider (spacer forces the stack to stretch across the widget).
  w.addSpacer(10);
  const div = w.addStack();
  div.backgroundColor = new Color("#2a2f4a");
  div.addSpacer();          // stretches width
  div.setPadding(0.5, 0, 0.5, 0);  // ~1pt tall
  w.addSpacer(8);

  // --- Recent list, full width: time left (accent), text flowing, newest
  // first, excluding the one shown in the band above. ---
  const earlier = all.slice(0, -1).reverse();
  const maxRows = size === "large" ? 8 : 4;
  if (earlier.length === 0) {
    const none = w.addText("No earlier check-ins today");
    none.font = Font.systemFont(11);
    none.textColor = MUTED;
  } else {
    const rows = earlier.slice(0, maxRows);
    rows.forEach((e, i) => {
      const r = w.addStack();
      r.centerAlignContent();
      const tm = r.addText(fmtTime(e.timestamp));
      tm.font = Font.regularMonospacedSystemFont(11);
      tm.textColor = ACCENT;
      r.addSpacer(10);
      const tx = r.addText(e.text);
      tx.font = Font.systemFont(12);
      tx.textColor = WHITE;
      tx.lineLimit = 1;
      r.addSpacer();
      if (i < rows.length - 1) w.addSpacer(size === "large" ? 5 : 6);
    });
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
