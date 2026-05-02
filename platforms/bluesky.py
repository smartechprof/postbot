"""
Bluesky video publisher using AT Protocol (raw HTTP requests).

Upload flow:
  1. createSession — authenticate with handle + app password, get access token + DID.
  2. getServiceAuth — get scoped auth token for video upload service.
  3. uploadVideo — upload MP4 binary to video.bsky.app, receive job status.
  4. Poll job status — wait for video processing to complete.
  5. createRecord — create post with video embed referencing the processed blob.

Metadata keys (from metadata.json → "bluesky"):
  text  (str, required) — post text (max 300 graphemes)
"""

import json
import logging
import os
import time

import requests

import config
from utils.converter import compress_for_platform, delete_temp

log = logging.getLogger(__name__)

_PDS_HOST = "https://bsky.social"
_VIDEO_HOST = "https://video.bsky.app"
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 300  # 5 minutes


def _create_session(handle: str, app_password: str) -> dict:
    """
    Authenticate and return session dict with accessJwt and did.

    Raises RuntimeError on failure.
    """
    resp = requests.post(
        f"{_PDS_HOST}/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": app_password},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Auth failed (HTTP {resp.status_code}): {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Auth returned invalid JSON: {resp.text[:200]}")

    if "accessJwt" not in data or "did" not in data:
        raise RuntimeError(f"Auth response missing required fields: {list(data.keys())}")

    log.info("Bluesky auth OK | did=%s", data["did"])
    return data


def _get_pds_did(did: str) -> str:
    """
    Resolve the user's PDS service DID via plc.directory.

    Returns the PDS DID string (e.g. did:web:stropharia.us-west.host.bsky.network).
    Raises RuntimeError on failure.
    """
    resp = requests.get(f"https://plc.directory/{did}", timeout=15)
    if not resp.ok:
        raise RuntimeError(f"PLC directory lookup failed (HTTP {resp.status_code}): {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"PLC directory returned invalid JSON: {resp.text[:200]}")

    for service in data.get("service", []):
        if service.get("id") == "#atproto_pds":
            endpoint = service.get("serviceEndpoint", "")
            # Extract hostname from URL, build did:web:hostname
            host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
            pds_did = f"did:web:{host}"
            log.info("Resolved PDS DID: %s", pds_did)
            return pds_did

    raise RuntimeError(f"No atproto_pds service found in DID document: {data}")


def _get_service_auth(access_token: str, pds_did: str) -> str:
    """
    Get a scoped service auth token for video upload using PDS DID.

    Returns the auth token string.
    Raises RuntimeError on failure.
    """
    resp = requests.get(
        f"{_PDS_HOST}/xrpc/com.atproto.server.getServiceAuth",
        params={
            "aud": pds_did,
            "lxm": "com.atproto.repo.uploadBlob",
            "exp": int(time.time()) + 1800,  # 30 minutes
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Service auth failed (HTTP {resp.status_code}): {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Service auth returned invalid JSON: {resp.text[:200]}")

    token = data.get("token")
    if not token:
        raise RuntimeError(f"Service auth response missing token: {data}")

    log.info("Service auth token obtained.")
    return token


def _upload_video(service_token: str, did: str, video_path: str) -> dict:
    """
    Upload video binary to video.bsky.app.

    Returns job status dict.
    Raises RuntimeError on failure.
    """
    file_size = os.path.getsize(video_path)
    log.info("Uploading video (%d bytes) to Bluesky...", file_size)

    with open(video_path, "rb") as fh:
        resp = requests.post(
            f"{_VIDEO_HOST}/xrpc/app.bsky.video.uploadVideo",
            params={"did": did, "name": os.path.basename(video_path)},
            headers={
                "Authorization": f"Bearer {service_token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(file_size),
            },
            data=fh,
            timeout=300,
        )

    if not resp.ok:
        raise RuntimeError(f"Video upload failed (HTTP {resp.status_code}): {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Video upload returned invalid JSON: {resp.text[:200]}")

    job_status = data.get("jobStatus") or data
    log.info("Video upload accepted | jobId=%s", job_status.get("jobId", "unknown"))
    return job_status


def _poll_video_job(access_token: str, job_id: str) -> dict:
    """
    Poll video processing job until completed or failed.

    Returns the final job status dict (with blob reference).
    Raises RuntimeError on failure or timeout.
    """
    deadline = time.time() + _POLL_TIMEOUT

    while time.time() < deadline:
        resp = requests.get(
            f"{_VIDEO_HOST}/xrpc/app.bsky.video.getJobStatus",
            params={"jobId": job_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Job poll failed (HTTP {resp.status_code}): {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"Job poll returned invalid JSON: {resp.text[:200]}")

        job = data.get("jobStatus", data)
        state = job.get("state", "")
        log.info("Video processing state: %s", state)

        if state == "JOB_STATE_COMPLETED":
            blob = job.get("blob")
            if not blob:
                raise RuntimeError(f"Job completed but no blob reference: {job}")
            return job

        if state == "JOB_STATE_FAILED":
            error = job.get("error", "unknown error")
            raise RuntimeError(f"Video processing failed: {error}")

        time.sleep(_POLL_INTERVAL)

    raise RuntimeError(f"Video processing not completed within {_POLL_TIMEOUT}s")


def _get_video_dimensions(video_path: str) -> dict:
    """
    Get video width/height via ffprobe.

    Returns {"width": int, "height": int} or empty dict on failure.
    """
    import subprocess
    try:
        probe = subprocess.check_output([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            video_path,
        ])
        streams = json.loads(probe).get("streams", [])
        if streams:
            w = streams[0].get("width")
            h = streams[0].get("height")
            if w and h:
                log.info("Video dimensions: %sx%s", w, h)
                return {"width": w, "height": h}
    except Exception as exc:
        log.warning("ffprobe failed, posting without aspect ratio: %s", exc)
    return {}


def _create_post(access_token: str, did: str, text: str, blob: dict,
                 aspect_ratio: dict) -> str:
    """
    Create a Bluesky post with video embed.

    Returns the post URI.
    Raises RuntimeError on failure.
    """
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "embed": {
            "$type": "app.bsky.embed.video",
            "video": blob,
        },
        "langs": ["en"],
    }

    if aspect_ratio:
        record["embed"]["aspectRatio"] = aspect_ratio

    resp = requests.post(
        f"{_PDS_HOST}/xrpc/com.atproto.repo.createRecord",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": record,
        },
        timeout=30,
    )

    if not resp.ok:
        raise RuntimeError(f"Post creation failed (HTTP {resp.status_code}): {resp.text[:200]}")

    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"Post creation returned invalid JSON: {resp.text[:200]}")

    post_uri = data.get("uri", "")
    log.info("Bluesky post created | uri=%s", post_uri)
    return post_uri


# ── Public API ────────────────────────────────────────────────────────────────

def publish(video_path: str, metadata: dict) -> dict:
    """
    Compress, upload, and publish a video post on Bluesky.

    Args:
        video_path: Absolute local path to the source video file.
        metadata:   Platform dict from metadata.json → "bluesky".
                    Expected keys:
                        text  (str) — post text (max 300 graphemes).

    Returns:
        {"ok": True,  "post_id": str}   on success.
        {"ok": False, "error": str}      on failure.
    """
    text   = metadata.get("text", "")
    handle = config.BLUESKY_HANDLE
    app_pw = config.BLUESKY_APP_PASSWORD

    if not handle:
        return {"ok": False, "error": "BLUESKY_HANDLE is not set."}
    if not app_pw:
        return {"ok": False, "error": "BLUESKY_APP_PASSWORD is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "Bluesky publish | handle=%s | text=(%d chars)",
        handle, len(text),
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual publish to Bluesky.")
        return {"ok": True, "post_id": None}

    compressed_path = compress_for_platform(video_path)

    try:
        last_error = "unknown error"
        for attempt in range(config.MAX_RETRY_ATTEMPTS):
            try:
                # Step 1 — authenticate
                session = _create_session(handle, app_pw)
                access_token = session["accessJwt"]
                did = session["did"]

                # Step 2 — resolve PDS DID and get service auth
                pds_did = _get_pds_did(did)
                service_token = _get_service_auth(access_token, pds_did)

                # Step 3 — upload video
                job_status = _upload_video(service_token, did, compressed_path)

                # Step 4 — poll until processing completes
                job_id = job_status.get("jobId")
                if job_id:
                    final_job = _poll_video_job(access_token, job_id)
                    blob = final_job["blob"]
                else:
                    blob = job_status.get("blob")
                    if not blob:
                        raise RuntimeError(f"No jobId or blob in upload response: {job_status}")

                # Step 5 — get aspect ratio and create post
                dimensions = _get_video_dimensions(compressed_path)
                post_uri = _create_post(access_token, did, text, blob, dimensions)

                return {"ok": True, "post_id": post_uri}

            except Exception as exc:
                last_error = str(exc)
                log.error("Bluesky publish failed (attempt %d/%d). Check credentials and network.",
                          attempt + 1, config.MAX_RETRY_ATTEMPTS)
                if attempt < config.MAX_RETRY_ATTEMPTS - 1:
                    wait_time = 2 ** attempt * 10
                    log.warning("Retrying in %ds...", wait_time)
                    time.sleep(wait_time)

        return {"ok": False, "error": last_error}

    finally:
        if compressed_path != video_path:
            delete_temp(compressed_path)
