"""
Google Business Profile publisher using the My Business API v4.

Publish flow:
  1. Authenticate via OAuth2 refresh token → short-lived access token.
  2. Create a STANDARD localPost with optional video via public Google Drive URL.

Metadata keys (from metadata.json → "gmaps"):
  summary            (str, required) — post body text (max 1500 chars)
  drive_file_id      (str, optional) — Google Drive file ID for video media;
                                       if absent, post is published as text only
  call_to_action_url (str, optional) — URL for LEARN_MORE button
"""

import logging
import time

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from typing import Optional

import config

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


def _create_post(token: str, account_id: str, location_id: str, summary: str,
                 drive_file_id: Optional[str], call_to_action_url: Optional[str]) -> str:
    """
    Create a STANDARD localPost, optionally with a video via public Drive URL.

    Returns the created post name (resource ID).
    Raises RuntimeError on failure.
    """
    url = f"{_API_BASE}/accounts/{account_id}/locations/{location_id}/localPosts"

    body: dict = {
        "topicType": "STANDARD",
        "summary":   summary,
    }

    if drive_file_id:
        source_url = f"https://drive.google.com/uc?id={drive_file_id}&export=download"
        body["media"] = [{"mediaFormat": "VIDEO", "sourceUrl": source_url}]
        log.info("GBP media | drive_file_id=%s", drive_file_id)

    if call_to_action_url:
        body["callToAction"] = {
            "actionType": "LEARN_MORE",
            "url":        call_to_action_url,
        }

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })

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
    Publish a text (+ optional video) post to Google Business Profile.

    Args:
        video_path: Absolute local path to the source video file (not used
                    directly — video is referenced via Drive URL if drive_file_id
                    is present in metadata).
        metadata:   Platform dict from metadata.json → "gmaps".
                    Expected keys:
                        summary            (str) — post body text.
                        drive_file_id      (str, optional) — Google Drive file ID.
                        call_to_action_url (str, optional) — LEARN_MORE button URL.

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    summary            = metadata.get("summary", "")
    drive_file_id      = metadata.get("drive_file_id")
    call_to_action_url = metadata.get("call_to_action_url")
    account_id         = config.GBP_ACCOUNT_ID
    location_id        = config.GBP_LOCATION_ID

    log.info(
        "GBP publish | account=%s | location=%s | summary=(%d chars) | media=%s",
        account_id, location_id, len(summary),
        f"drive:{drive_file_id}" if drive_file_id else "none (text only)",
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
    if not account_id:
        return {"ok": False, "error": "GBP_ACCOUNT_ID is not set."}
    if not location_id:
        return {"ok": False, "error": "GBP_LOCATION_ID is not set."}

    drive_svc = None
    if drive_file_id:
        drive_svc = _get_drive_service()
        drive_svc.permissions().create(
            fileId=drive_file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        log.info("Drive: opened public access for %s", drive_file_id)

    try:
        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                token     = _get_access_token()
                post_name = _create_post(
                    token, account_id, location_id, summary, drive_file_id, call_to_action_url
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
        if drive_file_id and drive_svc:
            try:
                drive_svc.permissions().delete(
                    fileId=drive_file_id,
                    permissionId="anyoneWithLink",
                ).execute()
                log.info("Drive: closed public access for %s", drive_file_id)
            except Exception as exc:
                log.warning("Drive: failed to close access for %s: %s", drive_file_id, exc)
