"""
One-time script to get a fresh Google Business Profile refresh token.
Run locally on Mac — opens a browser for OAuth consent.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_CONFIG = {
    "web": {
        "client_id":     input("Paste GBP_WEB_CLIENT_ID: ").strip(),
        "client_secret": input("Paste GBP_WEB_CLIENT_SECRET: ").strip(),
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost:8080/"],
    }
}

SCOPES = ["https://www.googleapis.com/auth/business.manage"]

flow  = InstalledAppFlow.from_client_config(CLIENT_CONFIG, SCOPES)
creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

print("NEW GBP_REFRESH_TOKEN (copy this to /etc/igbot.env):")
print(creds.refresh_token)
