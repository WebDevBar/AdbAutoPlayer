"""A bounded replay of what the console showed during the last run.

Execution is synchronous, so there is no second thread to render a live pane into.
What this gives instead is the last N lines, offered after a run completes.

It stores log RECORDS and re-renders them through the console handler's own
formatter, rather than capturing stdout, so the replay is exactly what was shown -
including colour - instead of a second, subtly different rendering.
"""

import logging
from collections import deque

_DEFAULT_CAPACITY = 200


class DequeLogHandler(logging.Handler):
    """Keeps the most recent records in memory, dropping the oldest.

    Modelled on `WarningContextFileHandler`, which already uses a 200-record buffer
    for the same reason: bounded memory, no file, no ordering surprises.
    """

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        """Initialise the handler.

        Args:
            capacity: How many records to retain.
        """
        super().__init__()
        self.records: deque[logging.LogRecord] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        """Store a record.

        Args:
            record: The record to retain.
        """
        self.records.append(record)

    def clear(self) -> None:
        """Drop every retained record, so the next run starts clean."""
        self.records.clear()

    def render(self, limit: int | None = None) -> list[str]:
        """Format the retained records.

        Args:
            limit: How many of the most recent records to render. None renders all.

        Returns:
            One formatted string per record, oldest first.
        """
        records = list(self.records)
        if limit is not None:
            records = records[-limit:]
        return [self.format(record) for record in records]


def install(capacity: int = _DEFAULT_CAPACITY) -> DequeLogHandler:
    """Attach a tail handler to the root logger, matching the console handler.

    It takes the console handler's level and formatter so the replay matches what
    was displayed. Falling back to the root level would capture DEBUG records the
    operator never saw and render them without colour.

    Args:
        capacity: How many records to retain.

    Returns:
        The installed handler.
    """
    root = logging.getLogger()
    handler = DequeLogHandler(capacity)

    console = next(
        (h for h in root.handlers if isinstance(h, logging.StreamHandler)),
        None,
    )
    if console is not None:
        handler.setLevel(console.level)
        if console.formatter is not None:
            handler.setFormatter(console.formatter)
    else:
        handler.setLevel(root.level)

    root.addHandler(handler)
    return handler
