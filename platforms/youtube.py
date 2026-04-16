"""
YouTube video publisher using YouTube Data API v3.

Upload flow:
  1. Authenticate via OAuth2 refresh token.
  2. Upload video directly as "public" using resumable upload.

Metadata keys (from metadata.json → "youtube"):
  title       (str, required) — video title
  description (str, required) — video description
  tags        (list[str], optional) — list of tags
"""

import logging
import os

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import config

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_service():
    """Build and return an authenticated YouTube service client."""
    creds = Credentials(
        token=None,
        refresh_token=config.YT_REFRESH_TOKEN,
        client_id=config.YT_WEB_CLIENT_ID,
        client_secret=config.YT_WEB_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=_SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def publish(video_path: str, metadata: dict) -> dict:
    """
    Upload a video to YouTube, initially as unlisted, then set to public.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "youtube").
                    Expected keys:
                        title       (str) — video title.
                        description (str) — video description.
                        tags        (list[str], optional) — video tags.

    Returns:
        {"ok": True,  "video_id": str}  on success.
        {"ok": False, "error": str}      on failure.
    """
    title       = metadata.get("title", "")
    description = metadata.get("description", "")
    tags        = metadata.get("tags", [])

    if not config.YT_REFRESH_TOKEN:
        return {"ok": False, "error": "YT_REFRESH_TOKEN is not set."}
    if not config.YT_WEB_CLIENT_ID:
        return {"ok": False, "error": "YT_WEB_CLIENT_ID is not set."}
    if not config.YT_WEB_CLIENT_SECRET:
        return {"ok": False, "error": "YT_WEB_CLIENT_SECRET is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "YouTube publish | file=%s | title=%r",
        os.path.basename(video_path), title,
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual upload to YouTube.")
        return {"ok": True, "video_id": None}

    try:
        service = _get_service()

        log.info("Uploading video to YouTube (public)...")
        body = {
            "snippet": {
                "title":       title,
                "description": description,
                "tags":        tags,
                "categoryId":  "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": "public",
            },
        }

        media = MediaFileUpload(
            video_path,
            mimetype="video/quicktime",
            resumable=True,
            chunksize=8 * 1024 * 1024,
        )

        insert_request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = insert_request.next_chunk()
            if status:
                log.info("Upload progress: %d%%", int(status.progress() * 100))

        video_id = response["id"]
        log.info("Upload complete. video_id=%s", video_id)
        return {"ok": True, "video_id": video_id}

    except Exception as exc:
        log.error("YouTube publish failed: %s", exc)
        return {"ok": False, "error": str(exc)}
