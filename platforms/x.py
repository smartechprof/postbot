"""
X (Twitter) video publisher using OAuth 1.0a + Twitter API v1.1/v2.

Upload flow:
  1. INIT   — register upload, receive media_id.
  2. APPEND — upload video in 4 MB chunks.
  3. FINALIZE — tell Twitter the upload is complete.
  4. POLL   — wait for async video processing if required.
  5. TWEET  — create tweet via API v2 referencing the media_id.

Metadata keys (from metadata.json → "x"):
  text  (str, required) — tweet text (max 280 chars)
"""

import logging
import os
import time

from requests_oauthlib import OAuth1Session

import config
from utils.converter import compress_for_platform, delete_temp

log = logging.getLogger(__name__)

_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
_TWEET_URL  = "https://api.twitter.com/2/tweets"
_CHUNK_SIZE = 4 * 1024 * 1024   # 4 MB
_POLL_INTERVAL  = 5             # seconds between processing status checks
_POLL_MAX_TRIES = 24            # give up after ~2 minutes


def _session() -> OAuth1Session:
    """Return an authenticated OAuth 1.0a session."""
    return OAuth1Session(
        client_key=config.X_API_KEY,
        client_secret=config.X_API_SECRET,
        resource_owner_key=config.X_ACCESS_TOKEN,
        resource_owner_secret=config.X_ACCESS_TOKEN_SECRET,
    )


def _init_upload(session: OAuth1Session, file_size: int) -> str:
    """INIT phase — register the upload and return media_id_string."""
    resp = session.post(
        _UPLOAD_URL,
        data={
            "command":      "INIT",
            "media_type":   "video/quicktime",
            "total_bytes":  file_size,
            "media_category": "tweet_video",
        },
        timeout=30,
    )
    data = resp.json()
    if not resp.ok or "media_id_string" not in data:
        raise RuntimeError(f"INIT failed (HTTP {resp.status_code}): {data}")
    media_id = data["media_id_string"]
    log.info("INIT OK. media_id=%s", media_id)
    return media_id


def _append_chunks(session: OAuth1Session, media_id: str, video_path: str) -> None:
    """APPEND phase — upload video binary in 4 MB chunks."""
    file_size = os.path.getsize(video_path)
    total_chunks = (file_size + _CHUNK_SIZE - 1) // _CHUNK_SIZE

    with open(video_path, "rb") as fh:
        for segment_index in range(total_chunks):
            chunk = fh.read(_CHUNK_SIZE)
            log.info(
                "APPEND chunk %d/%d (%d bytes)...",
                segment_index + 1, total_chunks, len(chunk),
            )
            resp = session.post(
                _UPLOAD_URL,
                data={
                    "command":        "APPEND",
                    "media_id":       media_id,
                    "segment_index":  segment_index,
                },
                files={"media": chunk},
                timeout=120,
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(
                    f"APPEND chunk {segment_index} failed (HTTP {resp.status_code}): {resp.text}"
                )

    log.info("All chunks uploaded.")


def _finalize_upload(session: OAuth1Session, media_id: str) -> dict:
    """FINALIZE phase — signal upload complete, return response data."""
    resp = session.post(
        _UPLOAD_URL,
        data={"command": "FINALIZE", "media_id": media_id},
        timeout=30,
    )
    data = resp.json()
    if not resp.ok:
        raise RuntimeError(f"FINALIZE failed (HTTP {resp.status_code}): {data}")
    log.info("FINALIZE OK. media_id=%s", media_id)
    return data


def _poll_processing(session: OAuth1Session, media_id: str) -> None:
    """Poll media processing status until SUCCEEDED or FAILED."""
    for attempt in range(1, _POLL_MAX_TRIES + 1):
        resp = session.get(
            _UPLOAD_URL,
            params={"command": "STATUS", "media_id": media_id},
            timeout=15,
        )
        data = resp.json()
        info = data.get("processing_info", {})
        state = info.get("state", "")
        progress = info.get("progress_percent", "?")

        log.info(
            "Processing status: %s (%s%%) — attempt %d/%d",
            state, progress, attempt, _POLL_MAX_TRIES,
        )

        if state == "succeeded":
            return
        if state == "failed":
            raise RuntimeError(f"X media processing failed: {data}")

        wait = info.get("check_after_secs", _POLL_INTERVAL)
        time.sleep(wait)

    raise RuntimeError("X media processing timed out.")


def _create_tweet(session: OAuth1Session, text: str, media_id: str) -> str:
    """Create a tweet via API v2, return tweet ID."""
    resp = session.post(
        _TWEET_URL,
        json={
            "text":  text,
            "media": {"media_ids": [media_id]},
        },
        timeout=30,
    )
    data = resp.json()
    if not resp.ok or "data" not in data:
        raise RuntimeError(f"Tweet creation failed (HTTP {resp.status_code}): {data}")
    tweet_id = data["data"]["id"]
    log.info("Tweet created. id=%s", tweet_id)
    return tweet_id


# ── Public API ────────────────────────────────────────────────────────────────

def publish(video_path: str, metadata: dict) -> dict:
    """
    Publish a video to X (Twitter) as a tweet.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "x").
                    Expected keys:
                        text (str) — tweet text (max 280 chars).

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    text = metadata.get("text", "")

    if not config.X_API_KEY:
        return {"ok": False, "error": "X_API_KEY is not set."}
    if not config.X_API_SECRET:
        return {"ok": False, "error": "X_API_SECRET is not set."}
    if not config.X_ACCESS_TOKEN:
        return {"ok": False, "error": "X_ACCESS_TOKEN is not set."}
    if not config.X_ACCESS_TOKEN_SECRET:
        return {"ok": False, "error": "X_ACCESS_TOKEN_SECRET is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "X publish | file=%s | text=%r",
        os.path.basename(video_path), text[:80],
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to X.")
        return {"ok": True, "post_id": None}

    upload_path = compress_for_platform(video_path)

    try:
        session   = _session()
        file_size = os.path.getsize(upload_path)

        media_id      = _init_upload(session, file_size)
        _append_chunks(session, media_id, upload_path)
        finalize_data = _finalize_upload(session, media_id)

        # Poll only if Twitter says processing is pending
        if finalize_data.get("processing_info", {}).get("state") not in (None, "succeeded"):
            _poll_processing(session, media_id)

        tweet_id = _create_tweet(session, text, media_id)
        return {"ok": True, "post_id": tweet_id}

    except Exception as exc:
        log.error("X publish failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    finally:
        if upload_path != video_path:
            delete_temp(upload_path)
