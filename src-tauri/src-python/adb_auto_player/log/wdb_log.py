"""The WDB session log, and the rule for what reaches the live view.

Two different audiences. The FILE wants everything: at 4am, reading back why a run went
wrong, the line that explains it is usually one nobody would choose to watch. The LIVE
view wants almost nothing: it is read over a shoulder while a draft is running, and every
line that is merely true competes with the two or three that are useful.

Splitting them means neither has to compromise. The file is truncated on start, so it is
always exactly one session - which is also what makes it worth attaching to a bug report.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

# Messages the live view hides. Each is either visible on the game screen already, or a
# measurement that belongs in the file with the rest of the evidence.
#
# Written as prefixes and codes rather than a level, because level is the wrong axis: all
# of these are INFO, and so are the handful of lines worth watching.
_HIDDEN_CODES = frozenset({
    "SC-53",  # picks read and read timing - a measurement, not an event
    "SC-61",  # device stream up: inverted below, only the failure is worth saying
    "SC-69",  # the six identified slugs with scores - on screen already
    "SC-71",  # read order - an implementation detail of the locked read
    "SC-72",  # the model's own size, useful when reading back, noise while watching
    "SC-74",  # ratings read - on screen already
    "SC-75",  # the prediction, which the bubble is already showing
    "SC-77",  # crowd split - on screen already
    "SC-78",  # market recorded
    "SC-80",  # spectator count - on screen already
})

# Lines the odds block prints that repeat what the overlay shows, or qualify it in a way
# that only matters when reading back.
_HIDDEN_PREFIXES = (
    "ODDS",
    "80% interval",
    "=====",
    "  =====",
)

# "recorded 6 rows, 0 deduced by elimination, 0 set-consistent" is worth seeing ONLY when
# a number is off. All-zeroes is the normal case and says nothing.
_ALL_CLEAR = re.compile(r"recorded \d+ rows, 0 deduced by elimination, 0 set-consistent")

_CODE = re.compile(r"\[(SC-\d+)]")


def is_live_worthy(message: str) -> bool:
    """Whether a message earns a place in the live view.

    Args:
        message: The formatted log message.

    Returns:
        False if it belongs in the file only.
    """
    text = (message or "").strip()
    if not text:
        return False
    code = _CODE.search(text)
    if code and code.group(1) in _HIDDEN_CODES:
        return False
    if any(text.startswith(p) for p in _HIDDEN_PREFIXES):
        return False
    return not _ALL_CLEAR.search(text)


def wdb_log_path() -> Path:
    """Where the session log lives.

    Beside the existing debug log, so anyone who knows where one is knows the other.
    """
    override = os.environ.get("ADB_WDB_LOG_FILE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "adb-auto-player" / "wdb-session.log"


class WdbSessionFileHandler(logging.Handler):
    """Every record, to a file truncated at startup.

    Truncating rather than appending is deliberate. An append-only log grows without
    bound on an overnight run and then has to be searched for "which session was that";
    a truncated one is always exactly the session you are looking at, which is what makes
    it worth attaching to a bug report unedited.
    """

    def __init__(self, log_path: Path) -> None:
        super().__init__(level=logging.DEBUG)
        self.log_path = log_path
        self._ready = False

    def _open(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("w", encoding="utf-8") as handle:
            handle.write(
                f"# WDB session log - {datetime.now().isoformat(timespec='seconds')}\n"
            )
        self._ready = True

    def emit(self, record: logging.LogRecord) -> None:
        """Append one line. Never raises - a log is not worth a run."""
        try:
            if not self._ready:
                self._open()
            stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} {record.levelname:<7} {record.getMessage()}\n")
        except Exception:  # noqa: BLE001 - logging must never break the caller
            pass


def attach_wdb_session_log(
    logger: logging.Logger | None = None, log_path: Path | None = None
) -> Path:
    """Attach the session file handler once, returning the path used."""
    logger = logger or logging.getLogger()
    for existing in logger.handlers:
        if isinstance(existing, WdbSessionFileHandler):
            return existing.log_path
    path = log_path or wdb_log_path()
    logger.addHandler(WdbSessionFileHandler(path))
    return path
