"""
Instagram Reels publisher using Meta Graph API.

Publishing flow:
  1. Create a media container (resumable upload if no video_url in metadata,
     otherwise direct URL upload).
  2. Poll the container status until FINISHED (or ERROR).
  3. Publish the container via /{ig-user-id}/media_publish.

Metadata keys (from metadata.json → "instagram"):
  caption   (str, required)  — caption text for the Reel
  video_url (str, optional)  — publicly accessible video URL;
                               if absent, the local file is uploaded via
                               the resumable upload API
"""

import logging
import os
import time

import requests

import config
from utils.converter import compress_for_platform, delete_temp

log = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v19.0"
_POLL_INTERVAL  = 10   # seconds between status checks
_POLL_MAX_TRIES = 30   # give up after ~5 minutes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _graph_post(endpoint: str, **kwargs) -> dict:
    """POST to Graph API endpoint, return parsed JSON."""
    url = f"{_GRAPH}/{endpoint}"
    resp = requests.post(url, timeout=60, **kwargs)
    return resp.json()


def _graph_get(endpoint: str, params: dict) -> dict:
    """GET from Graph API endpoint, return parsed JSON."""
    url = f"{_GRAPH}/{endpoint}"
    resp = requests.get(url, params=params, timeout=30)
    return resp.json()


def _poll_container(container_id: str, token: str) -> bool:
    """
    Poll container status until FINISHED or ERROR.
    Returns True if FINISHED, False otherwise.
    """
    for attempt in range(1, _POLL_MAX_TRIES + 1):
        data = _graph_get(
            container_id,
            params={"fields": "status_code,status", "access_token": token},
        )
        status_code = data.get("status_code", "")
        log.info("Container %s status: %s (attempt %d/%d)", container_id, status_code, attempt, _POLL_MAX_TRIES)

        if status_code == "FINISHED":
            return True
        if status_code == "ERROR":
            log.error("Container processing error: %s", data.get("status"))
            return False
        if "error" in data:
            log.error("Graph API error while polling: %s", data["error"])
            return False

        time.sleep(_POLL_INTERVAL)

    log.error("Container did not finish within the polling timeout.")
    return False


def _create_container_url(video_url: str, caption: str, token: str, user_id: str) -> dict:
    """Create a Reels container using a public video URL."""
    log.info("Creating Instagram container via video_url...")
    return _graph_post(
        f"{user_id}/media",
        data={
            "media_type":   "REELS",
            "video_url":    video_url,
            "caption":      caption,
            "access_token": token,
        },
    )


def _create_container_resumable(video_path: str, caption: str, token: str, user_id: str) -> dict:
    """
    Create a Reels container using resumable upload (for local files).

    Step A — initialise upload session → get container id + upload URI.
    Step B — POST binary video to upload URI.
    """
    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)

    # Step A: initialise
    log.info("Initialising resumable upload for '%s' (%d bytes)...", file_name, file_size)
    init_data = _graph_post(
        f"{user_id}/media",
        data={
            "media_type":   "REELS",
            "upload_type":  "resumable",
            "caption":      caption,
            "access_token": token,
        },
    )

    if "error" in init_data:
        return init_data

    container_id = init_data.get("id")
    upload_uri   = init_data.get("uri")

    if not upload_uri:
        return {"error": {"message": "No upload URI returned from Graph API."}}

    log.info("Upload URI received. Uploading video binary...")

    # Step B: upload binary
    with open(video_path, "rb") as fh:
        upload_resp = requests.post(
            upload_uri,
            headers={
                "Authorization":  f"OAuth {token}",
                "offset":         "0",
                "file_size":      str(file_size),
                "Content-Type":   "application/octet-stream",
            },
            data=fh,
            timeout=300,
        )

    upload_result = upload_resp.json()
    log.info("Upload response: %s", upload_result)

    if not upload_result.get("success"):
        return {"error": {"message": f"Video upload failed: {upload_result}"}}

    return {"id": container_id}


def _publish_container(container_id: str, token: str, user_id: str) -> dict:
    """Publish a finished container."""
    log.info("Publishing container %s...", container_id)
    return _graph_post(
        f"{user_id}/media_publish",
        data={
            "creation_id":  container_id,
            "access_token": token,
        },
    )


# ── Public API ────────────────────────────────────────────────────────────────

def publish(video_path: str, metadata: dict) -> dict:
    """
    Publish a video to Instagram as a Reel.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "instagram").
                    Expected keys:
                        caption   (str) — Reel caption.
                        video_url (str, optional) — public URL; if absent,
                                                    local file is uploaded.

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    caption   = metadata.get("caption", "")
    video_url = metadata.get("video_url")
    user_id   = config.IG_USER_ID
    token     = config.IG_PAGE_TOKEN

    if not user_id:
        return {"ok": False, "error": "IG_USER_ID is not set."}
    if not token:
        return {"ok": False, "error": "IG_PAGE_TOKEN is not set."}
    if not video_url and not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "Instagram publish | user=%s | file=%s | caption=%r",
        user_id, os.path.basename(video_path), caption[:80],
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to Instagram.")
        return {"ok": True, "post_id": None}

    upload_path = None
    if not video_url:
        upload_path = compress_for_platform(video_path)

    try:
        last_error = "unknown error"
        for attempt in range(3):
            # Step 1: create container
            if video_url:
                container_data = _create_container_url(video_url, caption, token, user_id)
            else:
                container_data = _create_container_resumable(upload_path, caption, token, user_id)

            if "error" in container_data:
                last_error = container_data["error"].get("message", str(container_data["error"]))
                log.error("Failed to create container (attempt %d/3): %s", attempt + 1, last_error)
                if attempt < 2:
                    log.info("Retrying in 30 seconds...")
                    time.sleep(30)
                continue

            container_id = container_data.get("id")
            if not container_id:
                last_error = "No container ID returned from Graph API."
                log.error("(attempt %d/3): %s", attempt + 1, last_error)
                if attempt < 2:
                    log.info("Retrying in 30 seconds...")
                    time.sleep(30)
                continue

            log.info("Container created: %s", container_id)

            # Step 2: poll until ready
            if not _poll_container(container_id, token):
                last_error = f"Container {container_id} did not reach FINISHED state."
                log.error("(attempt %d/3): %s", attempt + 1, last_error)
                if attempt < 2:
                    log.info("Retrying in 30 seconds...")
                    time.sleep(30)
                continue

            # Step 3: publish
            publish_data = _publish_container(container_id, token, user_id)

            if "error" in publish_data:
                last_error = publish_data["error"].get("message", str(publish_data["error"]))
                log.error("Failed to publish container (attempt %d/3): %s", attempt + 1, last_error)
                if attempt < 2:
                    log.info("Retrying in 30 seconds...")
                    time.sleep(30)
                continue

            post_id = publish_data.get("id")
            log.info("Instagram Reel published! post_id=%s", post_id)
            return {"ok": True, "post_id": post_id}

        return {"ok": False, "error": last_error}

    finally:
        if upload_path and upload_path != video_path:
            delete_temp(upload_path)
