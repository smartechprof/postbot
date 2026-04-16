"""
Video publishing bot — main entry point.

Downloads the next scheduled video from Google Drive and publishes it
to all configured platforms (or a single platform via --platform).

Usage:
    python3 main.py                        # publish to all platforms
    python3 main.py --platform telegram    # publish to one platform only
    python3 main.py --dry-run              # same as SAFE_MODE=1
"""

import argparse
import logging
import os
import sys
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("main")

# ── Load environment ──────────────────────────────────────────────────────────
_ENV_PRODUCTION = "/etc/igbot.env"
_ENV_LOCAL      = os.path.join(os.path.dirname(__file__), ".env.test")


def _load_env_file(path: str) -> None:
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

# ── Imports after env ─────────────────────────────────────────────────────────
import config
import drive
import metadata as md
import scheduler
from platforms import telegram, instagram, facebook, youtube, linkedin
from platforms import x as x_platform
from utils.converter import delete_temp

# ── Platform registry ─────────────────────────────────────────────────────────
# Order determines publish sequence.
# Each entry: (name, module, metadata_key)
PLATFORMS = [
    ("telegram",  telegram,    "telegram"),
    ("instagram", instagram,   "instagram"),
    ("youtube",   youtube,     "youtube"),
    ("linkedin",  linkedin,    "linkedin"),
    ("x",         x_platform,  "x"),
    ("facebook",  facebook,    "facebook"),
]

PLATFORM_NAMES = [name for name, _, _ in PLATFORMS]


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Video publishing bot")
    parser.add_argument(
        "--platform",
        choices=PLATFORM_NAMES,
        default=None,
        help="Publish to a single platform only.",
    )
    parser.add_argument(
        "--video",
        default=None,
        help="Specific video ID to publish (e.g. 001), overrides scheduler.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enable SAFE_MODE (no actual publishing).",
    )
    return parser.parse_args()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_summary(results: list[dict]) -> None:
    """Print a formatted results table."""
    col_w = 12
    print()
    print(f"  {'Platform':<{col_w}}  {'Status':<8}  Detail")
    print(f"  {'─' * col_w}  {'─' * 8}  {'─' * 40}")
    for r in results:
        icon   = "✅" if r["ok"] else "❌"
        detail = r.get("detail", "")
        print(f"  {icon} {r['platform']:<{col_w - 2}}  {'OK' if r['ok'] else 'FAILED':<8}  {detail}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # --dry-run overrides env
    if args.dry_run:
        os.environ["SAFE_MODE"] = "1"
        # Reload config value in-process
        config.SAFE_MODE = True

    log.info("=== main.py | SAFE_MODE=%s ===", config.SAFE_MODE)

    # ── Determine active platforms ────────────────────────────────────────────
    skip = {p.strip().lower() for p in os.environ.get("SKIP_PLATFORMS", "").split(",") if p.strip()}
    if skip:
        log.info("Skipping platforms: %s", ", ".join(sorted(skip)))

    if args.platform:
        active = [(n, m, k) for n, m, k in PLATFORMS if n == args.platform]
        log.info("Single-platform mode: %s", args.platform)
    else:
        active = [(n, m, k) for n, m, k in PLATFORMS if n not in skip]
        log.info("Publishing to %d platform(s).", len(active))

    # ── Resolve next video ────────────────────────────────────────────────────
    if args.video:
        video_id = args.video
        log.info("Manual mode — publishing video '%s' (scheduler bypassed).", video_id)
        drive_map = {}
    else:
        log.info("Fetching video list from Google Drive...")
        if config.SAFE_MODE:
            all_ids = md.list_video_ids()
            log.info("SAFE_MODE — using metadata IDs as video list: %s", all_ids)
            drive_map = {}
        else:
            drive_files = drive.list_mov_files()
            if not drive_files:
                log.error("No video files found in Drive folder '%s'.", config.DRIVE_FOLDER_NAME)
                sys.exit(1)
            drive_map = {f["video_id"]: f["name"] for f in drive_files}
            all_ids   = sorted(drive_map.keys())

        video_id = scheduler.get_next_video_id(all_ids)
        log.info("Next video to publish: %s", video_id)

    # Resolve actual filename: use Drive map if available, else fall back to "001.mov"
    if config.SAFE_MODE:
        file_name = f"{video_id}.mov"
    else:
        file_name = drive_map.get(video_id, f"{video_id}.mov")

    # ── Download video ────────────────────────────────────────────────────────
    if config.SAFE_MODE:
        video_path = os.path.join(tempfile.gettempdir(), file_name)
        open(video_path, "wb").close()
        log.info("SAFE_MODE — using dummy file: %s", video_path)
    else:
        log.info("Downloading '%s' from Google Drive...", file_name)
        try:
            video_path = drive.download_file(file_name)
        except Exception as exc:
            log.error("Drive download failed: %s", exc)
            sys.exit(1)
        log.info("Downloaded to: %s", video_path)

    # ── Load and validate metadata ────────────────────────────────────────────
    log.info("Loading metadata for video '%s'...", video_id)
    try:
        md.get_metadata(video_id)   # triggers validation + warnings
    except KeyError as exc:
        log.error("Metadata error: %s", exc)
        sys.exit(1)

    # ── Publish to each platform ──────────────────────────────────────────────
    results = []
    all_ok  = True

    for platform_name, module, meta_key in active:
        log.info("── Publishing to %s...", platform_name)

        platform_meta = md.get_platform_data(video_id, meta_key)
        if platform_meta is None:
            log.warning("No metadata for platform '%s' — skipping.", platform_name)
            results.append({
                "platform": platform_name,
                "ok":       False,
                "detail":   "no metadata",
            })
            all_ok = False
            continue

        try:
            result = module.publish(video_path, platform_meta)
        except Exception as exc:
            log.error("Unexpected error on %s: %s", platform_name, exc)
            result = {"ok": False, "error": str(exc)}

        ok     = result.get("ok", False)
        detail = ""
        if ok:
            # Collect whichever ID key the platform returns
            for id_key in ("post_id", "video_id", "message_id"):
                if result.get(id_key) is not None:
                    detail = f"{id_key}={result[id_key]}"
                    break
            if not detail:
                detail = "published" if not config.SAFE_MODE else "safe_mode"
        else:
            detail = result.get("error", "unknown error")
            all_ok = False

        log.info("%s → %s | %s", platform_name, "OK" if ok else "FAILED", detail)
        results.append({"platform": platform_name, "ok": ok, "detail": detail})

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_summary(results)

    # ── Always mark published after the loop, even if some platforms failed ─────
    if config.SAFE_MODE:
        log.info("SAFE_MODE — would mark '%s' as published in state.txt.", video_id)
    else:
        scheduler.mark_published(video_id)
        log.info("Marked '%s' as published.", video_id)
    if not all_ok:
        log.warning("One or more platforms failed for '%s'.", video_id)

    # ── Cleanup original downloaded file ─────────────────────────────────────
    delete_temp(video_path)

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
