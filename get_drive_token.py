"""
Get Google Drive OAuth2 refresh token using google-auth-oauthlib.

Reads DRIVE_WEB_CLIENT_ID and DRIVE_WEB_CLIENT_SECRET from environment variables,
starts a local server on port 8080 to handle the OAuth callback,
and prints the refresh_token on success.

Usage:
    export DRIVE_WEB_CLIENT_ID=your_client_id
    export DRIVE_WEB_CLIENT_SECRET=your_client_secret
    python3 get_drive_token.py
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

CLIENT_ID     = os.environ.get("DRIVE_WEB_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DRIVE_WEB_CLIENT_SECRET", "")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ DRIVE_WEB_CLIENT_ID and DRIVE_WEB_CLIENT_SECRET must be set as environment variables.")
    print("   Example: export DRIVE_WEB_CLIENT_ID=xxx && export DRIVE_WEB_CLIENT_SECRET=yyy")
    sys.exit(1)

client_config = {
    "web": {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8080"],
    }
}

flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
creds = flow.run_local_server(port=8080)

if creds.refresh_token:
    print("\n✅ Success!\n")
    print(f"DRIVE_REFRESH_TOKEN={creds.refresh_token}\n")
    print("Add this to /etc/igbot.env on the server.")
else:
    print("❌ No refresh_token returned. Try revoking access at myaccount.google.com/permissions and re-running.")
    sys.exit(1)
