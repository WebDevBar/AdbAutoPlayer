"""The log export writes a file and SAYS WHERE.

There is no file picker: the write is silent and the destination is not always
Downloads. Before this, the frontend revealed the file in the file manager instead -
which on Linux prompted "open with..." rather than showing the folder, so the export
looked like it had done the wrong thing, and on Windows nothing said where the file
went at all.
"""

import asyncio
import importlib
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


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


@pytest.fixture
def main_module():
    """`adb_auto_player.__main__`, imported fresh with the pass-through stub in place.

    Import order cannot be trusted: `test_e2e_flow` imports the same module with a plain
    MagicMock for `pytauri`, and whichever test module lands first wins the sys.modules
    entry for the whole session. When that was the other one, every command here was a
    MagicMock and `asyncio.run` got a mock instead of a coroutine - a failure that only
    appeared in the full suite and never when this file ran alone.

    Everything is put back afterwards. Leaving the stub in place, or leaving the module
    evicted, broke `test_e2e_flow` in turn - swapping a global and not restoring it just
    moves the failure to whoever runs next.
    """
    saved = {
        name: sys.modules.get(name)
        for name in ("pytauri", "adb_auto_player.ext_mod", "adb_auto_player.__main__")
    }
    sys.modules["pytauri"] = _mock_pytauri
    sys.modules["adb_auto_player.ext_mod"] = MagicMock()
    sys.modules.pop("adb_auto_player.__main__", None)
    try:
        yield importlib.import_module("adb_auto_player.__main__")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_writes_the_file_and_logs_where(main_module, caplog, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "Downloads").mkdir()

    with caplog.at_level(logging.INFO):
        result = Path(
            asyncio.run(
                main_module.save_log_file(
                    main_module.SaveLogFileBody(content="hello\n", filename="probe.txt")
                )
            )
        )

    assert result.read_text(encoding="utf-8") == "hello\n"
    assert result.parent.name == "Downloads"
    assert any("exported" in r.getMessage() for r in caplog.records), (
        "the export must say where it went - there is no file picker to tell the user"
    )


def test_falls_back_to_temp_and_still_says_where(
    main_module, caplog, monkeypatch, tmp_path
):
    """A machine with no Downloads folder gets the temp directory - silently, before."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))  # no Downloads

    with caplog.at_level(logging.INFO):
        body = main_module.SaveLogFileBody(content="x", filename="probe2.txt")
        result = Path(asyncio.run(main_module.save_log_file(body)))

    assert result.is_file()
    assert result.parent.name != "Downloads"
    messages = [r.getMessage() for r in caplog.records if "exported" in r.getMessage()]
    assert messages, "the fallback location is exactly the case the user cannot guess"
    assert str(result) in messages[0] or result.name in messages[0]
