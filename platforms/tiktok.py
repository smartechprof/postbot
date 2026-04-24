"""
TikTok video publisher using TikTok Content Posting API v2.

Publish flow:
  1. Refresh access token via refresh_token
  2. Initialize upload: POST /v2/post/publish/video/init/
       → publish_id + upload_url
  3. Upload video binary via PUT to upload_url
  4. Poll publish status every 5s until PUBLISH_COMPLETE (timeout 5 min)

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

import requests

import config

log = logging.getLogger(__name__)

_TOKEN_URL     = "https://open.tiktokapis.com/v2/oauth/token/"
_INIT_URL      = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_STATUS_URL    = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
_POLL_INTERVAL  = 5              # seconds between status checks
_POLL_TIMEOUT   = 300            # 5 minutes max
_MAX_CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB


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
                 disable_duet: bool, disable_stitch: bool, disable_comment: bool) -> tuple[str, str, int]:
    """
    Initialize a video upload via Content Posting API.

    Returns (publish_id, upload_url, chunk_size).
    Raises RuntimeError on failure.
    """
    if file_size <= _MAX_CHUNK_SIZE:
        chunk_size        = file_size
        total_chunk_count = 1
    else:
        chunk_size        = _MAX_CHUNK_SIZE
        total_chunk_count = file_size // _MAX_CHUNK_SIZE

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
                "source":            "FILE_UPLOAD",
                "video_size":        file_size,
                "chunk_size":        chunk_size,
                "total_chunk_count": total_chunk_count,
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

    log.info("TikTok upload initialized | publish_id=%s | chunks=%d", publish_id, total_chunk_count)
    return publish_id, upload_url, chunk_size


def _upload_video(upload_url: str, video_path: str, file_size: int, chunk_size: int) -> None:
    """
    Upload video binary to TikTok's upload URL.

    Sends a single PUT when file fits in one chunk; otherwise streams
    chunk_size-sized pieces with correct Content-Range per chunk.
    The last chunk absorbs any remainder.

    Raises RuntimeError on failure.
    """
    with open(video_path, "rb") as fh:
        offset = 0
        chunk_index = 0
        while offset < file_size:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            end = offset + len(chunk) - 1
            resp = requests.put(
                upload_url,
                headers={
                    "Content-Type":   "video/mp4",
                    "Content-Range":  f"bytes {offset}-{end}/{file_size}",
                    "Content-Length": str(len(chunk)),
                },
                data=chunk,
                timeout=300,
            )
            if not resp.ok:
                raise RuntimeError(
                    f"Video upload chunk {chunk_index} HTTP {resp.status_code}: {resp.text[:300]}"
                )
            log.info("TikTok chunk %d uploaded (bytes %d-%d)", chunk_index, offset, end)
            offset += len(chunk)
            chunk_index += 1

    log.info("TikTok video upload complete (%d bytes, %d chunk(s))", file_size, chunk_index)


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
    Refresh token, upload, and publish a video to TikTok.

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

    token = _refresh_access_token()
    file_size = os.path.getsize(video_path)

    last_error = "unknown error"
    for attempt in range(config.MAX_RETRY_ATTEMPTS):
        try:
            publish_id, upload_url, chunk_size = _init_upload(
                token, file_size, caption, privacy_level,
                disable_duet, disable_stitch, disable_comment,
            )
            _upload_video(upload_url, video_path, file_size, chunk_size)
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
