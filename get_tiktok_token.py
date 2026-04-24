"""
One-time script to get TikTok OAuth tokens (access + refresh).
Run locally on Mac — opens browser for OAuth consent,
then exchange authorization code for tokens.

Flow:
  1. Opens TikTok auth URL in browser
  2. User authorizes → redirected to botshub.io/oauth/callback?code=XXX
  3. Copy the 'code' param from the URL bar
  4. Paste it here → script exchanges it for access + refresh tokens
"""

import requests
import urllib.parse
import webbrowser

CLIENT_KEY = input("Paste TIKTOK_CLIENT_KEY: ").strip()
CLIENT_SECRET = input("Paste TIKTOK_CLIENT_SECRET: ").strip()

REDIRECT_URI = "https://botshub.io/oauth/callback"
SCOPES = "user.info.basic,video.publish,video.upload"

# Step 1: Open browser for authorization
auth_url = (
    "https://www.tiktok.com/v2/auth/authorize/"
    f"?client_key={CLIENT_KEY}"
    f"&scope={urllib.parse.quote(SCOPES)}"
    f"&response_type=code"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
)

print("\nOpening TikTok authorization in browser...")
print(f"If browser doesn't open, visit:\n{auth_url}\n")
webbrowser.open(auth_url)

# Step 2: User pastes the code from redirect URL
print("After authorizing, you'll be redirected to:")
print(f"  {REDIRECT_URI}?code=XXXXX")
print()
code = input("Paste the 'code' value from the URL: ").strip()

if not code:
    print("ERROR: No code provided. Exiting.")
    exit(1)

# Step 3: Exchange code for tokens
print("\nExchanging code for tokens...")
resp = requests.post(
    "https://open.tiktokapis.com/v2/oauth/token/",
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
    },
    data={
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    },
    timeout=30,
)

if not resp.ok:
    print(f"ERROR: HTTP {resp.status_code}")
    print(resp.text[:500])
    exit(1)

data = resp.json()

if "access_token" not in data:
    print(f"ERROR: {data}")
    exit(1)

print("\n" + "=" * 60)
print("SUCCESS! Add these to /etc/igbot.env:")
print("=" * 60)
print(f"\nTIKTOK_CLIENT_KEY={CLIENT_KEY}")
print(f"TIKTOK_CLIENT_SECRET={CLIENT_SECRET}")
print(f"TIKTOK_ACCESS_TOKEN={data['access_token']}")
print(f"TIKTOK_REFRESH_TOKEN={data['refresh_token']}")
print(f"\nAccess token expires in: {data.get('expires_in', '?')} seconds (24h)")
print(f"Refresh token expires in: {data.get('refresh_expires_in', '?')} seconds (~1 year)")
print(f"Scopes granted: {data.get('scope', '?')}")
print(f"Open ID: {data.get('open_id', '?')}")
print("\nIMPORTANT: Access token expires daily.")
print("PostBot will auto-refresh it using the refresh token.")
