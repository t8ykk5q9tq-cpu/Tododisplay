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

## Notes

- Each time you open the app, a check-in is logged. This tracks HOW OFTEN you
  open them (a usage signal), not exact watch duration — iOS doesn't expose
  duration to automations.
- If the Pi's Tailscale address changes, update the URLs here and in the
  automations.
- To add more apps (Instagram, etc.): add the category to `CATEGORIES` in
  `tracker_config.py` (or it still logs fine as free text) and make another
  automation pointing at `/quicklog?category=Instagram`.
- Want it to also confirm visually? The `/quicklog` page returns a small
  "Logged" page, but with "Ask Before Running" off it runs invisibly, which is
  usually what you want for passive tracking.
