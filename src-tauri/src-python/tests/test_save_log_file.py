"""The log export writes a file and SAYS WHERE.

There is no file picker: the write is silent and the destination is not always
Downloads. Before this, the frontend revealed the file in the file manager instead -
which on Linux prompted "open with..." rather than showing the folder, so the export
looked like it had done the wrong thing, and on Windows nothing said where the file
went at all.
"""

# ruff: noqa: E402  - sys.modules must be patched before the import under test
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock


class _Commands:
    """Pass-through decorator.

    The real `pytauri.Commands.command` registers the function and returns it. A bare
    MagicMock returns another MagicMock, which makes every command in the module
    uncallable from a test - so the stub has to preserve the function.
    """

    def __init__(self, *args, **kwargs) -> None:
        pass

    def command(self, *args, **kwargs):
        def deco(fn):
            return fn

        return deco


_mock_pytauri = MagicMock()
_mock_pytauri.Commands = _Commands
sys.modules["pytauri"] = _mock_pytauri
sys.modules["adb_auto_player.ext_mod"] = MagicMock()

from adb_auto_player.__main__ import SaveLogFileBody, save_log_file


def test_writes_the_file_and_logs_where(caplog, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "Downloads").mkdir()

    with caplog.at_level(logging.INFO):
        result = Path(
            asyncio.run(
                save_log_file(SaveLogFileBody(content="hello\n", filename="probe.txt"))
            )
        )

    assert result.read_text(encoding="utf-8") == "hello\n"
    assert result.parent.name == "Downloads"
    assert any("exported" in r.getMessage() for r in caplog.records), (
        "the export must say where it went - there is no file picker to tell the user"
    )


def test_falls_back_to_temp_and_still_says_where(caplog, monkeypatch, tmp_path):
    """A machine with no Downloads folder gets the temp directory - silently, before."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # no Downloads

    with caplog.at_level(logging.INFO):
        result = Path(
            asyncio.run(
                save_log_file(SaveLogFileBody(content="x", filename="probe2.txt"))
            )
        )

    assert result.is_file()
    assert result.parent.name != "Downloads"
    messages = [r.getMessage() for r in caplog.records if "exported" in r.getMessage()]
    assert messages, "the fallback location is exactly the case the user cannot guess"
    assert str(result) in messages[0] or result.name in messages[0]
