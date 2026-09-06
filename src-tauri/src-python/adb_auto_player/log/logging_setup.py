"""ADB Auto Player Logging Setup Module."""

import collections
import logging
import re
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

from adb_auto_player.util import (
    StringHelper,
    TracebackHelper,
)

from .log_presets import LogPreset


# The GUI renders log messages as HTML, so a few of them carry <span class="sc-*">
# markers to colour a hero side or a prediction verdict. A terminal is not a browser:
# printed raw, those tags are noise wrapped around the words that matter. Rather than
# strip them and lose the emphasis, map each class onto the ANSI colour it stands for.
_SPAN_COLOURS: dict[str, str] = {
    "sc-blue": "\033[94m",
    "sc-red": "\033[91m",
    "sc-hit": "\033[92m",
    "sc-miss": "\033[93m",
}
_SPAN = re.compile(r'<span class="([a-z-]+)">(.*?)</span>', re.DOTALL)


def _render_spans(message: str, reset: str, restore: str) -> str:
    """Turn the GUI's colour spans into ANSI, keeping the surrounding colour.

    Args:
        message: The raw message, possibly containing span markers.
        reset: The ANSI reset sequence.
        restore: The colour to return to after a span, so the rest of the line
            keeps
            the level colour it would otherwise have lost.

    Returns:
        The message with spans rendered as colour.
    """

    def swap(m: re.Match[str]) -> str:
        colour = _SPAN_COLOURS.get(m.group(1), "")
        return f"{colour}{m.group(2)}{reset}{restore}" if colour else m.group(2)

    return _SPAN.sub(swap, message)


def _strip_spans(message: str) -> str:
    """Remove span markers entirely, for output that carries no colour.

    Args:
        message: The raw message.

    Returns:
        The message with every span replaced by its text.
    """
    return _SPAN.sub(lambda m: m.group(2), message)


class BaseLogHandler(logging.Handler):
    """Base log handler with common functionality."""


# How many preceding records to keep so a warning arrives with its context.
CONTEXT_RECORDS = 200


