# iOS Shortcuts — Tap-to-Add Icons

Add items to your lists from a home-screen icon. These talk to the Pi over
Tailscale, so make sure the Tailscale app is on when away from home Wi-Fi.

Replace `PI_HOST` everywhere with your Pi's Tailscale address or MagicDNS name,
e.g. `100.101.102.103:5000` or `todoscreen:5000`.

---

## Option 1: One icon that asks which list

Creates a single "Add to List" icon. Tapping it asks Todo/Shopping, then the item.

Build in the **Shortcuts** app → **+** (new shortcut) → add these actions in order:

1. **Choose from Menu**
   - Prompt: `Add to which list?`
   - Menu items: `Todo` and `Shopping`

2. Under the **Todo** branch:
   - **Ask for Input**
     - Prompt: `Add to Todo`
     - Input Type: Text
   - **Get Contents of URL**
     - URL: `http://PI_HOST/api/items/todo`
     - Method: `POST`
     - Request Body: `JSON`
     - Add field — Key: `text`, Type: Text, Value: the **Provided Input** variable
     - (Add a header) Key: `Content-Type`, Value: `application/json`

3. Under the **Shopping** branch: same as above but
   - Prompt: `Add to Shopping`
   - URL: `http://PI_HOST/api/items/shopping`

4. (Optional) **Show Notification**
   - Text: `Added!`

Name it "Add to List", pick an icon/color, then:
**Share** (the shortcut) → **Add to Home Screen**.

---

## Option 2: Two separate icons (fastest — one tap each)

If you'd rather skip the menu, make two shortcuts so each is a single tap.

### "Add Todo"
1. **Ask for Input** — Prompt: `Add a task`, Type: Text
2. **Get Contents of URL**
   - URL: `http://PI_HOST/api/items/todo`
   - Method: `POST`
   - Headers: `Content-Type` = `application/json`
   - Request Body: JSON → Key `text` = Provided Input
3. (Optional) **Show Notification** — `Task added`

Name it "Add Todo" → Share → Add to Home Screen.

### "Add Shopping"
Same steps, but URL: `http://PI_HOST/api/items/shopping`.
Name it "Add Shopping" → Share → Add to Home Screen.

---

## Notes

- The API expects a POST with JSON body `{"text": "your item"}`. That's exactly
  what the "Get Contents of URL" action sends when configured as above.
- If adding fails, check: Tailscale is on, the Pi is powered on, and `PI_HOST`
  is correct (test `http://PI_HOST:5000` in a browser first).
- Items appear on the Pi's display within ~5 seconds.
