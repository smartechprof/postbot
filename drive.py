import logging
import os
import tempfile

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

import config

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def _get_service():
    """Build and return an authenticated Google Drive service client."""
    creds = Credentials(
        token=None,
        refresh_token=config.DRIVE_REFRESH_TOKEN,
        client_id=config.DRIVE_WEB_CLIENT_ID,
        client_secret=config.DRIVE_WEB_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def _find_folder(service, folder_name: str) -> str:
    """Return the Drive folder ID for the given folder name."""
    query = (
        f"name = '{folder_name}' "
        f"and mimeType = 'application/vnd.google-apps.folder' "
        f"and trashed = false"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=10,
    ).execute()

    folders = result.get("files", [])
    if not folders:
        raise FileNotFoundError(f"Google Drive folder not found: '{folder_name}'")
    if len(folders) > 1:
        log.warning("Multiple folders named '%s' found, using the first one.", folder_name)

    folder_id = folders[0]["id"]
    log.info("Found folder '%s' (id=%s)", folder_name, folder_id)
    return folder_id


_VIDEO_EXTENSIONS = (".mov", ".mp4", ".avi", ".mkv", ".webm")


def list_mov_files(folder_name: str = None) -> list[dict]:
    """
    Return a list of video files (.mov, .mp4, .avi, .mkv, .webm) in the
    Drive folder, sorted by name.

    Each item: {"id": str, "name": str}
    """
    folder_name = folder_name or config.DRIVE_FOLDER_NAME
    service = _get_service()
    folder_id = _find_folder(service, folder_name)

    ext_conditions = " or ".join(
        f"name contains '{ext}'" for ext in _VIDEO_EXTENSIONS
    )
    query = (
        f"'{folder_id}' in parents "
        f"and ({ext_conditions}) "
        f"and trashed = false"
    )
    files = []
    page_token = None

    while True:
        result = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageSize=100,
            pageToken=page_token,
        ).execute()

        files.extend(result.get("files", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    files.sort(key=lambda f: f["name"])

    # Attach video_id = first 3 chars of filename (e.g. "001" from "001_spray_foam.mov")
    for f in files:
        f["video_id"] = os.path.splitext(f["name"])[0][:3]

    log.info("Found %d video file(s) in '%s'", len(files), folder_name)
    return files


def download_file(file_name: str, folder_name: str = None) -> str:
    """
    Download a file by name from the Drive folder to a local temp directory.

    Returns the local file path.
    """
    folder_name = folder_name or config.DRIVE_FOLDER_NAME
    service = _get_service()
    folder_id = _find_folder(service, folder_name)

    query = (
        f"'{folder_id}' in parents "
        f"and name = '{file_name}' "
        f"and trashed = false"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=2,
    ).execute()

    files = result.get("files", [])
    if not files:
        # Exact name not found — fall back to any file whose video_id prefix matches.
        video_id = os.path.splitext(file_name)[0][:3]
        log.warning(
            "File '%s' not found — searching for any file with video_id prefix '%s'.",
            file_name, video_id,
        )
        all_files = list_mov_files(folder_name)
        matches = [f for f in all_files if f.get("video_id") == video_id]
        if not matches:
            raise FileNotFoundError(
                f"No file with video_id '{video_id}' found in folder '{folder_name}'"
            )
        if len(matches) > 1:
            log.warning("Multiple files match video_id '%s', using first: %s", video_id, matches[0]["name"])
        file_name = matches[0]["name"]
        file_id   = matches[0]["id"]
    else:
        file_id = files[0]["id"]

    log.info("Downloading '%s' (id=%s)...", file_name, file_id)

    # Sanitize file name to prevent path traversal
    safe_name = os.path.basename(file_name).replace("..", "")
    if not safe_name or safe_name.startswith("."):
        safe_name = f"video_{file_id}"
    local_path = os.path.join(tempfile.gettempdir(), safe_name)

    request = service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            log.info("Download progress: %d%%", int(status.progress() * 100))

    log.info("Saved to %s", local_path)
    return local_path
