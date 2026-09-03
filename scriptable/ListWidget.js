// ListWidget.js — Scriptable (iOS) home-screen widget
// Shows your Todo and Shopping lists from the Raspberry Pi over Tailscale.
//
// SETUP:
//   1. Set PI_HOST below to your Pi's Tailscale address or MagicDNS name.
//      e.g. "100.101.102.103:5000"  or  "todoscreen:5000"
//   2. In the Scriptable app, create a new script, paste this in, name it.
//   3. Add a Scriptable widget to your home screen, long-press it, choose
//      "Edit Widget", and select this script.
//
// The widget is READ-ONLY (iOS widgets can't take text input). Tapping it
// opens the full web interface so you can add/edit items.

const PI_HOST = "100.67.122.101:5000"; // Pi's Tailscale address
const BASE_URL = `http://${PI_HOST}`;

async function fetchItems(listType) {
  try {
    const req = new Request(`${BASE_URL}/api/items/${listType}`);
    req.timeoutInterval = 8;
    return await req.loadJSON();
  } catch (e) {
    return null; // null signals a fetch error (e.g. Tailscale off)
  }
}

function addListSection(widget, title, items, accentColor) {
  const header = widget.addText(title);
  header.font = Font.boldSystemFont(13);
  header.textColor = accentColor;
  widget.addSpacer(3);

  if (items === null) {
    const err = widget.addText("Can't reach Pi");
    err.font = Font.systemFont(10);
    err.textColor = Color.gray();
    return;
  }
  if (items.length === 0) {
    const empty = widget.addText("—");
    empty.font = Font.systemFont(11);
    empty.textColor = Color.gray();
    return;
  }

  // Show up to 4 items per list to fit a medium widget.
  const shown = items.slice(0, 4);
  for (const item of shown) {
    const prefix = item.done ? "\u2713 " : "\u2022 ";
    const line = widget.addText(prefix + item.text);
    line.font = Font.systemFont(11);
    line.textColor = item.done ? Color.gray() : Color.white();
    line.lineLimit = 1;
  }
  if (items.length > shown.length) {
    const more = widget.addText(`+${items.length - shown.length} more`);
    more.font = Font.systemFont(9);
    more.textColor = Color.gray();
  }
}

async function buildWidget() {
  const widget = new ListWidget();
  widget.backgroundColor = new Color("#1a1a2e");
  widget.setPadding(12, 14, 12, 14);

  // Tapping the widget opens the full web app.
  widget.url = BASE_URL;

  const [todo, shopping] = await Promise.all([
    fetchItems("todo"),
    fetchItems("shopping"),
  ]);

  addListSection(widget, "TODO", todo, new Color("#00d4ff"));
  widget.addSpacer(8);
  addListSection(widget, "SHOPPING", shopping, new Color("#00d4ff"));

  return widget;
}

const widget = await buildWidget();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  // When run inside the app (not as a widget), show a preview.
  await widget.presentMedium();
}
Script.complete();
