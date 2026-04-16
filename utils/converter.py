"""
Video conversion utilities using ffmpeg.

convert_to_mp4(input_path) — converts any supported video format to .mp4.
delete_temp(path)           — deletes a file only if it lives in /tmp/.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

_TMP_DIR       = tempfile.gettempdir()
_VIDEO_FORMATS = {".mov", ".avi", ".mkv", ".webm", ".mp4", ".m4v", ".flv", ".ts"}


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(cmd: list) -> subprocess.CompletedProcess:
    """Run a shell command, capture output."""
    log.debug("ffmpeg cmd: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True)


def _get_longest_dimension(path: str) -> int:
    """Return the longest video dimension (width or height) via ffprobe, or 0 on error."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "v:0",
                path,
            ],
            capture_output=True, text=True,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        if streams:
            w = streams[0].get("width", 0) or 0
            h = streams[0].get("height", 0) or 0
            return max(w, h)
    except Exception as exc:
        log.warning("ffprobe dimension check failed for '%s': %s", os.path.basename(path), exc)
    return 0


def _encode(input_path: str, output_path: str) -> bool:
    """
    Re-encode to H.264/AAC mp4 with faststart and pixel-dimension fix.

    Flags:
      -crf 18          — high quality (visually near-lossless)
      -preset fast     — good speed/size balance
      -movflags +faststart — web-friendly moov atom at front
      -vf scale=...    — rounds dimensions to even numbers (required by libx264)
    """
    result = _run([
        "ffmpeg", "-y",
        "-i",         input_path,
        "-c:v",       "libx264",
        "-crf",       "18",
        "-preset",    "fast",
        "-c:a",       "aac",
        "-movflags",  "+faststart",
        "-vf",        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        output_path,
    ])
    if result.returncode == 0:
        log.info("Encode succeeded: %s", output_path)
        return True
    log.error("Encode failed: %s", result.stderr[-300:])
    return False


def compress_for_telegram(input_path: str) -> str:
    """
    Create a compressed copy of the video sized for Telegram upload.

    Checks the longest dimension first. If already <= 1280 px, returns
    input_path unchanged (no copy created). Otherwise scales down to max
    1280 px on the longest dimension, re-encodes as H.264/AAC mp4 with
    faststart. Output is written to /tmp/ with a '_tg' suffix.

    If ffmpeg is not installed, logs a warning and returns input_path unchanged.

    Args:
        input_path: Absolute path to the source video file.

    Returns:
        Path to the compressed .mp4 temp file, or original path if
        compression was skipped or ffmpeg is unavailable.
    """
    if not _ffmpeg_available():
        log.warning("ffmpeg not found — skipping Telegram compression, using original: %s", input_path)
        return input_path

    longest = _get_longest_dimension(input_path)
    if longest > 0 and longest <= 1280:
        log.info(
            "Telegram: longest dimension %dpx <= 1280px — skipping compression: %s",
            longest, os.path.basename(input_path),
        )
        return input_path

    base_name   = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(_TMP_DIR, f"{base_name}_tg.mp4")

    log.info(
        "Compressing for Telegram '%s' → '%s' (longest=%dpx)...",
        os.path.basename(input_path), output_path, longest,
    )

    result = _run([
        "ffmpeg", "-y",
        "-i",        input_path,
        "-c:v",      "libx264",
        "-crf",      "23",
        "-preset",   "fast",
        "-c:a",      "aac",
        "-movflags", "+faststart",
        "-vf",       (
            "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        ),
        output_path,
    ])

    if result.returncode == 0:
        log.info("Telegram compression succeeded: %s", output_path)
        return output_path

    log.error("Telegram compression failed — using original: %s", result.stderr[-300:])
    return input_path


def compress_for_platform(input_path: str) -> str:
    """
    Create a compressed copy of the video for social platform upload.

    Checks the longest dimension first. If already <= 1920 px, returns
    input_path unchanged (no copy created). Otherwise scales down to max
    1920 px on the longest dimension, re-encodes as H.264/AAC mp4 with
    faststart. Output is written to /tmp/ with a '_1080p' suffix.

    If ffmpeg is not installed, logs a warning and returns input_path unchanged.

    Args:
        input_path: Absolute path to the source video file.

    Returns:
        Path to the compressed .mp4 temp file, or original path if
        compression was skipped or ffmpeg is unavailable.
    """
    if not _ffmpeg_available():
        log.warning("ffmpeg not found — skipping platform compression, using original: %s", input_path)
        return input_path

    longest = _get_longest_dimension(input_path)
    if longest > 0 and longest <= 1920:
        log.info(
            "Platform: longest dimension %dpx <= 1920px — skipping compression: %s",
            longest, os.path.basename(input_path),
        )
        return input_path

    base_name   = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(_TMP_DIR, f"{base_name}_1080p.mp4")

    log.info(
        "Compressing for platform '%s' → '%s' (longest=%dpx)...",
        os.path.basename(input_path), output_path, longest,
    )

    result = _run([
        "ffmpeg", "-y",
        "-i",        input_path,
        "-c:v",      "libx264",
        "-crf",      "23",
        "-preset",   "fast",
        "-c:a",      "aac",
        "-movflags", "+faststart",
        "-vf",       (
            "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease,"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        ),
        output_path,
    ])

    if result.returncode == 0:
        log.info("Platform compression succeeded: %s", output_path)
        return output_path

    log.error("Platform compression failed — using original: %s", result.stderr[-300:])
    return input_path


def convert_to_mp4(input_path: str) -> str:
    """
    Convert a video file to .mp4 and return the output path in /tmp/.

    Always re-encodes using H.264 CRF 18 with faststart and pixel-fix scale
    filter for vertical video compatibility — even if input is already .mp4.

    If ffmpeg is not installed, logs a warning and returns input_path unchanged.

    Args:
        input_path: Absolute path to the source video file.

    Returns:
        Path to the converted .mp4 file, or original path if ffmpeg unavailable.
    """
    if not _ffmpeg_available():
        log.warning("ffmpeg not found — skipping conversion, using original: %s", input_path)
        return input_path

    base_name   = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(_TMP_DIR, f"{base_name}.mp4")

    log.info("Converting '%s' → '%s'...", os.path.basename(input_path), output_path)

    if _encode(input_path, output_path):
        return output_path

    log.error("Conversion failed — using original file: %s", input_path)
    return input_path


def delete_temp(path: str) -> None:
    """
    Delete a file only if it is located inside the system temp directory.

    Silently skips deletion for files outside /tmp/ to avoid accidentally
    removing source files.
    """
    if not path:
        return
    real_path = os.path.realpath(path)
    real_tmp  = os.path.realpath(_TMP_DIR)

    if not real_path.startswith(real_tmp + os.sep) and real_path != real_tmp:
        log.debug("Skipping delete — not a temp file: %s", path)
        return

    try:
        os.remove(path)
        log.info("Deleted temp file: %s", path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not delete temp file '%s': %s", path, exc)
