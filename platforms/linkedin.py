"""
LinkedIn video publisher using LinkedIn API v2.

Upload flow:
  1. Resolve the authenticated member's URN via /v2/userinfo.
  2. Initialize upload session → receive upload URLs and video URN.
  3. Upload video binary in chunks (one PUT per chunk URL).
  4. Finalize upload.
  5. Create a post referencing the uploaded video URN.

Metadata keys (from metadata.json → "linkedin"):
  text  (str, required) — post commentary text
  title (str, optional) — video title shown in the post
"""

import logging
import os
import time

import requests

import config
from utils.converter import compress_for_platform, delete_temp

log = logging.getLogger(__name__)

_API_BASE    = "https://api.linkedin.com"
_LI_VERSION  = "202602"
_CHUNK_SIZE  = 4 * 1024 * 1024   # 4 MB per chunk


def _headers(token: str, content_type: str = "application/json") -> dict:
    return {
        "Authorization":              f"Bearer {token}",
        "LinkedIn-Version":           _LI_VERSION,
        "X-Restli-Protocol-Version":  "2.0.0",
        "Content-Type":               content_type,
    }


def _get_person_urn(token: str) -> str:
    """Resolve the authenticated member's URN via /v2/userinfo."""
    resp = requests.get(
        f"{_API_BASE}/v2/userinfo",
        headers=_headers(token),
        timeout=15,
    )
    data = resp.json()
    if not resp.ok or "sub" not in data:
        raise RuntimeError(f"Failed to resolve LinkedIn person URN: {data}")
    urn = f"urn:li:person:{data['sub']}"
    log.info("LinkedIn person URN: %s", urn)
    return urn


def _initialize_upload(token: str, person_urn: str, file_size: int) -> dict:
    """
    Step 1 — Initialize upload session.
    Returns the full value dict with uploadInstructions, video URN, uploadToken.
    """
    resp = requests.post(
        f"{_API_BASE}/rest/videos?action=initializeUpload",
        headers=_headers(token),
        json={
            "initializeUploadRequest": {
                "owner":           person_urn,
                "fileSizeBytes":   file_size,
                "uploadCaptions":  False,
                "uploadThumbnail": False,
            }
        },
        timeout=30,
    )
    data = resp.json()
    if not resp.ok or "value" not in data:
        raise RuntimeError(f"LinkedIn initializeUpload failed: {data}")
    return data["value"]


def _upload_chunks(upload_instructions: list, video_path: str) -> list:
    """
    Step 2 — Upload video binary in chunks.
    Returns list of ETags received from each chunk upload.
    """
    etags = []
    with open(video_path, "rb") as fh:
        for i, instruction in enumerate(upload_instructions):
            url        = instruction["uploadUrl"]
            first_byte = instruction["firstByte"]
            last_byte  = instruction["lastByte"]
            chunk_size = last_byte - first_byte + 1

            fh.seek(first_byte)
            chunk = fh.read(chunk_size)

            log.info(
                "Uploading chunk %d/%d (%d bytes)...",
                i + 1, len(upload_instructions), len(chunk),
            )
            resp = requests.put(
                url,
                headers={"Content-Type": "application/octet-stream"},
                data=chunk,
                timeout=120,
            )
            if not resp.ok:
                raise RuntimeError(
                    f"Chunk {i + 1} upload failed (HTTP {resp.status_code}): {resp.text}"
                )
            etag = resp.headers.get("ETag", resp.headers.get("etag", ""))
            etags.append(etag)
            log.info("Chunk %d uploaded. ETag: %s", i + 1, etag)

    return etags


