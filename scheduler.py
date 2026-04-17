import logging
import os
from typing import Optional

import config

log = logging.getLogger(__name__)


def get_last_published() -> Optional[str]:
    """
    Read and return the last published video ID from state.txt.

    Returns None if the state file does not exist yet (first run).
    """
    path = config.STATE_FILE
    if not os.path.exists(path):
        log.info("State file '%s' not found — assuming first run.", path)
        return None

    with open(path, encoding="utf-8") as fh:
        value = fh.read().strip()

    if not value:
        log.warning("State file '%s' is empty — treating as first run.", path)
        return None

    log.info("Found last published video ID in state file.")
    return value


def get_next_video_id(metadata_ids: list[str]) -> str:
    """
    Return the next video ID to publish, given a sorted list of all video IDs.

    Finds the last published ID in the list and returns the one after it.
    Wraps around cyclically: after the last video, returns the first.
    If no state exists yet (first run), returns the first video in the list.

    Raises ValueError if metadata_ids is empty.
    """
    if not metadata_ids:
        raise ValueError("metadata_ids list is empty — nothing to publish.")

    last = get_last_published()

    if last is None:
        next_id = metadata_ids[0]
        log.info("First run — starting with '%s'.", next_id)
        return next_id

    if last not in metadata_ids:
        log.warning("Last published ID not found in metadata — starting from the beginning.")
        return metadata_ids[0]

    current_index = metadata_ids.index(last)
    next_index    = current_index + 1

    if next_index >= len(metadata_ids):
        log.info(
            "Reached end of playlist after '%s' — clearing state.txt and starting a new cycle from '%s'.",
            last, metadata_ids[0],
        )
        _clear_state()
        return metadata_ids[0]

    next_id = metadata_ids[next_index]
    log.info(
        "Next video: '%s' (after '%s', index %d → %d).",
        next_id, last, current_index, next_index,
    )
    return next_id


def _clear_state() -> None:
    """Truncate state.txt so the next run starts fresh from the first video."""
    path = config.STATE_FILE
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")
    log.info("State file '%s' cleared — new cycle begins.", path)


def mark_published(video_id: str) -> None:
    """
    Write video_id to state.txt, replacing any previous value.

    Raises ValueError if video_id is empty.
    """
    if not video_id or not video_id.strip():
        raise ValueError("video_id must not be empty.")

    path = config.STATE_FILE
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(video_id.strip())

    log.info("State updated: '%s' → '%s'", path, video_id)
