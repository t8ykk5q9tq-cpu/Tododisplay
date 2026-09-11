// TrackerLoader.js — paste this ONCE into a Scriptable script named "Tracker
// Widget" and assign it to a home-screen widget. It pulls the real
// TrackerWidget code from GitHub each run, so pushing changes to the repo
// updates the widget with no re-pasting. Caches the last copy for offline use.

const WIDGET = "TrackerWidget.js";
const RAW_BASE =
  "https://raw.githubusercontent.com/t8ykk5q9tq-cpu/Tododisplay/main/scriptable/";

const fm = FileManager.local();
const cacheDir = fm.joinPath(fm.cacheDirectory(), "tododisplay-widgets");
if (!fm.fileExists(cacheDir)) fm.createDirectory(cacheDir);
const cachePath = fm.joinPath(cacheDir, WIDGET);

let code = null;
try {
  const req = new Request(RAW_BASE + WIDGET);
  req.timeoutInterval = 8;
  code = await req.loadString();
  fm.writeString(cachePath, code);
} catch (e) {
  if (fm.fileExists(cachePath)) {
    code = fm.readString(cachePath);
  } else {
    throw new Error("Can't fetch widget code and no cached copy: " + e);
  }
}

await eval(`(async () => { ${code} })()`);
