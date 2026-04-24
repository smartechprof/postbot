"""
TikTok video publisher using TikTok Content Posting API v2.

Publish flow:
  1. Refresh access token via refresh_token
  2. compress_for_telegram(video_path) → 720p mp4 in /tmp/
  3. Initialize upload: POST /v2/post/publish/video/init/
       → publish_id + upload_url
  4. Upload video binary via PUT to upload_url
  5. Poll publish status every 5s until PUBLISH_COMPLETE (timeout 5 min)
  6. Clean up /tmp/ compressed file

Metadata keys (from metadata.json → "tiktok"):
  caption        (str, required) — post caption (max 2200 chars)
  privacy_level  (str, optional) — PUBLIC_TO_EVERYONE | MUTUAL_CAN_VIEW |
                                    SELF_ONLY  (default: PUBLIC_TO_EVERYONE)
  disable_duet   (bool, optional) — default False
  disable_stitch (bool, optional) — default False
  disable_comment (bool, optional) — default False
"""

import logging
import os
import time
from typing import Optional

import requests

import config
from utils.converter import compress_for_telegram, delete_temp

log = logging.getLogger(__name__)

_TOKEN_URL     = "https://open.tiktokapis.com/v2/oauth/token/"
_INIT_URL      = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_STATUS_URL    = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
_POLL_INTERVAL = 5     # seconds between status checks
_POLL_TIMEOUT  = 300   # 5 minutes max


def _refresh_access_token() -> str:
    """
    Exchange refresh token for a fresh access token.

    Returns the new access token string.
    Raises RuntimeError on failure.
    """
    resp = requests.post(
        _TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    config.TIKTOK_CLIENT_KEY,
            "client_secret": config.TIKTOK_CLIENT_SECRET,
            "grant_type":    "refresh_token",
            "refresh_token": config.TIKTOK_REFRESH_TOKEN,
        },
        timeout=30,
    )

    if not resp.ok:
        raise RuntimeError(f"Token refresh HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Invalid JSON from token refresh: {resp.text[:200]}")

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in refresh response: {data}")

    log.info("TikTok access token refreshed (expires in %ss)", data.get("expires_in", "?"))
    return token


def _init_upload(token: str, file_size: int, caption: str, privacy_level: str,
                 disable_duet: bool, disable_stitch: bool, disable_comment: bool) -> tuple[str, str]:
    """
    Initialize a video upload via Content Posting API.

    Returns (publish_id, upload_url).
    Raises RuntimeError on failure.
    """
    resp = requests.post(
        _INIT_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "title":           caption,
                "privacy_level":   privacy_level,
                "disable_duet":    disable_duet,
                "disable_stitch":  disable_stitch,
                "disable_comment": disable_comment,
            },
            "source_info": {
                "source":          "FILE_UPLOAD",
                "video_size":      file_size,
                "chunk_size":      file_size,
                "total_chunk_count": 1,
            },
        },
        timeout=30,
    )

    if not resp.ok:
        raise RuntimeError(f"Init upload HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Invalid JSON from init upload: {resp.text[:200]}")

    err = data.get("error", {})
    if err.get("code", "ok") != "ok":
        raise RuntimeError(f"Init upload error: {err.get('message', data)}")

    publish_id = data.get("data", {}).get("publish_id")
    upload_url = data.get("data", {}).get("upload_url")

    if not publish_id or not upload_url:
        raise RuntimeError(f"Missing publish_id or upload_url: {data}")

    log.info("TikTok upload initialized | publish_id=%s", publish_id)
    return publish_id, upload_url


def _upload_video(upload_url: str, video_path: str, file_size: int) -> None:
    """
    Upload video binary to TikTok's upload URL (single chunk).

    Raises RuntimeError on failure.
    """
    with open(video_path, "rb") as fh:
        resp = requests.put(
            upload_url,
            headers={
                "Content-Type":  "video/mp4",
                "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                "Content-Length": str(file_size),
            },
            data=fh,
            timeout=300,
        )

    if not resp.ok:
        raise RuntimeError(f"Video upload HTTP {resp.status_code}: {resp.text[:300]}")

    log.info("TikTok video uploaded (%d bytes)", file_size)


