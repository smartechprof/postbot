import json
import logging
import os
from typing import Optional

import config

log = logging.getLogger(__name__)

_cache: Optional[dict] = None

# ── Platform character limits ─────────────────────────────────────────────────
# Each entry: (platform, field_in_metadata, limit)
PLATFORM_LIMITS = [
    ("instagram",  "caption",     2200),
    ("facebook",   "post",     63206),
    ("youtube",    "title",       100),
    ("youtube",    "description", 5000),
    ("youtube",    "tags",        460),
    ("linkedin",   "post",        3000),
    ("telegram",   "caption",     1024),
    ("tiktok",     "caption",     2200),
    ("pinterest",  "title",       100),
    ("pinterest",  "description", 500),
    ("threads",    "text",        440),
    ("x",          "post",        280),
    ("gmaps",      "summary",     1500),
    ("bluesky",    "text",        300),
]


# ── Internal loader ───────────────────────────────────────────────────────────

def _load() -> dict:
    """Load metadata.json into memory (once) and return it."""
    global _cache
    if _cache is not None:
        return _cache

    path = config.METADATA_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"Metadata file not found: '{path}'")

    with open(path, encoding="utf-8") as fh:
        _cache = json.load(fh)

    log.info("Loaded metadata from '%s' (%d video(s))", path, len(_cache))
    return _cache


# ── Validation ────────────────────────────────────────────────────────────────

def validate_metadata(video_id: str) -> list[dict]:
    """
    Check all text fields for video_id against PLATFORM_LIMITS.

    Logs a WARNING for each field that exceeds its limit but does NOT raise
    or block publishing. Returns a list of result dicts, one per checked field:

        {
            "platform": str,
            "field":    str,
            "length":   int,
            "limit":    int,
            "ok":       bool,
        }

    Fields absent from the metadata are skipped silently.
    """
    data = _load()
    video_meta = data.get(video_id, {})
    results = []

    for platform, field, limit in PLATFORM_LIMITS:
        platform_data = video_meta.get("platforms", {}).get(platform)
        if not platform_data:
            continue
        value = platform_data.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            if platform == "youtube" and field == "tags":
                # YouTube wraps multi-word tags in quotes, adding 2 chars per such tag
                length = sum(len(str(tag)) + (2 if " " in str(tag) else 0) for tag in value)
            else:
                length = sum(len(str(tag)) for tag in value)
        else:
            length = len(str(value))
        ok = length <= limit
        results.append({
            "platform": platform,
            "field":    field,
            "length":   length,
            "limit":    limit,
            "ok":       ok,
        })
        if not ok:
            log.warning(
                "Metadata %s / %s / %s: %d chars exceeds limit of %d (over by %d)",
                video_id, platform, field, length, limit, length - limit,
            )

    return results


# ── Public API ────────────────────────────────────────────────────────────────

def get_metadata(video_id: str) -> dict:
    """
    Return the full metadata dict for video_id.

    Raises KeyError if the video ID is not found.
    Also runs validate_metadata() and logs any limit warnings.
    """
    data = _load()
    if video_id not in data:
        raise KeyError(
            f"Video '{video_id}' not found in metadata. "
            f"Available IDs: {sorted(data.keys())}"
        )
    validate_metadata(video_id)
    return data[video_id]


def get_platform_data(video_id: str, platform: str) -> Optional[dict]:
    """
    Return the platform-specific metadata dict for video_id, or None if absent.
    """
    meta = get_metadata(video_id)
    return meta.get("platforms", {}).get(platform)


def list_video_ids() -> list[str]:
    """Return a sorted list of all video IDs in metadata.json."""
    return sorted(_load().keys())
