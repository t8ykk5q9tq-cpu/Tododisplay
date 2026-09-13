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

// Draw a mini 24-hour ribbon colored by app, with faint hour gridlines and a
// subtle "now" marker so it reads clearly at a glance.
function drawRibbon(minutes, colors, defaultColor, width, height) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;

  // idle base (dark, clearly distinct from used minutes)
  ctx.setFillColor(new Color("#20263f"));
  ctx.fillRect(new Rect(0, 0, width, height));

  const perMin = width / 1440; // px per minute of day

  // faint hour gridlines every 3 hours
  ctx.setFillColor(new Color("#2f3856"));
  for (let h = 3; h < 24; h += 3) {
    const x = h * 60 * perMin;
    ctx.fillRect(new Rect(x, 0, 1, height));
  }

  // used minutes, colored by app (drawn a touch taller-feeling via full height)
  for (const key of Object.keys(minutes)) {
    const md = parseInt(key, 10);
    if (md < 0 || md >= 1440) continue;
    const app = minutes[key];
    const hex = (app && colors[app]) ? colors[app] : defaultColor;
    ctx.setFillColor(new Color(hex));
    const x = md * perMin;
    ctx.fillRect(new Rect(x, 0, Math.max(1.5, perMin + 0.5), height));
  }

  // "now" marker (thin white line at the current minute of day)
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  ctx.setFillColor(new Color("#ffffff", 0.85));
  ctx.fillRect(new Rect(nowMin * perMin, 0, 1.5, height));

  return ctx.getImage();
}

// A gradient background shared by all polished widgets, for a bit of depth.
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
  w.setPadding(10, 16, 10, 16);
  w.url = `${BASE_URL}/usage`; // tap to open the usage page

  const d = await fetchUsage();

  // Header row: title left, date chip right.
  const head = w.addStack();
  head.centerAlignContent();
  const header = head.addText("Screen Usage");
  header.font = Font.boldSystemFont(14);
  header.textColor = ACCENT;
  head.addSpacer();
  if (d) {
    const now = new Date();
    const chip = head.addText(
      now.toLocaleDateString([], { month: "short", day: "numeric" }));
    chip.font = Font.systemFont(10);
    chip.textColor = MUTED;
  }
  w.addSpacer(6);

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
  const totalH = Math.floor(total / 60), totalM = total % 60;
  const totalStr = totalH ? `${totalH}h ${totalM}m` : `${total}m`;

  // Big number: today's phone time, with a baseline-aligned unit.
  const bigRow = w.addStack();
  bigRow.bottomAlignContent();
  const big = bigRow.addText(totalStr);
  big.font = Font.boldSystemFont(size === "small" ? 22 : 28);
  big.textColor = total > 0 ? ALERT : MUTED;
  bigRow.addSpacer(7);
  const unit = bigRow.addText("on phone today");
  unit.font = Font.systemFont(11);
  unit.textColor = MUTED;
  bigRow.addSpacer(2);

  w.addSpacer(6);

  // 24-hour ribbon (rounded for legibility).
  const ribbonW = size === "small" ? 130 : 300;
  const ribbonH = size === "small" ? 12 : 16;
  const img = drawRibbon(d.minutes || {}, colors, defaultColor, ribbonW * 2, ribbonH * 2);
  const wimg = w.addImage(img);
  wimg.imageSize = new Size(ribbonW, ribbonH);
  wimg.cornerRadius = 5;

  // Top apps (medium/large only — small has no room). Trimmed to fit.
  if (size !== "small") {
    w.addSpacer(6);
    const apps = Object.keys(perApp).sort((a, b) => perApp[b] - perApp[a]);
    const maxRows = size === "large" ? 6 : 2;
    if (apps.length === 0) {
      const none = w.addText("No phone use logged today");
      none.font = Font.systemFont(11);
      none.textColor = MUTED;
    } else {
      const list = apps.slice(0, maxRows);
      list.forEach((a, i) => {
        const row = w.addStack();
        row.centerAlignContent();
        const dotHex = (a === "Other") ? defaultColor : (colors[a] || defaultColor);
        const dot = row.addText("\u25CF");
        dot.font = Font.systemFont(10);
        dot.textColor = new Color(dotHex);
        row.addSpacer(7);
        const name = row.addText(a);
        name.font = Font.mediumSystemFont(12);
        name.textColor = WHITE;
        row.addSpacer();
        const mins = row.addText(`${perApp[a]}m`);
        mins.font = Font.semiboldSystemFont(12);
        mins.textColor = MUTED;
        if (i < list.length - 1) w.addSpacer(4);
      });
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