def _poll_status(publish_id: str, token: str) -> None:
    """
    Poll publish status until PUBLISH_COMPLETE.

    Raises RuntimeError on FAILED status or timeout.
    """
    deadline = time.time() + _POLL_TIMEOUT
    status   = ""

    while time.time() < deadline:
        resp = requests.post(
            _STATUS_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )

        if not resp.ok:
            raise RuntimeError(f"Status poll HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Invalid JSON from status poll: {resp.text[:200]}")

        err = data.get("error", {})
        if err.get("code", "ok") != "ok":
            raise RuntimeError(f"Status poll error: {err.get('message', data)}")

        status = data.get("data", {}).get("status", "")
        log.info("TikTok publish status: %s", status)

        if status == "PUBLISH_COMPLETE":
            return
        if status in ("FAILED", "SPAM_RISK_TOO_MANY_POSTS", "SPAM_RISK_USER_BANNED_FROM_POSTING"):
            fail_reason = data.get("data", {}).get("fail_reason", status)
            raise RuntimeError(f"Publish failed: {fail_reason}")

        time.sleep(_POLL_INTERVAL)

    raise RuntimeError(f"Publish not complete within {_POLL_TIMEOUT}s (last status: {status})")


def publish(video_path: str, metadata: dict) -> dict:
    """
    Refresh token, compress, upload, and publish a video to TikTok.

    Args:
        video_path: Absolute local path to the source video file.
        metadata:   Platform dict from metadata.json → "tiktok".
                    Expected keys:
                        caption        (str)  — post text (max 2200 chars).
                        privacy_level  (str, optional) — PUBLIC_TO_EVERYONE | MUTUAL_CAN_VIEW | SELF_ONLY.
                        disable_duet   (bool, optional)
                        disable_stitch (bool, optional)
                        disable_comment (bool, optional)

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    caption         = metadata.get("caption", "")
    privacy_level   = metadata.get("privacy_level", "PUBLIC_TO_EVERYONE")
    disable_duet    = bool(metadata.get("disable_duet", False))
    disable_stitch  = bool(metadata.get("disable_stitch", False))
    disable_comment = bool(metadata.get("disable_comment", False))

    log.info("TikTok publish | caption=(%d chars) | privacy=%s", len(caption), privacy_level)

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to TikTok.")
        return {"ok": True, "post_id": None}

    if not config.TIKTOK_CLIENT_KEY:
        return {"ok": False, "error": "TIKTOK_CLIENT_KEY is not set."}
    if not config.TIKTOK_CLIENT_SECRET:
        return {"ok": False, "error": "TIKTOK_CLIENT_SECRET is not set."}
    if not config.TIKTOK_REFRESH_TOKEN:
        return {"ok": False, "error": "TIKTOK_REFRESH_TOKEN is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    compressed_path: Optional[str] = None

    try:
        token = _refresh_access_token()

        compressed_path = compress_for_telegram(video_path)
        file_size = os.path.getsize(compressed_path)

        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                publish_id, upload_url = _init_upload(
                    token, file_size, caption, privacy_level,
                    disable_duet, disable_stitch, disable_comment,
                )
                _upload_video(upload_url, compressed_path, file_size)
                _poll_status(publish_id, token)
                return {"ok": True, "post_id": publish_id}

            except Exception as exc:
                last_error = str(exc)
                log.error("TikTok publish failed (attempt %d/%d): %s",
                          attempt + 1, config.MAX_RETRY_ATTEMPTS, last_error)

            if attempt < config.MAX_RETRY_ATTEMPTS - 1:
                wait_time = 2 ** attempt * 10
                log.warning("Retrying in %ds...", wait_time)
                time.sleep(wait_time)

        return {"ok": False, "error": last_error}

    finally:
        if compressed_path and compressed_path != video_path:
            delete_temp(compressed_path)
