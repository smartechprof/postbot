"""
Facebook Page video publisher using Meta Graph API.

Uploads a video directly to a Facebook Page via POST /{page-id}/videos.
The API handles encoding and publishing in a single step.

Metadata keys (from metadata.json → "facebook"):
  message     (str, required) — post text accompanying the video
  title       (str, optional) — video title shown in the post
  description (str, optional) — video description
"""

import logging
import os
import time

import requests

import config
from utils.converter import compress_for_platform, delete_temp

log = logging.getLogger(__name__)

_GRAPH_VIDEO = "https://graph-video.facebook.com/v19.0"


def publish(video_path: str, metadata: dict) -> dict:
    """
    Publish a video to the configured Facebook Page.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "facebook").
                    Expected keys:
                        message     (str) — post caption.
                        title       (str, optional) — video title.
                        description (str, optional) — video description.

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    message     = metadata.get("post", "")
    title       = metadata.get("title", "")
    description = metadata.get("description", "")
    page_id     = config.FB_PAGE_ID
    token       = config.FB_PAGE_TOKEN

    if not page_id:
        return {"ok": False, "error": "FB_PAGE_ID is not set."}
    if not token:
        return {"ok": False, "error": "FB_PAGE_TOKEN is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "Facebook publish | page=%s | file=%s | post=%s",
        page_id, os.path.basename(video_path), f"post ({len(message)} chars)",
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to Facebook.")
        return {"ok": True, "post_id": None}

    url         = f"{_GRAPH_VIDEO}/{page_id}/videos"
    upload_path = compress_for_platform(video_path)

    try:
        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                with open(upload_path, "rb") as video_fh:
                    response = requests.post(
                        url,
                        data={
                            "access_token": token,
                            "description":  message,
                            "title":        title,
                        },
                        files={"source": (os.path.basename(upload_path), video_fh, "video/mp4")},
                        timeout=300,
                    )

                if not response.ok:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    log.error("Facebook API error (attempt %d/3): %s", attempt + 1, last_error)
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    last_error = f"Invalid JSON response: {response.text[:200]}"
                    log.error("Facebook JSON error (attempt %d/3): %s", attempt + 1, last_error)
                    continue

                if payload.get("id"):
                    post_id = payload["id"]
                    log.info("Facebook OK | post_id=%s", post_id)
                    return {"ok": True, "post_id": post_id}

                last_error = payload.get("error", {}).get("message", response.text)
                log.error("Facebook API error (attempt %d/3): %s", attempt + 1, last_error)

            except requests.RequestException as exc:
                last_error = str(exc)
                log.error("Facebook request failed (attempt %d/3): %s", attempt + 1, last_error)

            if attempt < config.MAX_RETRY_ATTEMPTS - 1:
                wait_time = 2 ** attempt * 10
                log.warning("Retrying in %ds...", wait_time)
                time.sleep(wait_time)

        return {"ok": False, "error": last_error}

    finally:
        if upload_path != video_path:
            delete_temp(upload_path)
