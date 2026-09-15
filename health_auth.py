#!/usr/bin/env python3
"""health_auth.py -- one-time Google Health API authorization helper.

Run this ONCE (on any machine with a browser handy) to turn your OAuth
Client ID + Secret into a long-lived refresh token for the Pi.

Steps:
  1. python3 health_auth.py
  2. It prints an authorize URL. Open it, sign in, click Allow.
  3. Google redirects to https://www.google.com/?code=XXXX  -- copy the
     `code` value out of the browser's address bar.
  4. Paste the code (and your Client Secret) when prompted here.
  5. It prints the refresh token. Put CLIENT_ID / CLIENT_SECRET / the
     refresh token into tracker_config.py on the Pi (gitignored).

Nothing is written to disk and no secret is committed; you copy the
resulting refresh token into tracker_config.py yourself.
"""
import json
import sys
import urllib.parse
import urllib.request

# The read-only scopes we need for a dashboard (steps/HR/activity + sleep).
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
]
REDIRECT_URI = "https://www.google.com"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def _extract_code(pasted):
    """Accept either a bare code or the full redirect URL/query string and
    return just the auth code (the value of `code`, before any `&scope`)."""
    pasted = pasted.strip()
    if "code=" in pasted:
        # Pull the code= value out of a URL or query string.
        after = pasted.split("code=", 1)[1]
        pasted = after.split("&", 1)[0]
    return urllib.parse.unquote(pasted)


def main():
    print("=== Google Health API auth helper ===\n")
    client_id = input("Client ID: ").strip()
    if not client_id:
        print("Client ID is required.")
        sys.exit(1)

    # 1) Build and show the authorization URL.
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "access_type": "offline",       # needed to receive a refresh token
        "prompt": "consent",            # force a fresh refresh token
        "scope": " ".join(SCOPES),
    }
    auth_url = AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)
    print("\n1) Open this URL in a browser, sign in, and click Allow:\n")
    print(auth_url)
    print("\n2) You'll be redirected to https://www.google.com/?code=...")
    print("   Copy the value of `code` from the address bar.\n")

    pasted = input("Paste the code (or the whole redirect URL) here: ").strip()
    code = _extract_code(pasted)
    client_secret = input("Client Secret: ").strip()

    # 3) Exchange the code for tokens.
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        print("\nToken exchange failed:", e.read().decode(errors="replace"))
        sys.exit(1)

    refresh = tok.get("refresh_token")
    if not refresh:
        print("\nNo refresh_token returned. Response was:")
        print(json.dumps(tok, indent=2))
        print("\nMake sure access_type=offline and prompt=consent (they are), "
              "and that you didn't already authorize without offline access.")
        sys.exit(1)

    print("\n=== SUCCESS ===")
    print("Add these to tracker_config.py on the Pi (gitignored):\n")
    print(f'GOOGLE_HEALTH_CLIENT_ID = "{client_id}"')
    print('GOOGLE_HEALTH_CLIENT_SECRET = "<your client secret>"')
    print(f'GOOGLE_HEALTH_REFRESH_TOKEN = "{refresh}"')
    print("\n(Access token, for reference, expires in "
          f"{tok.get('expires_in', '?')}s -- the Pi refreshes it automatically.)")


if __name__ == "__main__":
    main()
