"""
Test script: publish to Google Business Profile.

Environment variables are loaded from /etc/igbot.env if the file exists,
then .env.test fills in any missing vars for local testing.

Run with SAFE_MODE=1 (default in .env.test) to skip actual publishing.
Set SAFE_MODE=0 in the env file to do a real publish.

Two checks are always run:
  1. SAFE_MODE publish returns {"ok": True}.
  2. Missing-token guard returns {"ok": False}.
"""

import logging
import os
import sys
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("test_gbp")

# ── Load environment ──────────────────────────────────────────────────────────
_ENV_PRODUCTION = "/etc/igbot.env"
_ENV_LOCAL      = os.path.join(os.path.dirname(__file__), ".env.test")


def _load_env_file(path: str) -> None:
    """Read key=value pairs from a file and inject into os.environ."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


if os.path.exists(_ENV_PRODUCTION):
    log.info("Loading environment from %s", _ENV_PRODUCTION)
    _load_env_file(_ENV_PRODUCTION)

if os.path.exists(_ENV_LOCAL):
    log.info("Loading environment from %s (filling missing vars)", _ENV_LOCAL)
    _load_env_file(_ENV_LOCAL)

if not os.path.exists(_ENV_PRODUCTION) and not os.path.exists(_ENV_LOCAL):
    log.info("No env file found — using system environment.")

# ── Import project modules AFTER env is loaded ────────────────────────────────
import config
import drive
import metadata
from platforms import gbp

VIDEO_ID  = "001"
FILE_NAME = f"{VIDEO_ID}.mov"


def main() -> None:
    log.info("=== test_gbp.py | SAFE_MODE=%s ===", config.SAFE_MODE)
    all_ok = True

    # ── Prepare video path ────────────────────────────────────────────────────
    if config.SAFE_MODE:
        dummy_path = os.path.join(tempfile.gettempdir(), FILE_NAME)
        open(dummy_path, "wb").close()
        video_path = dummy_path
        log.info("SAFE_MODE — skipping Drive download, using dummy file: %s", video_path)
    else:
        log.info("Step 1: downloading '%s' from Google Drive...", FILE_NAME)
        try:
            video_path = drive.download_file(FILE_NAME)
        except Exception as exc:
            log.error("Drive download failed: %s", exc)
            sys.exit(1)
        log.info("Downloaded to: %s", video_path)

    # ── Read metadata ─────────────────────────────────────────────────────────
    log.info("Step 2: reading metadata for video '%s'...", VIDEO_ID)
    try:
        full_meta = metadata.get_metadata(VIDEO_ID)
        platform_meta = full_meta.get("gmaps") if full_meta else None
    except KeyError as exc:
        log.error("Metadata error: %s", exc)
        sys.exit(1)

    if platform_meta is None:
        log.error("No 'gmaps' key in metadata for video '%s'.", VIDEO_ID)
        sys.exit(1)

    log.info("Metadata: %s", platform_meta)

    # ── Check 1: SAFE_MODE returns {"ok": True} ───────────────────────────────
    log.info("Check 1: publish() in SAFE_MODE...")
    saved_safe_mode = config.SAFE_MODE
    config.SAFE_MODE = True

    result = gbp.publish(video_path, platform_meta)

    config.SAFE_MODE = saved_safe_mode

    print()
    if result.get("ok"):
        print("✅ Check 1 passed: SAFE_MODE publish returned ok=True.")
    else:
        print(f"❌ Check 1 failed: expected ok=True, got: {result}")
        all_ok = False

    # ── Check 2: missing token returns {"ok": False} ──────────────────────────
    log.info("Check 2: publish() with missing GBP_REFRESH_TOKEN...")
    saved_token = config.GBP_REFRESH_TOKEN
    config.GBP_REFRESH_TOKEN = None
    config.SAFE_MODE = False

    result = gbp.publish(video_path, platform_meta)

    config.GBP_REFRESH_TOKEN = saved_token
    config.SAFE_MODE = saved_safe_mode

    if not result.get("ok") and result.get("error"):
        print(f"✅ Check 2 passed: missing token returned ok=False — {result['error']}")
    else:
        print(f"❌ Check 2 failed: expected ok=False, got: {result}")
        all_ok = False

    # ── Full publish (non-SAFE_MODE only) ─────────────────────────────────────
    if not config.SAFE_MODE:
        log.info("Step 3: publishing to Google Business Profile...")
        result = gbp.publish(video_path, platform_meta)
        print()
        if result["ok"]:
            print(f"✅ Published! post_id={result['post_id']}")
        else:
            print(f"❌ Publish failed: {result['error']}")
            all_ok = False
    else:
        print()
        print("ℹ️  SAFE_MODE — skipping real publish (set SAFE_MODE=0 to test full flow).")

    print()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
