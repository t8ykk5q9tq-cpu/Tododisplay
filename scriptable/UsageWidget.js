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
// Draw a zoomed-in ribbon of the LAST `windowMin` minutes (default 120 = 2h),
// ending at the current minute. Each minute is wide enough to actually see.
const RIBBON_WINDOW_MIN = 120; // last 2 hours

function drawRibbon(minutes, colors, defaultColor, width, height) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;

  // idle base (dark, clearly distinct from used minutes)
  ctx.setFillColor(new Color("#20263f"));
  ctx.fillRect(new Rect(0, 0, width, height));

  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const startMin = nowMin - RIBBON_WINDOW_MIN + 1; // window is [startMin .. nowMin]
  const perMin = width / RIBBON_WINDOW_MIN;         // px per minute in the window

  // faint gridlines every 30 min within the window
  ctx.setFillColor(new Color("#2f3856"));
  for (let m = startMin; m <= nowMin; m++) {
    if (((m % 60) + 60) % 60 === 0 || ((m % 60) + 60) % 60 === 30) {
      const x = (m - startMin) * perMin;
      ctx.fillRect(new Rect(x, 0, 1, height));
    }
  }

  // used minutes within the window, colored by app
  for (const key of Object.keys(minutes)) {
    const md = parseInt(key, 10);
    if (md < startMin || md > nowMin) continue; // only the last 2 hours
    const app = minutes[key];
    const hex = (app && colors[app]) ? colors[app] : defaultColor;
    ctx.setFillColor(new Color(hex));
    const x = (md - startMin) * perMin;
    ctx.fillRect(new Rect(x, 0, Math.max(2, perMin), height));
  }

  // "now" marker at the right edge (end of the window)
  ctx.setFillColor(new Color("#ffffff", 0.85));
  ctx.fillRect(new Rect(width - 2, 0, 2, height));

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
  // Corner-safe padding: iOS widget corners are ~22pt, so keep content
  // inset from the curve (esp. horizontally) so nothing clips the corners.
  w.setPadding(12, 18, 12, 18);
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

  w.addSpacer(5);

  // Zoomed ribbon: last 2 hours (taller now that each minute is wide).
  const ribbonW = size === "small" ? 130 : 300;
  const ribbonH = size === "small" ? 16 : 22;
  const img = drawRibbon(d.minutes || {}, colors, defaultColor, ribbonW * 3, ribbonH * 3);
  const ribRow = w.addStack();
  const wimg = ribRow.addImage(img);
  wimg.imageSize = new Size(ribbonW, ribbonH);
  wimg.applyFillingContentMode();
  wimg.cornerRadius = 5;

  // Window axis with the used-minutes count in the middle: "2h ago … Xm … now".
  const now2 = new Date();
  const nowMin2 = now2.getHours() * 60 + now2.getMinutes();
  const startMin2 = nowMin2 - 120 + 1;
  let winUsed = 0;
  for (const k of Object.keys(d.minutes || {})) {
    const md = parseInt(k, 10);
    if (md >= startMin2 && md <= nowMin2) winUsed++;
  }
  const axis = w.addStack();
  const aL = axis.addText("2h ago"); aL.font = Font.systemFont(8); aL.textColor = MUTED;
  axis.addSpacer();
  const aM = axis.addText(`${winUsed}m in last 2h`); aM.font = Font.systemFont(8); aM.textColor = MUTED;
  axis.addSpacer();
  const aR = axis.addText("now"); aR.font = Font.systemFont(8); aR.textColor = MUTED;

  // Top apps (medium/large only — small has no room). Trimmed to fit.
  if (size !== "small") {
    w.addSpacer(5);
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
