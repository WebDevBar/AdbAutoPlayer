"""The one thing only a real terminal can prove: a long list scrolls.

The picker is driven under a pty because `tui` opens `/dev/tty` directly, so the
child of `os.forkpty` is the only place that resolves to a terminal we control
the size of. Mocking that seam away is exactly what let the scroll bug ship: the
list rendered correctly, it just never moved.
"""

import fcntl
import os
import pty
import select
import struct
import termios
import time

from adb_auto_player.cli import tui

_ROWS, _COLS = 24, 80
_ENTRIES = 60
_TARGET = "item49"  # reachable only if the view scrolls past row 24


def _child() -> None:
    os.environ["TERM"] = "xterm"
    tui.select("Tasks", [f"item{i}" for i in range(_ENTRIES)])


def _read_until(fd: int, needle: str, deadline: float) -> str:
    """Drain the pty until `needle` shows up or time runs out.

    `select` rather than a bare `os.read`: a picker that is NOT scrolling simply
    stops emitting, and a blocking read then waits for output that never comes -
    the failing case would hang instead of failing.
    """
    seen = ""
    while (remaining := deadline - time.monotonic()) > 0:
        if not select.select([fd], [], [], remaining)[0]:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        seen += chunk.decode(errors="replace")
        if needle in seen:
            break
    return seen


def test_the_selected_row_stays_on_screen_in_a_long_list():
    pid, fd = pty.fork()
    if pid == 0:  # child: never returns
        try:
            _child()
        finally:
            os._exit(0)

    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", _ROWS, _COLS, 0, 0))
        time.sleep(1.0)  # let the first full-screen render land
        os.write(fd, b"50")  # digit jump to option 50, i.e. item49
        screen = _read_until(fd, _TARGET, time.monotonic() + 5.0)
        os.write(fd, b"\x1b")  # escape, so the child exits
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        os.waitpid(pid, 0)

    assert _TARGET in screen, (
        f"{_TARGET!r} never rendered - the view did not scroll to the cursor"
    )
