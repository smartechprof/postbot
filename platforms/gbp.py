"""
Google Business Profile publisher using the My Business API v4.

Publish flow:
  1. compress_for_telegram(video_path) → 720p mp4 in /tmp/
  2. Upload compressed video to Google Drive (temp file) → get uploaded_file_id
  3. Open public access: anyone with link → reader
  4. Create STANDARD localPost with sourceUrl referencing the uploaded file
  5. Close public access (always, even on failure)
  6. Delete temp file from Drive (always)
  7. Delete local compressed file (always)

Metadata keys (from metadata.json → "gmaps"):
  summary            (str, required) — post body text (max 1500 chars)
  call_to_action_url (str, optional) — URL for LEARN_MORE button
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

_SCOPES       = ["https://www.googleapis.com/auth/business.manage"]
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_API_BASE     = "https://mybusiness.googleapis.com/v4"


def _get_access_token() -> str:
    """Return a fresh OAuth2 access token using the configured refresh token."""
    creds = Credentials(
        token=None,
        refresh_token=config.GBP_REFRESH_TOKEN,
        client_id=config.YT_WEB_CLIENT_ID,
        client_secret=config.YT_WEB_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_SCOPES,
    )
    creds.refresh(Request())
    return creds.token


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
    """
    file_name = os.path.basename(local_path)
    media     = MediaFileUpload(local_path, mimetype="video/mp4", resumable=True)
    file_meta = {"name": f"gbp_tmp_{file_name}"}

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


def _create_post(token: str, account_id: str, location_id: str, summary: str,
                 uploaded_file_id: str, call_to_action_url: Optional[str]) -> str:
    """
    Create a STANDARD localPost with video via public Drive URL.

    Returns the created post name (resource ID).
    Raises RuntimeError on failure.
    """
    url        = f"{_API_BASE}/accounts/{account_id}/locations/{location_id}/localPosts"
    source_url = f"https://drive.google.com/uc?id={uploaded_file_id}&export=download"

    body: dict = {
        "topicType": "STANDARD",
        "summary":   summary,
        "media":     [{"mediaFormat": "VIDEO", "sourceUrl": source_url}],
    }

    if call_to_action_url:
        body["callToAction"] = {
            "actionType": "LEARN_MORE",
            "url":        call_to_action_url,
        }

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    response = session.post(url, json=body, timeout=60, verify=True)

    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(f"Invalid JSON from localPosts: {response.text[:200]}")

    post_name = payload.get("name")
    if not post_name:
        raise RuntimeError(f"No post name in response: {payload}")

    return post_name


def publish(video_path: str, metadata: dict) -> dict:
    """
    Compress, upload to Drive, publish to GBP, then clean up.

    Args:
        video_path: Absolute local path to the source video file.
        metadata:   Platform dict from metadata.json → "gmaps".
                    Expected keys:
                        summary            (str) — post body text.
                        call_to_action_url (str, optional) — LEARN_MORE button URL.

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    summary            = metadata.get("summary", "")
    call_to_action_url = metadata.get("call_to_action_url")
    account_id         = config.GBP_ACCOUNT_ID
    location_id        = config.GBP_LOCATION_ID

    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "GBP publish | account=%s | location=%s | summary=(%d chars)",
        account_id, location_id, len(summary),
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to Google Business Profile.")
        return {"ok": True, "post_id": None}

    if not config.GBP_REFRESH_TOKEN:
        return {"ok": False, "error": "GBP_REFRESH_TOKEN is not set."}
    if not config.YT_WEB_CLIENT_ID:
        return {"ok": False, "error": "YT_WEB_CLIENT_ID is not set."}
    if not config.YT_WEB_CLIENT_SECRET:
        return {"ok": False, "error": "YT_WEB_CLIENT_SECRET is not set."}
    if not config.DRIVE_REFRESH_TOKEN:
        return {"ok": False, "error": "DRIVE_REFRESH_TOKEN is not set."}
    if not account_id:
        return {"ok": False, "error": "GBP_ACCOUNT_ID is not set."}
    if not location_id:
        return {"ok": False, "error": "GBP_LOCATION_ID is not set."}

    compressed_path  = compress_for_telegram(video_path)
    drive_svc        = _get_drive_service()
    uploaded_file_id = None

    try:
        # Step 1 — upload compressed video to Drive
        uploaded_file_id = _upload_to_drive(drive_svc, compressed_path)

        # Step 2 — open public access
        drive_svc.permissions().create(
            fileId=uploaded_file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        log.info("Drive: opened public access for %s", uploaded_file_id)

        # Step 3 — create localPost with retry
        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                token     = _get_access_token()
                post_name = _create_post(
                    token, account_id, location_id, summary,
                    uploaded_file_id, call_to_action_url,
                )
                log.info("GBP OK | post_name=%s", post_name)
                return {"ok": True, "post_id": post_name}

            except Exception as exc:
                last_error = str(exc)
                log.error("GBP publish failed (attempt %d/%d): %s",
                          attempt + 1, config.MAX_RETRY_ATTEMPTS, last_error)

            if attempt < config.MAX_RETRY_ATTEMPTS - 1:
                wait_time = 2 ** attempt * 10
                log.warning("Retrying in %ds...", wait_time)
                time.sleep(wait_time)

        return {"ok": False, "error": last_error}

    finally:
        # Always: close public access, delete Drive temp file, delete local temp
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
