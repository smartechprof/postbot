"""
Threads video publisher using Threads API (graph.threads.net).

Publish flow:
  1. compress_for_telegram(video_path) → 720p mp4 in /tmp/
  2. Upload compressed video to Google Drive (temp file) → uploaded_file_id
  3. Open public access: anyone with link → reader
  4. Create media container: POST /{user_id}/threads
       media_type=VIDEO, video_url=public_drive_url, text=post text
  5. Poll container status every 15s (timeout 10 min) until status=FINISHED
  6. Publish: POST /{user_id}/threads_publish with creation_id=container_id
  7. Finally: close Drive access, delete Drive temp file, delete local temp

Metadata keys (from metadata.json → "threads"):
  text  (str, required) — post text (max 500 chars)
"""

import logging
import os
import time

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from typing import Optional

import config
from utils.converter import compress_for_telegram, delete_temp

log = logging.getLogger(__name__)

_DRIVE_SCOPES  = ["https://www.googleapis.com/auth/drive"]
_API_BASE      = "https://graph.threads.net/v1.0"
_POLL_INTERVAL = 15    # seconds between status checks
_POLL_TIMEOUT  = 600   # 10 minutes max wait for FINISHED


def _get_drive_service():
    """Build and return an authenticated Drive service client with full drive scope."""
    creds = Credentials(
        token=None,
        refresh_token=config.DRIVE_REFRESH_TOKEN,
        client_id=config.DRIVE_WEB_CLIENT_ID,
        client_secret=config.DRIVE_WEB_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_DRIVE_SCOPES,
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def _upload_to_drive(drive_svc, local_path: str) -> str:
    """
    Upload a local video file to Google Drive as a temporary file.

    Returns the uploaded file's Drive ID.
    Raises RuntimeError on failure.
    """
    file_name = os.path.basename(local_path)
    media     = MediaFileUpload(local_path, mimetype="video/mp4", resumable=True)
    file_meta = {"name": f"threads_tmp_{file_name}"}

    request  = drive_svc.files().create(body=file_meta, media_body=media, fields="id")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("Drive upload progress: %d%%", int(status.progress() * 100))

    file_id = response.get("id")
    if not file_id:
        raise RuntimeError(f"Drive upload returned no file ID: {response}")

    log.info("Drive upload OK | file_id=%s", file_id)
    return file_id


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
            "media_type":  "VIDEO",
            "video_url":   video_url,
            "text":        text,
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
    Compress, upload to Drive, create Threads video post, then clean up.

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
    if not config.DRIVE_REFRESH_TOKEN:
        return {"ok": False, "error": "DRIVE_REFRESH_TOKEN is not set."}

    compressed_path  = compress_for_telegram(video_path)
    drive_svc        = _get_drive_service()
    uploaded_file_id: Optional[str] = None

    try:
        # Step 1 — upload to Drive
        uploaded_file_id = _upload_to_drive(drive_svc, compressed_path)

        # Step 2 — open public access
        drive_svc.permissions().create(
            fileId=uploaded_file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        log.info("Drive: opened public access for %s", uploaded_file_id)

        video_url = f"https://drive.google.com/uc?id={uploaded_file_id}&export=download"

        # Step 3 — create container + poll + publish (with retry)
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
        if uploaded_file_id:
            try:
                drive_svc.permissions().delete(
                    fileId=uploaded_file_id,
                    permissionId="anyoneWithLink",
                ).execute()
                log.info("Drive: closed public access for %s", uploaded_file_id)
            except Exception as exc:
                log.warning("Drive: failed to close access for %s: %s", uploaded_file_id, exc)

            try:
                drive_svc.files().delete(fileId=uploaded_file_id).execute()
                log.info("Drive: deleted temp file %s", uploaded_file_id)
            except Exception as exc:
                log.warning("Drive: failed to delete temp file %s: %s", uploaded_file_id, exc)

        if compressed_path != video_path:
            delete_temp(compressed_path)
