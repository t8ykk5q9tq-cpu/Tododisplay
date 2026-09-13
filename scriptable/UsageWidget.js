// UsageWidget.js — Scriptable (iOS) home-screen widget for Screen Usage.
// Shows today's phone (distraction-app) minutes, a mini 24-hour ribbon
// colored by app, and the top apps. Pulls data from the Pi over Tailscale.
//
// SETUP:
//   1. PI_HOST is set to your Pi's Tailscale address + tracker port (5050).
//   2. In Scriptable, use the UsageLoader script (auto-updates from GitHub),
//      or paste this in directly, name it "Usage Widget".
//   3. Add a Scriptable widget to your home screen (small or medium),
//      long-press -> "Edit Widget" -> select the script.
//
// Tapping the widget opens the full /usage page.

const PI_HOST = "100.102.96.42:5050"; // Pi's Tailscale address + tracker port
const BASE_URL = `http://${PI_HOST}`;

// --- Colors (match the app theme) ---
const BG = new Color("#1a1a2e");
const PANEL = new Color("#243056");
const ACCENT = new Color("#00d4ff");
const WHITE = new Color("#eaeaea");
const MUTED = new Color("#8a8a9a");
const ALERT = new Color("#e94560");

async function fetchUsage() {
  try {
    const req = new Request(`${BASE_URL}/usage-data`);
    req.timeoutInterval = 8;
    return await req.loadJSON();
  } catch (e) {
    return null; // couldn't reach the Pi
  }
}

// Draw a mini 24-hour ribbon (1 px per ~few minutes) colored by app.
function drawRibbon(minutes, colors, defaultColor, width, height) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;

  // idle base
  ctx.setFillColor(PANEL);
  ctx.fillRect(new Rect(0, 0, width, height));

  const perMin = width / 1440; // px per minute of day
  for (const key of Object.keys(minutes)) {
    const md = parseInt(key, 10);
    if (md < 0 || md >= 1440) continue;
    const app = minutes[key];
    const hex = (app && colors[app]) ? colors[app] : defaultColor;
    ctx.setFillColor(new Color(hex));
    const x = md * perMin;
    ctx.fillRect(new Rect(x, 0, Math.max(1, perMin), height));
  }
  return ctx.getImage();
}

async function buildWidget() {
  const size = config.widgetFamily || "medium";
  const w = new ListWidget();
  w.backgroundColor = BG;
  w.setPadding(14, 16, 14, 16);
  w.url = `${BASE_URL}/usage`; // tap to open the usage page

  const header = w.addText("Screen Usage");
  header.font = Font.boldSystemFont(13);
  header.textColor = ACCENT;
  w.addSpacer(6);

  const d = await fetchUsage();
  if (d === null) {
    const err = w.addText("Can't reach Pi");
    err.font = Font.systemFont(12);
    err.textColor = MUTED;
    const hint = w.addText("Is Tailscale on?");
    hint.font = Font.systemFont(10);
    hint.textColor = MUTED;
    return w;
  }

  const total = d.total_minutes || 0;
  const perApp = d.per_app || {};
  const colors = d.colors || {};
  const defaultColor = d.default_color || "#9b6dff";

  // Big number: today's phone minutes.
  const bigRow = w.addStack();
  bigRow.centerAlignContent();
  const big = bigRow.addText(String(total));
  big.font = Font.boldSystemFont(size === "small" ? 30 : 36);
  big.textColor = total > 0 ? ALERT : MUTED;
  bigRow.addSpacer(6);
  const unit = bigRow.addText("min on phone");
  unit.font = Font.systemFont(12);
  unit.textColor = MUTED;

  w.addSpacer(8);

  // Mini ribbon.
  const ribbonW = size === "small" ? 130 : 300;
  const ribbonH = 16;
  const img = drawRibbon(d.minutes || {}, colors, defaultColor, ribbonW, ribbonH);
  const imgStack = w.addStack();
  const wimg = imgStack.addImage(img);
  wimg.imageSize = new Size(ribbonW, ribbonH);
  wimg.cornerRadius = 4;

  // Top apps (medium/large only — small has no room).
  if (size !== "small") {
    w.addSpacer(8);
    const apps = Object.keys(perApp).sort((a, b) => perApp[b] - perApp[a]);
    const maxRows = size === "large" ? 5 : 3;
    if (apps.length === 0) {
      const none = w.addText("No phone use logged today");
      none.font = Font.systemFont(11);
      none.textColor = MUTED;
    } else {
      for (const a of apps.slice(0, maxRows)) {
        const row = w.addStack();
        row.centerAlignContent();
        // color dot
        const dotHex = (a === "Other") ? defaultColor : (colors[a] || defaultColor);
        const dot = row.addText("\u25CF");
        dot.font = Font.systemFont(10);
        dot.textColor = new Color(dotHex);
        row.addSpacer(6);
        const name = row.addText(a);
        name.font = Font.systemFont(12);
        name.textColor = WHITE;
        row.addSpacer();
        const mins = row.addText(`${perApp[a]}m`);
        mins.font = Font.mediumSystemFont(12);
        mins.textColor = MUTED;
        w.addSpacer(3);
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
