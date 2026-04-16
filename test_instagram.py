"""
Test script: download 001.mov from Google Drive and publish to Instagram as a Reel.

Environment variables are loaded from /etc/igbot.env if the file exists,
then .env.test fills in any missing vars for local testing.

Run with SAFE_MODE=1 (default in .env.test) to skip actual publishing.
Set SAFE_MODE=0 in the env file to do a real publish.
"""

import logging
import os
import sys
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("test_instagram")

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
from platforms import instagram

VIDEO_ID  = "001"
FILE_NAME = f"{VIDEO_ID}.mov"


def main() -> None:
    log.info("=== test_instagram.py | SAFE_MODE=%s ===", config.SAFE_MODE)

    # 1. Download video from Google Drive (skipped in SAFE_MODE — uses dummy file)
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

    # 2. Read metadata
    log.info("Step 2: reading metadata for video '%s'...", VIDEO_ID)
    try:
        platform_meta = metadata.get_platform_data(VIDEO_ID, "instagram")
    except KeyError as exc:
        log.error("Metadata error: %s", exc)
        sys.exit(1)

    if platform_meta is None:
        log.error("No 'instagram' key in metadata for video '%s'.", VIDEO_ID)
        sys.exit(1)

    log.info("Metadata: %s", platform_meta)

    # 3. Publish to Instagram
    log.info("Step 3: publishing to Instagram...")
    result = instagram.publish(video_path, platform_meta)

    # 4. Print result
    print()
    if result["ok"]:
        if config.SAFE_MODE:
            print("✅ SAFE_MODE — would have published to Instagram (no actual request made).")
        else:
            print(f"✅ Published! post_id={result['post_id']}")
    else:
        print(f"❌ Failed: {result['error']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
