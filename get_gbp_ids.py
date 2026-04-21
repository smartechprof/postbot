"""
One-time script to discover GBP_ACCOUNT_ID and GBP_LOCATION_ID.
Run locally in Terminal — no browser needed, uses existing refresh token.

Usage:
    python3 get_gbp_ids.py
"""

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/business.manage"]

refresh_token = input("Paste GBP_REFRESH_TOKEN: ").strip()
client_id     = input("Paste YT_WEB_CLIENT_ID: ").strip()
client_secret = input("Paste YT_WEB_CLIENT_SECRET: ").strip()

creds = Credentials(
    token=None,
    refresh_token=refresh_token,
    client_id=client_id,
    client_secret=client_secret,
    token_uri="https://oauth2.googleapis.com/token",
    scopes=SCOPES,
)
creds.refresh(Request())
token = creds.token
headers = {"Authorization": f"Bearer {token}"}

_ACCOUNT_API  = "https://mybusinessaccountmanagement.googleapis.com/v1"
_LOCATION_API = "https://mybusinessbusinessinformation.googleapis.com/v1"

print("\n── Accounts ─────────────────────────────────────────────────────────")
resp = requests.get(f"{_ACCOUNT_API}/accounts", headers=headers, timeout=30)
resp.raise_for_status()
accounts = resp.json().get("accounts", [])

if not accounts:
    print("No accounts found.")
else:
    for account in accounts:
        account_name = account.get("name", "")        # "accounts/123456789"
        account_id   = account_name.split("/")[-1]
        display_name = account.get("accountName", "")
        print(f"\n  accountName : {display_name}")
        print(f"  GBP_ACCOUNT_ID={account_id}")

        print(f"\n  ── Locations for {display_name} ──────────────────────────")
        loc_resp = requests.get(
            f"{_LOCATION_API}/accounts/{account_id}/locations",
            headers=headers,
            params={"readMask": "name,title"},
            timeout=30,
        )
        if not loc_resp.ok:
            print(f"  Could not fetch locations: HTTP {loc_resp.status_code} {loc_resp.text[:200]}")
            continue

        locations = loc_resp.json().get("locations", [])
        if not locations:
            print("  No locations found.")
        for loc in locations:
            loc_name    = loc.get("name", "")          # "locations/456"
            location_id = loc_name.split("/")[-1]
            loc_display = loc.get("title", "")
            print(f"    locationName   : {loc_display}")
            print(f"    GBP_LOCATION_ID={location_id}")
            print()

print("─────────────────────────────────────────────────────────────────────")
print("Copy GBP_ACCOUNT_ID and GBP_LOCATION_ID into /etc/igbot.env")
