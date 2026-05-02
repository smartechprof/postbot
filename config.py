import os
import logging

log = logging.getLogger(__name__)

# ── Google Drive (source of videos) ──────────────────────────────────────────
DRIVE_REFRESH_TOKEN  = os.getenv("DRIVE_REFRESH_TOKEN")
DRIVE_CLIENT_ID      = os.getenv("DRIVE_CLIENT_ID")       # legacy, kept for compatibility
DRIVE_CLIENT_SECRET  = os.getenv("DRIVE_CLIENT_SECRET")   # legacy, kept for compatibility
DRIVE_WEB_CLIENT_ID     = os.getenv("DRIVE_WEB_CLIENT_ID")
DRIVE_WEB_CLIENT_SECRET = os.getenv("DRIVE_WEB_CLIENT_SECRET")

# ── Instagram ─────────────────────────────────────────────────────────────────
IG_USER_ID   = os.getenv("IG_USER_ID")
IG_PAGE_TOKEN = os.getenv("IG_PAGE_TOKEN")

# ── Threads ───────────────────────────────────────────────────────────────────
THREADS_USER_ID      = os.getenv("THREADS_USER_ID")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN")

# ── Facebook ──────────────────────────────────────────────────────────────────
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN")
FB_PAGE_ID    = os.getenv("FB_PAGE_ID")

# ── YouTube ───────────────────────────────────────────────────────────────────
YT_REFRESH_TOKEN    = os.getenv("YT_REFRESH_TOKEN")
YT_CLIENT_ID        = os.getenv("YT_CLIENT_ID")        # legacy, kept for compatibility
YT_CLIENT_SECRET    = os.getenv("YT_CLIENT_SECRET")    # legacy, kept for compatibility
YT_WEB_CLIENT_ID     = os.getenv("YT_WEB_CLIENT_ID")
YT_WEB_CLIENT_SECRET = os.getenv("YT_WEB_CLIENT_SECRET")

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# ── LinkedIn ──────────────────────────────────────────────────────────────────
LI_ACCESS_TOKEN   = os.getenv("LI_ACCESS_TOKEN")
LI_CLIENT_ID      = os.getenv("LI_CLIENT_ID")
LI_CLIENT_SECRET  = os.getenv("LI_CLIENT_SECRET")
LI_ORGANIZATION_ID = os.getenv("LI_ORGANIZATION_ID")  # optional: publish to org page too

# ── TikTok ───────────────────────────────────────────────────────────────────
TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
TIKTOK_ACCESS_TOKEN  = os.getenv("TIKTOK_ACCESS_TOKEN")
TIKTOK_REFRESH_TOKEN = os.getenv("TIKTOK_REFRESH_TOKEN")

# ── Google Business Profile ───────────────────────────────────────────────────
GBP_ACCOUNT_ID    = os.getenv("GBP_ACCOUNT_ID")
GBP_LOCATION_ID   = os.getenv("GBP_LOCATION_ID")
GBP_REFRESH_TOKEN = os.getenv("GBP_REFRESH_TOKEN")

# ── X / Twitter (placeholder for future) ─────────────────────────────────────
X_API_KEY              = os.getenv("X_API_KEY")
X_API_SECRET           = os.getenv("X_API_SECRET")
X_ACCESS_TOKEN         = os.getenv("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET  = os.getenv("X_ACCESS_TOKEN_SECRET")

# ── Pinterest (placeholder for future) ───────────────────────────────────────
PINTEREST_ACCESS_TOKEN = os.getenv("PINTEREST_ACCESS_TOKEN")
PINTEREST_BOARD_ID     = os.getenv("PINTEREST_BOARD_ID")

# ── Bluesky ──────────────────────────────────────────────────────────────────
BLUESKY_HANDLE       = os.getenv("BLUESKY_HANDLE")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD")

# ── General settings ──────────────────────────────────────────────────────────
DRIVE_FOLDER_NAME = os.getenv("DRIVE_FOLDER_NAME", "reels for pb")
STATE_FILE        = os.getenv("STATE_FILE", "state.txt")
METADATA_FILE     = os.getenv("METADATA_FILE", "metadata.json")
SAFE_MODE         = os.getenv("SAFE_MODE", "1") == "1"

# ── Validation ────────────────────────────────────────────────────────────────
_REQUIRED = {
    "DRIVE_REFRESH_TOKEN": DRIVE_REFRESH_TOKEN,
    "DRIVE_WEB_CLIENT_ID":     DRIVE_WEB_CLIENT_ID,
    "DRIVE_WEB_CLIENT_SECRET": DRIVE_WEB_CLIENT_SECRET,
    "IG_USER_ID":          IG_USER_ID,
    "IG_PAGE_TOKEN":       IG_PAGE_TOKEN,
    "THREADS_USER_ID":      THREADS_USER_ID,
    "THREADS_ACCESS_TOKEN": THREADS_ACCESS_TOKEN,
    "FB_PAGE_TOKEN":       FB_PAGE_TOKEN,
    "FB_PAGE_ID":          FB_PAGE_ID,
    "YT_REFRESH_TOKEN":    YT_REFRESH_TOKEN,
    "YT_WEB_CLIENT_ID":        YT_WEB_CLIENT_ID,
    "YT_WEB_CLIENT_SECRET":    YT_WEB_CLIENT_SECRET,
    "YT_CLIENT_SECRET":    YT_CLIENT_SECRET,
    "TELEGRAM_BOT_TOKEN":  TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHANNEL_ID": TELEGRAM_CHANNEL_ID,
    "LI_ACCESS_TOKEN":     LI_ACCESS_TOKEN,
    "LI_CLIENT_ID":        LI_CLIENT_ID,
    "LI_CLIENT_SECRET":    LI_CLIENT_SECRET,
    "GBP_ACCOUNT_ID":    GBP_ACCOUNT_ID,
    "GBP_LOCATION_ID":   GBP_LOCATION_ID,
    "GBP_REFRESH_TOKEN": GBP_REFRESH_TOKEN,
    "TIKTOK_CLIENT_KEY":    TIKTOK_CLIENT_KEY,
    "TIKTOK_CLIENT_SECRET": TIKTOK_CLIENT_SECRET,
    "TIKTOK_REFRESH_TOKEN": TIKTOK_REFRESH_TOKEN,
    "BLUESKY_HANDLE":       BLUESKY_HANDLE,
    "BLUESKY_APP_PASSWORD": BLUESKY_APP_PASSWORD,
}

_missing = [name for name, value in _REQUIRED.items() if not value]
if _missing:
    log.warning("Missing %d required environment variables. Check deployment guide.", len(_missing))

# ── Retry settings ────────────────────────────────────────────────────────────
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "5"))
if MAX_RETRY_ATTEMPTS > 8:
    MAX_RETRY_ATTEMPTS = 8
    log.warning("MAX_RETRY_ATTEMPTS capped at 8 for safety")
