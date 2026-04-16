"""
Standalone metadata validation script.

Loads metadata.json and checks all text fields for every video ID against
platform character limits defined in metadata.PLATFORM_LIMITS.

Usage:
    python3 validate.py

Exit code 0 if all fields are within limits, 1 if any warnings exist.
"""

import os
import sys

# ── Bootstrap env (same priority as test scripts) ────────────────────────────
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
    _load_env_file(_ENV_PRODUCTION)
if os.path.exists(_ENV_LOCAL):
    _load_env_file(_ENV_LOCAL)

# ── Import after env ──────────────────────────────────────────────────────────
import metadata as md


def main() -> None:
    video_ids = md.list_video_ids()

    if not video_ids:
        print("No videos found in metadata.json.")
        sys.exit(0)

    total_ok       = 0
    total_warnings = 0

    print(f"\nValidating {len(video_ids)} video(s) across all platforms...\n")
    print(f"{'─' * 62}")

    for video_id in video_ids:
        results = md.validate_metadata(video_id)

        if not results:
            print(f"  {video_id}  (no platform data)\n")
            continue

        for r in results:
            platform = r["platform"]
            field    = r["field"]
            length   = r["length"]
            limit    = r["limit"]
            ok       = r["ok"]

            if ok:
                icon = "✅"
                note = "OK"
                total_ok += 1
            else:
                icon = "⚠️ "
                note = f"EXCEEDS by {length - limit} chars"
                total_warnings += 1

            print(f"  {icon} {video_id} / {platform} / {field}: {length}/{limit} — {note}")

        print()

    print(f"{'─' * 62}")
    print(f"  Total OK: {total_ok}   Warnings: {total_warnings}\n")

    sys.exit(1 if total_warnings else 0)


if __name__ == "__main__":
    main()