def _finalize_upload(token: str, video_urn: str, upload_token: str, etags: list) -> None:
    """Step 3 — Finalize the upload."""
    resp = requests.post(
        f"{_API_BASE}/rest/videos?action=finalizeUpload",
        headers=_headers(token),
        json={
            "finalizeUploadRequest": {
                "video":            video_urn,
                "uploadToken":      upload_token,
                "uploadedPartIds":  etags,
            }
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"LinkedIn finalizeUpload failed (HTTP {resp.status_code}): {resp.text}")
    log.info("Upload finalized for %s", video_urn)


def _create_post(token: str, person_urn: str, video_urn: str, text: str, title: str) -> str:
    """Step 4 — Create a LinkedIn post referencing the uploaded video. Returns post URN."""
    body = {
        "author":         person_urn,
        "commentary":     text,
        "visibility":     "PUBLIC",
        "distribution": {
            "feedDistribution":            "MAIN_FEED",
            "targetEntities":              [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "title": title,
                "id":    video_urn,
            }
        },
        "lifecycleState":            "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    resp = requests.post(
        f"{_API_BASE}/rest/posts",
        headers=_headers(token),
        json=body,
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"LinkedIn post creation failed (HTTP {resp.status_code}): {resp.text}")

    # Post URN is returned in the X-RestLi-Id header
    post_id = resp.headers.get("x-restli-id", resp.headers.get("X-RestLi-Id", ""))
    log.info("LinkedIn post created: %s", post_id)
    return post_id


# ── Public API ────────────────────────────────────────────────────────────────

def _publish_to(author_urn: str, video_path: str, text: str, title: str, token: str) -> dict:
    """Upload video and create a post for one author URN. Returns result dict."""
    file_size = os.path.getsize(video_path)
    log.info("LinkedIn upload for %s | file=%s | size=%d bytes", author_urn, os.path.basename(video_path), file_size)

    upload_value = _initialize_upload(token, author_urn, file_size)
    video_urn           = upload_value["video"]
    upload_token        = upload_value["uploadToken"]
    upload_instructions = upload_value["uploadInstructions"]

    log.info("Video URN: %s | %d chunk(s) to upload", video_urn, len(upload_instructions))

    etags = _upload_chunks(upload_instructions, video_path)
    _finalize_upload(token, video_urn, upload_token, etags)
    post_id = _create_post(token, author_urn, video_urn, text, title)
    return {"ok": True, "post_id": post_id}


def publish(video_path: str, metadata: dict) -> dict:
    """
    Publish a video to LinkedIn.

    Always publishes as the authenticated person. If LI_ORGANIZATION_ID is set,
    also publishes a second post as the organization. Returns ok=True if at
    least one post succeeds.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "linkedin").
                    Expected keys:
                        text  (str) — post commentary.
                        title (str, optional) — video title.

    Returns:
        {"ok": True,  "post_id": str}   on success (person post ID).
        {"ok": False, "error": str}      on failure.
    """
    text   = metadata.get("text", "")
    title  = metadata.get("title", "")
    token  = config.LI_ACCESS_TOKEN
    org_id = config.LI_ORGANIZATION_ID

    if not token:
        return {"ok": False, "error": "LI_ACCESS_TOKEN is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "LinkedIn publish | file=%s | text=%r",
        os.path.basename(video_path), text[:80],
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to LinkedIn (person).")
        return {"ok": True, "post_id": None}

    person_urn  = _get_person_urn(token)
    upload_path = compress_for_platform(video_path)

    # Publish to person URN only.
    log.info("── Publishing to LinkedIn as person (%s)...", person_urn)
    try:
        last_error = "unknown error"
        for attempt in range(3):
            try:
                r = _publish_to(person_urn, upload_path, text, title, token)
                log.info("LinkedIn person OK | post_id=%s", r["post_id"])
                return {"ok": True, "post_id": r["post_id"]}
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = str(exc)
                log.error("LinkedIn person network error (attempt %d/3): %s", attempt + 1, last_error)
                if attempt < 2:
                    log.info("Retrying in 30 seconds...")
                    time.sleep(30)
            except Exception as exc:
                log.error("LinkedIn person failed: %s", exc)
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": last_error}
    finally:
        if upload_path != video_path:
            delete_temp(upload_path)

    # ── Organization publishing (disabled) ────────────────────────────────────
    # Publishing to an organization URN requires the Community Management API,
    # which is a restricted LinkedIn product and must be explicitly approved by
    # LinkedIn for the app. Without that permission the API returns 403.
    # Re-enable this block once Community Management API access is granted.
    #
    # if org_id:
    #     org_urn = f"urn:li:organization:{org_id}"
    #     log.info("── Publishing to LinkedIn as org (%s)...", org_urn)
    #     try:
    #         r = _publish_to(org_urn, video_path, text, title, token)
    #         log.info("LinkedIn org OK | post_id=%s", r["post_id"])
    #     except Exception as exc:
    #         log.error("LinkedIn org failed: %s", exc)
