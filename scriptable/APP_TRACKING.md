# Auto-log app opens (TikTok / YouTube) to the Time Tracker

iOS can't track "phone unlocked", but it CAN run an automation when you OPEN a
specific app. We use that to log a check-in to the tracker's `/quicklog`
endpoint each time you open TikTok or YouTube — over Tailscale, fully automatic.

The tracker already has "TikTok" and "YouTube" categories, so these opens show
up in the daily summary alongside your other check-ins.

Requirements: Tailscale on your phone must be ON for the request to reach the Pi.

---

## Build the TikTok automation

1. Open the **Shortcuts** app → **Automation** tab (bottom) → **+** (top right)
   → **Create Personal Automation**.
2. Scroll to **App** → tap it.
   - **App:** tap Choose → select **TikTok**
   - **When:** check **Is Opened** (uncheck "Is Closed")
   - Tap **Next**.
3. Tap **Add Action**, search **Get Contents of URL**, add it.
   - URL: `http://100.102.96.42:5050/quicklog?category=TikTok`
   - (Method can stay GET — no other options needed.)
4. Tap **Next**.
5. **Turn OFF "Ask Before Running"** (so it logs silently). Confirm "Don't Ask".
6. Tap **Done**.

## Build the YouTube automation

Repeat the exact steps above, but:
- **App:** YouTube
- **URL:** `http://100.102.96.42:5050/quicklog?category=YouTube`

---

## Track actual TIME in app (open + close automations)

For real time-spent (not just opens), make TWO automations per app - one for
open, one for close - pointing at the timing endpoints:

- **When [TikTok] Is Opened** -> Get Contents of URL:
  `http://100.102.96.42:5050/appstart?app=TikTok`
- **When [TikTok] Is Closed** -> Get Contents of URL:
  `http://100.102.96.42:5050/appstop?app=TikTok`

The server computes close-minus-open = time spent, and the display's "App Time"
box shows today's total per app (e.g. "TikTok 47m"). Sessions longer than 4h
are ignored (guards against the phone sleeping with the app "open").

Turn OFF "Ask Before Running" on both so they fire silently.

## Notes

- The open-only `/quicklog?category=...` approach (above) tracks HOW OFTEN you
  open an app. The open+close `/appstart` + `/appstop` approach tracks actual
  TIME spent. Use whichever you prefer (or both).
- If the Pi's Tailscale address changes, update the URLs here and in the
  automations.
- To add more apps (Instagram, etc.): add the category to `CATEGORIES` in
  `tracker_config.py` (or it still logs fine as free text) and make another
  automation pointing at `/quicklog?category=Instagram`.
- Want it to also confirm visually? The `/quicklog` page returns a small
  "Logged" page, but with "Ask Before Running" off it runs invisibly, which is
  usually what you want for passive tracking.
