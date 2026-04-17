import fcntl
import logging
import os
from typing import Optional

import config

log = logging.getLogger(__name__)


def get_user_state_path(user_id: str = "default") -> str:
    """Return state file path for user. Future: will map to DB query."""
    # For now, single user - return global state file
    # Future: return f"/data/{user_id}/state.txt" or DB lookup
    return config.STATE_FILE


def get_last_published(user_id: str = "default") -> Optional[str]:
    """
    Read and return the last published video ID for user.
    Returns None if no state exists yet (first run).
    """
    path = get_user_state_path(user_id)
    if not os.path.exists(path):
        log.info("State file not found — assuming first run.")
        return None

    # Atomic read with shared lock
    with open(path, 'r', encoding='utf-8') as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
        value = fh.read().strip()

    if not value:
        log.warning("State file is empty — treating as first run.")
        return None

    log.info("Found last published video ID in state file.")
    return value


def mark_published(video_id: str, user_id: str = "default") -> None:
    """
    Atomically write video_id to user's state file.
    Uses exclusive lock to prevent race conditions.
    """
    if not video_id or not video_id.strip():
        raise ValueError("video_id must not be empty.")

    path = get_user_state_path(user_id)
    
    # Atomic write with exclusive lock
    with open(path, 'w', encoding='utf-8') as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
        fh.write(video_id.strip())
        fh.flush()  # Ensure data hits disk before releasing lock

    log.info("State updated successfully.")


def _clear_state(user_id: str = "default") -> None:
    """Atomically clear user's state file to start new cycle."""
    path = get_user_state_path(user_id)
    
    with open(path, 'w', encoding='utf-8') as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write("")
        fh.flush()
    
    log.info("State file cleared — new cycle begins.")


def get_next_video_id(metadata_ids: list[str], user_id: str = "default") -> str:
    """
    Return the next video ID to publish for user.
    Thread-safe: uses file locking to prevent race conditions.
    """
    if not metadata_ids:
        raise ValueError("metadata_ids list is empty — nothing to publish.")

    last = get_last_published(user_id)

    if last is None:
        next_id = metadata_ids[0]
        log.info("First run — starting with first video.")
        return next_id

    if last not in metadata_ids:
        log.warning("Last published ID not found in metadata — starting from the beginning.")
        return metadata_ids[0]

    current_index = metadata_ids.index(last)
    next_index = current_index + 1

    if next_index >= len(metadata_ids):
        log.info("Reached end of playlist — starting new cycle.")
        _clear_state(user_id)
        return metadata_ids[0]

    next_id = metadata_ids[next_index]
    log.info("Selected next video for publishing.")
    return next_id
