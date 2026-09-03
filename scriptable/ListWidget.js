// ListWidget.js — Scriptable (iOS) home-screen widget
// Shows your Todo and Shopping lists from the Raspberry Pi over Tailscale.
//
// SETUP:
//   1. PI_HOST is already set to your Pi's Tailscale address below.
//   2. In the Scriptable app, create a new script, paste this in, name it.
//   3. Add a Scriptable widget to your home screen (medium or large works best),
//      long-press it, choose "Edit Widget", and select this script.
//
// The widget is READ-ONLY (iOS widgets can't take text input). Tapping it
// opens the full web interface so you can add/edit items.

const PI_HOST = "100.67.122.101:5000"; // Pi's Tailscale address
const BASE_URL = `http://${PI_HOST}`;

// --- Colors ---
const BG = new Color("#1a1a2e");
const PANEL = new Color("#16213e");
const ACCENT = new Color("#00d4ff");
const WHITE = new Color("#eaeaea");
const MUTED = new Color("#8a8a9a");
const DIVIDER = new Color("#2a2f4a");

async function fetchItems(listType) {
  try {
    const req = new Request(`${BASE_URL}/api/items/${listType}`);
    req.timeoutInterval = 8;
    return await req.loadJSON();
  } catch (e) {
    return null; // null = couldn't reach the Pi
  }
}

// How many item rows fit, based on widget size.
function rowsForSize(size) {
  if (size === "large") return 10;
  if (size === "small") return 4;
  return 5; // medium
}

// Build one list column inside a vertical stack.
function buildColumn(container, title, items, maxRows, fontSize) {
  const col = container.addStack();
  col.layoutVertically();

  // Column header with count
  const head = col.addStack();
  head.centerAlignContent();
  const titleTxt = head.addText(title);
  titleTxt.font = Font.boldSystemFont(fontSize + 2);
  titleTxt.textColor = ACCENT;
  head.addSpacer();
  if (items && items.length) {
    const count = head.addText(String(items.length));
    count.font = Font.mediumSystemFont(fontSize);
    count.textColor = MUTED;
  }
  col.addSpacer(5);

  if (items === null) {
    const err = col.addText("Can't reach Pi");
    err.font = Font.systemFont(fontSize);
    err.textColor = MUTED;
    return;
  }
  if (items.length === 0) {
    const empty = col.addText("All clear");
    empty.font = Font.systemFont(fontSize);
    empty.textColor = MUTED;
    return;
  }

  const shown = items.slice(0, maxRows);
  for (const item of shown) {
    const row = col.addStack();
    row.centerAlignContent();
    row.spacing = 4;

    const bullet = row.addText(item.done ? "\u2713" : "\u2022");
    bullet.font = Font.systemFont(fontSize);
    bullet.textColor = item.done ? MUTED : ACCENT;

    const txt = row.addText(item.text);
    txt.font = Font.systemFont(fontSize);
    txt.textColor = item.done ? MUTED : WHITE;
    txt.lineLimit = 1;

    col.addSpacer(3);
  }

  if (items.length > shown.length) {
    const more = col.addText(`+${items.length - shown.length} more`);
    more.font = Font.systemFont(fontSize - 2);
    more.textColor = MUTED;
  }
}

async function buildWidget() {
  const size = config.widgetFamily || "medium";
  const widget = new ListWidget();
  widget.backgroundColor = BG;
  widget.setPadding(14, 16, 14, 16);
  widget.url = BASE_URL; // tap to open the full web app

  const [todo, shopping] = await Promise.all([
    fetchItems("todo"),
    fetchItems("shopping"),
  ]);

  const maxRows = rowsForSize(size);
  const fontSize = size === "small" ? 11 : 13;

  if (size === "small") {
    // Small widget: only room for one list — show Todo.
    buildColumn(widget, "Todo", todo, maxRows, fontSize);
  } else {
    // Medium / large: two columns side by side with a divider.
    const cols = widget.addStack();
    cols.topAlignContent();

    const left = cols.addStack();
    left.layoutVertically();
    left.size = new Size(0, 0);
    buildColumn(left, "Todo", todo, maxRows, fontSize);

    cols.addSpacer(12);
    // Thin vertical divider
    const line = cols.addStack();
    line.backgroundColor = DIVIDER;
    line.size = new Size(1, size === "large" ? 260 : 120);
    cols.addSpacer(12);

    const right = cols.addStack();
    right.layoutVertically();
    buildColumn(right, "Shopping", shopping, maxRows, fontSize);
  }

  // Footer timestamp
  widget.addSpacer(6);
  const foot = widget.addText("Updated " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
  foot.font = Font.systemFont(9);
  foot.textColor = MUTED;

  return widget;
}

const widget = await buildWidget();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
