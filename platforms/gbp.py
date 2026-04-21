"""
Google Business Profile publisher using the My Business API v4.

Publish flow:
  1. Authenticate via OAuth2 refresh token → short-lived access token.
  2. Upload compressed video to the GBP media endpoint (multipart) → get sourceUrl.
  3. Create a STANDARD localPost referencing that sourceUrl.

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
from typing import Optional

import config
from utils.converter import compress_for_telegram, delete_temp

log = logging.getLogger(__name__)

_SCOPES      = ["https://www.googleapis.com/auth/business.manage"]
_API_BASE    = "https://mybusiness.googleapis.com/v4"
_UPLOAD_BASE = "https://mybusiness.googleapis.com/upload/v4"


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


def _upload_media(upload_path: str, token: str, account_id: str, location_id: str) -> str:
    """
    Upload a video file to the GBP media endpoint.

    Returns the sourceUrl of the created media item.
    Raises RuntimeError on failure.
    """
    url = (
        f"{_UPLOAD_BASE}/accounts/{account_id}/locations/{location_id}/media"
        "?uploadType=multipart"
    )
    metadata = '{"mediaFormat": "VIDEO"}'

    with open(upload_path, "rb") as video_fh:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": ("metadata", metadata, "application/json; charset=UTF-8"),
                "media":    (os.path.basename(upload_path), video_fh, "video/mp4"),
            },
            timeout=300,
        )

    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(f"Invalid JSON from media upload: {response.text[:200]}")

    source_url = payload.get("sourceUrl") or payload.get("googleUrl")
    if not source_url:
        raise RuntimeError(f"No sourceUrl in media response: {payload}")

    log.info("GBP media upload OK | sourceUrl=%s", source_url)
    return source_url


def _create_post(token: str, account_id: str, location_id: str, summary: str,
                 source_url: str, call_to_action_url: Optional[str]) -> str:
    """
    Create a STANDARD localPost with an attached video.

    Returns the created post name (resource ID).
    Raises RuntimeError on failure.
    """
    url = f"{_API_BASE}/accounts/{account_id}/locations/{location_id}/localPosts"

    body: dict = {
        "topicType": "STANDARD",
        "summary":   summary,
        "media": [{"mediaFormat": "VIDEO", "sourceUrl": source_url}],
    }
    if call_to_action_url:
        body["callToAction"] = {
            "actionType": "LEARN_MORE",
            "url":        call_to_action_url,
        }

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        json=body,
        timeout=60,
    )

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
    Publish a video post to Google Business Profile.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "gmaps").
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
        "GBP publish | account=%s | location=%s | summary=%s",
        account_id, location_id, f"({len(summary)} chars)",
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

    upload_path = compress_for_telegram(video_path)

    try:
        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                token      = _get_access_token()
                source_url = _upload_media(upload_path, token, account_id, location_id)
                post_name  = _create_post(
                    token, account_id, location_id, summary, source_url, call_to_action_url
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
        if upload_path != video_path:
            delete_temp(upload_path)
