"""
Threads video publisher using Threads API (graph.threads.net).

Publish flow:
  1. compress_for_telegram(video_path) → 720p mp4 in /tmp/
  2. Copy compressed file to _MEDIA_DIR (/var/www/tmp-media/)
  3. Form public URL: _MEDIA_BASE_URL/{filename}
  4. Create media container: POST /{user_id}/threads
       media_type=VIDEO, video_url=public_url, text=post text
  5. Poll container status every 15s (timeout 10 min) until status=FINISHED
  6. Publish: POST /{user_id}/threads_publish with creation_id=container_id
  7. Finally: delete file from _MEDIA_DIR, delete local /tmp/ compressed file

Metadata keys (from metadata.json → "threads"):
  text  (str, required) — post text (max 500 chars)
"""

import logging
import os
import shutil
import time

import requests
from typing import Optional

import config
from utils.converter import compress_for_telegram, delete_temp

log = logging.getLogger(__name__)

_API_BASE      = "https://graph.threads.net/v1.0"
_MEDIA_DIR     = "/var/www/tmp-media"
_MEDIA_BASE_URL = "https://media.botshub.io/tmp-media"
_POLL_INTERVAL = 15    # seconds between status checks
_POLL_TIMEOUT  = 600   # 10 minutes max wait for FINISHED


def _create_container(user_id: str, token: str, video_url: str, text: str) -> str:
    """
    Create a Threads media container for a video post.

    Returns container_id.
    Raises RuntimeError on failure.
    """
    url = f"{_API_BASE}/{user_id}/threads"
    resp = requests.post(
        url,
        params={
            "media_type":   "VIDEO",
            "video_url":    video_url,
            "text":         text,
            "access_token": token,
        },
        timeout=60,
    )

    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(f"Invalid JSON from container create: {resp.text[:200]}")

    container_id = payload.get("id")
    if not container_id:
        raise RuntimeError(f"No container ID in response: {payload}")

    log.info("Threads container created | container_id=%s", container_id)
    return container_id


def _poll_container(container_id: str, token: str) -> None:
    """
    Poll container status every _POLL_INTERVAL seconds until FINISHED.

    Raises RuntimeError if status is ERROR or timeout is exceeded.
    """
    url      = f"{_API_BASE}/{container_id}"
    deadline = time.time() + _POLL_TIMEOUT
    status   = ""

    while time.time() < deadline:
        resp = requests.get(
            url,
            params={"fields": "status,error_message", "access_token": token},
            timeout=30,
        )

        if not resp.ok:
            raise RuntimeError(f"Status poll HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            payload = resp.json()
        except ValueError:
            raise RuntimeError(f"Invalid JSON from status poll: {resp.text[:200]}")

        status = payload.get("status", "")
        log.info("Threads container status: %s", status)

        if status == "FINISHED":
            return
        if status == "ERROR":
            error_msg = payload.get("error_message", "unknown error")
            raise RuntimeError(f"Container processing failed: {error_msg}")

        time.sleep(_POLL_INTERVAL)

    raise RuntimeError(f"Container not FINISHED within {_POLL_TIMEOUT}s (last status: {status})")


def _publish_container(user_id: str, token: str, container_id: str) -> str:
    """
    Publish a FINISHED container to the Threads feed.

    Returns the published thread ID.
    Raises RuntimeError on failure.
    """
    url = f"{_API_BASE}/{user_id}/threads_publish"
    resp = requests.post(
        url,
        params={"creation_id": container_id, "access_token": token},
        timeout=60,
    )

    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError(f"Invalid JSON from threads_publish: {resp.text[:200]}")

    thread_id = payload.get("id")
    if not thread_id:
        raise RuntimeError(f"No thread ID in publish response: {payload}")

    log.info("Threads publish OK | thread_id=%s", thread_id)
    return thread_id


def publish(video_path: str, metadata: dict) -> dict:
    """
    Compress, serve via public URL, create Threads video post, then clean up.

    Args:
        video_path: Absolute local path to the source video file.
        metadata:   Platform dict from metadata.json → "threads".
                    Expected keys:
                        text  (str) — post text (max 500 chars).

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    text    = metadata.get("text", "")
    user_id = config.THREADS_USER_ID
    token   = config.THREADS_ACCESS_TOKEN

    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "Threads publish | user=%s | text=(%d chars)",
        user_id, len(text),
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to Threads.")
        return {"ok": True, "post_id": None}

    if not token:
        return {"ok": False, "error": "THREADS_ACCESS_TOKEN is not set."}
    if not user_id:
        return {"ok": False, "error": "THREADS_USER_ID is not set."}

    compressed_path = compress_for_telegram(video_path)
    filename        = os.path.basename(compressed_path)
    media_path: Optional[str] = None

    try:
        # Step 1 — copy to public media dir
        media_path = os.path.join(_MEDIA_DIR, filename)
        shutil.copy(compressed_path, media_path)
        log.info("Copied to media dir: %s", filename)

        video_url = f"{_MEDIA_BASE_URL}/{filename}"

        # Step 2 — create container + poll + publish (with retry)
        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                container_id = _create_container(user_id, token, video_url, text)
                _poll_container(container_id, token)
                thread_id = _publish_container(user_id, token, container_id)
                return {"ok": True, "post_id": thread_id}

            except Exception as exc:
                last_error = str(exc)
                log.error("Threads publish failed (attempt %d/%d): %s",
                          attempt + 1, config.MAX_RETRY_ATTEMPTS, last_error)

            if attempt < config.MAX_RETRY_ATTEMPTS - 1:
                wait_time = 2 ** attempt * 10
                log.warning("Retrying in %ds...", wait_time)
                time.sleep(wait_time)

        return {"ok": False, "error": last_error}

    finally:
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
                log.info("Deleted from media dir: %s", filename)
            except OSError as exc:
                log.warning("Failed to delete from media dir %s: %s", filename, exc)

        if compressed_path != video_path:
            delete_temp(compressed_path)
