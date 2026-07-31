"""Archiving evaluated frames, so the unobserved cases can be studied later.

Not /mnt/vault: that is the author's machine. This app ships to Windows and macOS,
so the destination is resolved the way the Solstice Clash mode resolves it.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from adb_auto_player.util import RuntimeInfo

from .geometry import Mode


def collection_dir() -> Path:
    """Where evaluated frames are written."""
    override = os.environ.get("ADB_FRIENDLY_FIRE_DIR")
    if override:
        return Path(override).expanduser()
    if RuntimeInfo.is_windows():
        base = os.environ.get("APPDATA") or "~/AppData/Roaming"
    elif RuntimeInfo.is_mac():
        base = "~/Library/Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "AdbAutoPlayer" / "friendly-fire"


def archive(frame: np.ndarray, mode: Mode, outcome: str) -> Path | None:
    """Write one frame, named with mode, timestamp and outcome.

    Args:
        frame: BGR frame.
        mode: which screen it came from.
        outcome: short slug describing what was decided.

    Returns:
        The path written, or None if anything went wrong - diagnostics must never
        cost a match.
    """
    try:
        directory = collection_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        path = directory / f"{mode.value}-{stamp}-{outcome}.png"
        cv2.imwrite(str(path), frame)  # already BGR
        return path
    except Exception as exc:
        logging.debug(f"[FF-10] could not archive frame: {exc}")
        return None
