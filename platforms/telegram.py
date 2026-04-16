import json
import logging
import os
import subprocess

import requests

import config
from utils.converter import compress_for_telegram, delete_temp

log = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendVideo"


def publish(video_path: str, metadata: dict) -> dict:
    """
    Publish a video file to the configured Telegram channel.

    Args:
        video_path: Absolute local path to the .mov video file.
        metadata:   Platform dict from get_platform_data(video_id, "telegram").
                    Expected keys:
                        caption (str) — message text sent with the video.

    Returns:
        {"ok": True,  "message_id": int}   on success.
        {"ok": False, "error": str}         on failure.
    """
    caption = metadata.get("caption", "")
    channel = config.TELEGRAM_CHANNEL_ID
    token   = config.TELEGRAM_BOT_TOKEN

    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not set."}
    if not channel:
        return {"ok": False, "error": "TELEGRAM_CHANNEL_ID is not set."}
    if not os.path.exists(video_path):
        return {"ok": False, "error": f"Video file not found: '{video_path}'"}

    log.info(
        "Telegram publish | channel=%s | file=%s | caption=%r",
        channel, os.path.basename(video_path), caption[:80],
    )

    if config.SAFE_MODE:
        log.info("SAFE_MODE — skipping actual send to Telegram.")
        return {"ok": True, "message_id": None}

    mp4_path = compress_for_telegram(video_path)
    url = _API_BASE.format(token=token)

    # Get actual video dimensions via ffprobe
    width, height = None, None
    try:
        probe = subprocess.check_output([
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            mp4_path,
        ])
        streams = json.loads(probe).get("streams", [])
        if streams:
            width  = streams[0].get("width")
            height = streams[0].get("height")
            log.info("Video dimensions: %sx%s", width, height)
    except Exception as exc:
        log.warning("ffprobe failed, sending without dimensions: %s", exc)

    try:
        post_data = {
            "chat_id":            channel,
            "caption":            caption,
            "parse_mode":         "HTML",
            "supports_streaming": True,
        }
        if width:
            post_data["width"] = width
        if height:
            post_data["height"] = height

        with open(mp4_path, "rb") as video_fh:
            response = requests.post(
                url,
                data=post_data,
                files={"video": (os.path.basename(mp4_path), video_fh, "video/mp4")},
                timeout=120,
            )

        payload = response.json()

        if response.ok and payload.get("ok"):
            message_id = payload["result"]["message_id"]
            log.info("Telegram OK | message_id=%s", message_id)
            return {"ok": True, "message_id": message_id}

        error = payload.get("description", response.text)
        log.error("Telegram API error: %s", error)
        return {"ok": False, "error": error}

    except requests.RequestException as exc:
        log.error("Telegram request failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    finally:
        delete_temp(mp4_path)
