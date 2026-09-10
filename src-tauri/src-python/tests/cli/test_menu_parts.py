"""The menu's own logic: exits, field collection, routine rules, log tail.

Driving the whole loop would be testing `input()`; these cover the parts where
getting it wrong loses data or hides a failure.
"""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

mock_pytauri = MagicMock()
mock_pytauri.Commands = MagicMock
mock_pytauri.AppHandle = MagicMock
mock_pytauri.Event = MagicMock
mock_pytauri.Emitter = MagicMock
sys.modules.setdefault("pytauri", mock_pytauri)
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.cli import log_tail, routine_editor, settings_editor  # noqa: E402
from adb_auto_player.models.pydantic import AdbSettings  # noqa: E402
from adb_auto_player.models.pydantic.app_settings import AppSettings  # noqa: E402


class TestFieldCollection:
    def test_nested_models_become_dotted_keys(self):
        keys = {f.dotted_key for f in settings_editor.collect_fields(AppSettings)}
        assert "ui.theme" in keys
        assert "logging.level" in keys

    def test_dotted_keys_match_the_toml_shape(self, tmp_path: Path):
        """A dotted key must address the file, or edits land in the wrong place."""
        from adb_auto_player.file_loader import settings_file  # noqa: PLC0415

        data: dict = {}
        settings_file.set_in(data, "advanced.template_timeout", 9.0)
        assert data == {"advanced": {"template_timeout": 9.0}}

    def test_adb_settings_fields_are_editable(self):
        fields = settings_editor.collect_fields(AdbSettings)
        kinds = {f.dotted_key: f.kind for f in fields}
        assert kinds["device.id"] == "string"
        assert kinds["device.streaming"] == "boolean"
        assert kinds["advanced.adb_port"] == "integer"

    def test_boolean_coercion_accepts_words_and_digits(self):
        field = next(
            f
            for f in settings_editor.collect_fields(AdbSettings)
            if f.dotted_key == "device.streaming"
        )
        assert settings_editor._coerce(field, "yes") is True
        assert settings_editor._coerce(field, "0") is False
        with pytest.raises(ValueError):
            settings_editor._coerce(field, "maybe")


class TestRoutineRules:
    def test_a_legacy_string_task_reads_as_its_name(self):
        assert routine_editor._task_name("Arena") == "Arena"

    def test_a_table_task_reads_as_its_name(self):
        assert routine_editor._task_name({"name": "Arena", "repeat": True}) == "Arena"

    def test_an_unknown_task_is_marked_not_dropped(self):
        label = routine_editor._label("Ghost", ["Arena"], set())
        assert "Ghost" in label
        assert "UNKNOWN" in label

    def test_a_duplicate_is_marked(self):
        label = routine_editor._label("Arena", ["Arena"], {"Arena"})
        assert "DUPLICATE" in label

    def test_a_known_first_occurrence_is_unmarked(self):
        assert routine_editor._label("Arena", ["Arena"], set()) == "Arena"

    def test_slot_lookup_prefers_the_alias_already_in_the_file(self):
        data = {"Custom Routine 1": {"Task List": ["Arena"]}}
        table = routine_editor._slot_table(data, "custom_routine_one")
        assert routine_editor._tasks(table) == ["Arena"]

    def test_slot_lookup_accepts_the_field_name_too(self):
        data = {"custom_routine_one": {"tasks": ["Arena"]}}
        table = routine_editor._slot_table(data, "custom_routine_one")
        assert routine_editor._tasks(table) == ["Arena"]

    def test_there_are_exactly_three_slots(self):
        assert len(routine_editor.SLOT_KEYS) == 3


class TestLogTail:
    def test_it_keeps_only_the_most_recent(self):
        handler = log_tail.DequeLogHandler(capacity=3)
        for i in range(10):
            handler.emit(
                logging.LogRecord("t", logging.INFO, "f", i, f"line {i}", None, None)
            )
        assert len(handler.records) == 3
        assert [r.getMessage() for r in handler.records] == [
            "line 7",
            "line 8",
            "line 9",
        ]

    def test_render_limits_and_keeps_order(self):
        handler = log_tail.DequeLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        for i in range(5):
            handler.emit(
                logging.LogRecord("t", logging.INFO, "f", i, f"line {i}", None, None)
            )
        assert handler.render(2) == ["line 3", "line 4"]

    def test_clear_empties_it_between_runs(self):
        handler = log_tail.DequeLogHandler()
        handler.emit(logging.LogRecord("t", logging.INFO, "f", 1, "x", None, None))
        handler.clear()
        assert handler.render() == []


class TestRunExitHandling:
    """A failed task must not look like a successful one."""

    @staticmethod
    def _run_with(monkeypatch, raises):
        from adb_auto_player.cli import menu  # noqa: PLC0415

        def fake_execute(command, tasks):
            raise raises

        monkeypatch.setattr(menu.Execute, "find_command_and_execute", fake_execute)
        monkeypatch.setattr(menu.prompt, "confirm", lambda *a, **k: False)
        monkeypatch.setattr(menu, "get_game_tasks", dict)
        return menu

    def test_a_nonzero_exit_is_reported(self, monkeypatch, capsys):
        menu = self._run_with(monkeypatch, SystemExit(1))
        menu._run_task("Whatever")
        assert "exited with code 1" in capsys.readouterr().out

    def test_a_zero_exit_reads_as_an_interrupt_not_a_failure(self, monkeypatch, capsys):
        menu = self._run_with(monkeypatch, SystemExit(0))
        menu._run_task("Whatever")
        out = capsys.readouterr().out
        assert "interrupted" in out
        assert "exited with code" not in out

    def test_a_bare_keyboard_interrupt_is_caught(self, monkeypatch, capsys):
        menu = self._run_with(monkeypatch, KeyboardInterrupt())
        menu._run_task("Whatever")
        assert "interrupted" in capsys.readouterr().out

    def test_the_summary_is_reset_between_runs(self, monkeypatch):
        from adb_auto_player.util import SummaryGenerator  # noqa: PLC0415

        menu = self._run_with(monkeypatch, SystemExit(0))
        SummaryGenerator().increment("Section", "item")
        assert SummaryGenerator().entries
        menu._run_task("Whatever")
        assert not SummaryGenerator().entries
