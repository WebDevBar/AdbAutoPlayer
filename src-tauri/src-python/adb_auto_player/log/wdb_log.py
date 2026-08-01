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
    "SC-77",  # crowd split - on screen already
    "SC-78",  # market recorded
    "SC-80",  # spectator count - on screen already
    # Screen transitions. Each one is visible on the device a second before it is
    # logged, so in the live view they only push the picks and the call off the top.
    "SC-50",  # mode banner at startup
    "SC-54",  # draft screen
    "SC-55",  # draft over - locked screen is up
    "SC-58",  # locked picks screen
    "SC-76",  # FINAL - all picks locked, immediately followed by the odds block
    "SC-82",  # overlay install/remove chatter
})

# SC-75 is NOT hidden. It was, under the comment "the prediction, which the bubble is
# already showing" - written when SC-75 carried only the prediction. It now also carries
# `BLUE WINS` / `RED WINS` and the HIT/MISS result, which are the two lines the operator
# most wants to see, and they inherited a hide rule meant for something else. The result
# of a match is not on screen by the time it is logged: the game has already moved on.

# Lines the odds block prints that repeat what the overlay shows, or qualify it in a way
# that only matters when reading back.
_HIDDEN_PREFIXES = (
    "ODDS",
    "80% interval",
    "=====",
    "  =====",
    # The union of both screens, printed the moment the picks are all known. Every
    # hero in it has already been announced one line at a time, in draft order.
    "locked - ",
)

# "recorded 6 rows, 0 deduced by elimination, 0 set-consistent" is worth seeing ONLY when
# a number is off. All-zeroes is the normal case and says nothing.
_ALL_CLEAR = re.compile(r"recorded \d+ rows, 0 deduced by elimination, 0 set-consistent")

# "4/6 heroes identified" is bookkeeping under the call: it says how much of the draft
# the number rests on, which matters when reconstructing a call and not while watching
# one. The picks themselves are announced individually, so the count is derivable anyway.
_HERO_COUNT = re.compile(r"^\s*\d/6 heroes identified")

# `[SC-35] sync enabled` is a one-off banner; `[SC-35] sync: pushed 1, duplicate 0` is
# the line the operator actually watches for. Same code, so this has to match the
# MESSAGE - which is why the filter is written on text rather than on level or code.
_SYNC_BANNER = re.compile(r"^\[SC-35]\s+sync (enabled|disabled)$")

_CODE = re.compile(r"\[(SC-\d+)]")

# Only a code at the START of the line is a prefix; one quoted mid-sentence is content.
_CODE_PREFIX = re.compile(r"^\s*\[SC-\d+]\s*")


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
    if _HERO_COUNT.match(text) or _SYNC_BANNER.match(text):
        return False
    return not _ALL_CLEAR.search(text)


def live_message(message: str) -> str:
    """The message as the live view should show it - without its `[SC-nn]` code.

    The codes exist so a line in a log can be grepped and talked about precisely. Nobody
    watching a draft run needs one, and six characters of prefix on every line is six
    characters of hero name pushed off a narrow panel. The FILE keeps them.

    Args:
        message: The formatted log message.

    Returns:
        The message with a leading code removed, if it had one.
    """
    return _CODE_PREFIX.sub("", message or "", count=1)


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