class WarningContextFileHandler(logging.Handler):
    """Writes to file ONLY when something goes wrong, with the lead-up included.

    A plain file handler forces a bad choice: log INFO and lose the detail that
    explains a failure, or log DEBUG and write hundreds of megabytes during an
    overnight run. This does neither. Every record goes into a fixed-size ring
    buffer in memory, and nothing reaches the disk until a WARNING or worse
    appears - at which point the buffer is flushed, so the file holds the failure
    AND the steps that led to it.

    A clean run therefore leaves an empty file, which is the point.
    """

    def __init__(self, log_path: Path, context: int = CONTEXT_RECORDS) -> None:
        super().__init__(level=logging.DEBUG)
        self.log_path = log_path
        self._buffer: deque[logging.LogRecord] = collections.deque(maxlen=context)
        self._started = False

    @staticmethod
    def _format_record(record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"{stamp}.{int(record.msecs):03d} [{record.levelname}] "
            f"{TracebackHelper.format_debug_info(record)} "
            f"{StringHelper.sanitize_path(record.getMessage())}"
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Buffer every record; flush the buffer when one is WARNING or worse."""
        try:
            self._buffer.append(record)
            if record.levelno < logging.WARNING:
                return
            lines = [self._format_record(buffered) for buffered in self._buffer]
            self._buffer.clear()
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                if not self._started:
                    handle.write(f"\n=== run started {datetime.now().isoformat()} ===\n")
                    self._started = True
                handle.write("\n".join(lines))
                handle.write("\n--- end of context ---\n")
        except Exception:
            # Logging must never take the run down with it.
            pass


def default_log_path() -> Path:
    """Where the debug log lives.

    Honours ADB_AUTO_PLAYER_LOG_FILE so a run can be pointed elsewhere without a
    rebuild.
    """
    override = os.environ.get("ADB_AUTO_PLAYER_LOG_FILE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "adb-auto-player" / "debug.log"


def attach_warning_context_file_logging(
    logger: logging.Logger | None = None, log_path: Path | None = None
) -> Path:
    """Attach the warning-triggered file handler once, returning the path used."""
    logger = logger or logging.getLogger()
    for existing in logger.handlers:
        if isinstance(existing, WarningContextFileHandler):
            return existing.log_path
    path = log_path or default_log_path()
    logger.addHandler(WarningContextFileHandler(path))
    return path


class TerminalLogHandler(BaseLogHandler):
    """Terminal log handler for logging to the console with colors."""

    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[94m",  # Blue
        "INFO": "\033[92m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[95m",  # Magenta
        "RESET": "\033[0m",  # Reset to default
    }

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log message in colored text format.

        Args:
            record (logging.LogRecord): The log record to emit.
        """
        log_level: str = record.levelname

        log_preset: LogPreset | None = getattr(record, "preset", None)

        if log_preset is not None:
            color: str = log_preset.get_terminal_color()
        else:
            color = self.COLORS.get(log_level, self.COLORS["RESET"])

        message = _render_spans(
            StringHelper.sanitize_path(record.getMessage()),
            reset=self.COLORS["RESET"],
            restore=color,
        )
        formatted_message: str = (
            f"{color}"
            f"[{log_level}] "
            f"{TracebackHelper.format_debug_info(record)} "
            f"{message}"
            f"{self.COLORS['RESET']}"
        )
        print(formatted_message)
        sys.stdout.flush()


class TextLogHandler(BaseLogHandler):
    """Text log handler for logging to the console with timestamps."""

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log message in text format with timestamp.

        Args:
            record (logging.LogRecord): The log record to emit.
        """
        log_level: str = record.levelname
        timestamp: str = datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        timestamp_with_ms: str = f"{timestamp}.{int(record.msecs):03d}"

        formatted_message: str = (
            f"{timestamp_with_ms} [{log_level}] "
            f"{TracebackHelper.format_debug_info(record)} "
            f"{_strip_spans(StringHelper.sanitize_path(record.getMessage()))}"
        )
        print(formatted_message)
        sys.stdout.flush()


LogHandlerType = Literal["terminal", "text", "raw"]


def setup_logging(handler_type: LogHandlerType, level: int | str) -> None:
    """Set up logging with specified handler type and level.

    Args:
        handler_type (LogHandlerType): Type of log handler to use
        level (int | str): The log level to set
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
    except AttributeError:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore
    except AttributeError:
        pass

    logger: logging.Logger = logging.getLogger()
    # The ROOT stays at DEBUG so records exist for the warning-context file to keep.
    # `level` is what the CONSOLE shows, applied to its handler below. Setting the root
    # to INFO instead would mean the DEBUG records are never created at all, and the
    # file that exists to explain a failure would hold only the failure.
    #
    # DISABLE is the exception: it means silence, so it is applied to the root and
    # nothing is recorded anywhere.
    logger.setLevel(logging.DEBUG if level not in (99, "DISABLE") else level)

    if "raw" == handler_type:
        return

    for handler in logger.handlers:
        logger.removeHandler(handler)

    handler_mapping = {
        "terminal": TerminalLogHandler,
        "text": TextLogHandler,
    }

    handler_class = handler_mapping.get(handler_type)
    if handler_class:
        handler = handler_class()
        # The ROOT logger stays wherever the caller put it - usually DEBUG - because
        # WarningContextFileHandler needs those records for the ring buffer it flushes
        # when something fails. Quieting the root instead would empty that buffer and
        # leave a failure with no lead-up, which is the whole point of the file.
        #
        # So the filtering happens HERE, on the console handler alone: a run prints
        # INFO and above, and `--log-level DEBUG` is what opts into the firehose.
        if level in (logging.DEBUG, "DEBUG"):
            handler.setLevel(logging.DEBUG)
        else:
            handler.setLevel(logging.INFO)
        logger.addHandler(handler)

        # Now that the console is quiet, the DEBUG detail has to go SOMEWHERE or
        # quieting it just destroys evidence. This handler was written for exactly
        # that and was never actually installed - nothing called it. A clean run
        # leaves an empty file; a run that hits a WARNING writes the failure and the
        # records leading up to it.
        attach_warning_context_file_logging(logger)
    else:
        raise ValueError(f"Unknown handler type: {handler_type}")
