"""
One-time script to get a Google Drive refresh token with full drive scope.
Run locally on Mac — opens a browser for OAuth consent.

Required for GBP platform to open/close public access on Drive files.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_CONFIG = {
    "web": {
        "client_id":     input("Paste DRIVE_WEB_CLIENT_ID: ").strip(),
        "client_secret": input("Paste DRIVE_WEB_CLIENT_SECRET: ").strip(),
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8080/"],
    }
}

SCOPES = ["https://www.googleapis.com/auth/drive"]

flow  = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

print("NEW DRIVE_REFRESH_TOKEN with full drive scope (copy this to /etc/igbot.env):")
print(creds.refresh_token)
