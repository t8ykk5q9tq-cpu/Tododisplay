// AddItem.js — Scriptable (iOS) script to add an item to a list on the Pi.
//
// Use it a few ways:
//   - Tap the script in the Scriptable app
//   - Add it to your home screen as a "Run Script" shortcut (via the Shortcuts app)
//   - Trigger with Siri: "Hey Siri, Add to list"
//
// SETUP: set PI_HOST to your Pi's Tailscale address or MagicDNS name.

const PI_HOST = "100.67.122.101:5000"; // Pi's Tailscale address
const BASE_URL = `http://${PI_HOST}`;

async function postItem(listType, text) {
  const req = new Request(`${BASE_URL}/api/items/${listType}`);
  req.method = "POST";
  req.headers = { "Content-Type": "application/json" };
  req.body = JSON.stringify({ text });
  req.timeoutInterval = 8;
  return await req.loadJSON();
}

async function main() {
  // 1. Pick which list.
  const listAlert = new Alert();
  listAlert.title = "Add to which list?";
  listAlert.addAction("Todo");
  listAlert.addAction("Shopping");
  listAlert.addCancelAction("Cancel");
  const choice = await listAlert.presentAlert();
  if (choice === -1) return; // cancelled
  const listType = choice === 0 ? "todo" : "shopping";

  // 2. Prompt for the item text.
  const inputAlert = new Alert();
  inputAlert.title = `Add to ${listType === "todo" ? "Todo" : "Shopping"}`;
  inputAlert.addTextField("Item...");
  inputAlert.addAction("Add");
  inputAlert.addCancelAction("Cancel");
  const inputResult = await inputAlert.presentAlert();
  if (inputResult === -1) return;

  const text = inputAlert.textFieldValue(0).trim();
  if (!text) return;

  // 3. Send it to the Pi.
  try {
    await postItem(listType, text);
    const ok = new Alert();
    ok.title = "Added";
    ok.message = `"${text}" added to ${listType}.`;
    ok.addAction("OK");
    await ok.presentAlert();
  } catch (e) {
    const err = new Alert();
    err.title = "Couldn't add item";
    err.message =
      "Could not reach the Pi. Make sure Tailscale is on and the Pi is running.";
    err.addAction("OK");
    await err.presentAlert();
  }
}

await main();
Script.complete();
