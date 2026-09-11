// Loader.js — a tiny Scriptable loader that pulls the REAL widget code from
// GitHub each run, so pushing changes to the repo updates the widget with no
// re-pasting. Paste this once into a Scriptable script; edit WIDGET below to
// pick which widget to load.
//
// HOW TO USE:
//   1. In Scriptable, make a new script (e.g. "List Widget"), paste this in.
//   2. Set WIDGET to the file you want ("ListWidget.js" or "TrackerWidget.js").
//   3. Assign it to a home-screen Scriptable widget.
//   From then on, edit the widget code in the repo + push; the widget follows.

const WIDGET = "ListWidget.js";   // or "TrackerWidget.js"
const RAW_BASE =
  "https://raw.githubusercontent.com/t8ykk5q9tq-cpu/Tododisplay/main/scriptable/";

// Cache the fetched code so the widget still renders if the network is down.
const fm = FileManager.local();
const cacheDir = fm.joinPath(fm.cacheDirectory(), "tododisplay-widgets");
if (!fm.fileExists(cacheDir)) fm.createDirectory(cacheDir);
const cachePath = fm.joinPath(cacheDir, WIDGET);

let code = null;
try {
  const req = new Request(RAW_BASE + WIDGET);
  req.timeoutInterval = 8;
  code = await req.loadString();
  fm.writeString(cachePath, code);       // save latest for offline use
} catch (e) {
  if (fm.fileExists(cachePath)) {
    code = fm.readString(cachePath);      // fall back to last good copy
  } else {
    throw new Error("Can't fetch widget code and no cached copy: " + e);
  }
}

// Run the fetched widget code. It builds and sets the widget itself.
await eval(`(async () => { ${code} })()`);
