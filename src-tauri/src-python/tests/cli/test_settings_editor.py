"""The settings editor shows what the bot will use, and says what it means.

Rows used to print the raw TOML lookup, so every key absent from the file read
"None" even though the bot runs on the model default. These cover the effective
value, the [set]/[default] marker, the help text, and the input checks.
"""

import copy
import sys
from enum import StrEnum
from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

mock_pytauri = MagicMock()
mock_pytauri.Commands = MagicMock
mock_pytauri.AppHandle = MagicMock
mock_pytauri.Event = MagicMock
mock_pytauri.Emitter = MagicMock
sys.modules.setdefault("pytauri", mock_pytauri)
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.cli import field_help, prompt, settings_editor, tui  # noqa: E402
from adb_auto_player.file_loader import settings_file  # noqa: E402
from adb_auto_player.games.afk_journey.settings import Settings  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

SECTION = "Inner Section"
EN = chr(0x2013)  # the range separator the editor prints


class Color(StrEnum):
    Red = "Red"
    Blue = "Blue"


class Inner(BaseModel):
    attempts: int = Field(default=5, ge=1, le=999, alias="Attempts", title="Attempts")
    flag: bool = Field(default=True, alias="Flag", title="Flag")
    color: Color = Field(default=Color.Red, alias="Color", title="Color")
    colors: list[Color] = Field(
        default_factory=lambda: [Color.Red, Color.Blue], alias="Colors", title="Colors"
    )
    note: str = Field(
        default="hi", alias="Note", title="Note", description="from the schema"
    )
    other: int = Field(default=1, alias="Zero Based", title="Zero Based", ge=0)


class Outer(BaseModel):
    inner: Inner = Field(default_factory=Inner, alias=SECTION, title=SECTION)


def _field(key: str) -> Any:
    return next(f for f in settings_editor.collect_fields(Outer) if f.dotted_key == key)


def _rows(data: dict) -> dict[str, str]:
    """Row text keyed by the field's label."""
    fields = settings_editor.collect_fields(Outer)
    rows = settings_editor.section_rows(fields, data)
    return {f.leaf_title: row for f, row in zip(fields, rows, strict=True)}


class TestEffectiveValues:
    def test_a_missing_key_shows_the_model_default_not_none(self):
        row = _rows({})["Attempts"]
        assert "5" in row
        assert "None" not in row
        assert "[default]" in row

    def test_a_present_key_shows_its_value_and_set_marker(self):
        row = _rows({SECTION: {"Attempts": 40}})["Attempts"]
        assert "40" in row
        assert "[set]" in row
        assert "[default]" not in row

    def test_default_factory_fields_have_a_default(self):
        """The schema omits default_factory defaults; the editor must not."""
        assert _field(f"{SECTION}.Colors").default == ["Red", "Blue"]

    def test_viewing_does_not_insert_defaults_into_the_raw_dict(self):
        data = {SECTION: {"Attempts": 40}}
        before = copy.deepcopy(data)
        _rows(data)
        assert data == before

    def test_an_empty_dict_stays_empty_after_viewing(self):
        data: dict = {}
        _rows(data)
        assert data == {}


class TestFormatting:
    def test_booleans_read_on_and_off(self):
        assert "On" in _rows({})["Flag"]
        assert "Off" in _rows({SECTION: {"Flag": False}})["Flag"]
        assert "True" not in _rows({})["Flag"]

    def test_integer_range_comes_from_the_schema(self):
        assert _rows({})["Attempts"].endswith(f"1{EN}999")
        assert settings_editor.range_text(_field(f"{SECTION}.Attempts")) == f"1{EN}999"

    def test_one_sided_range_is_still_shown(self):
        assert settings_editor.range_text(_field(f"{SECTION}.Zero Based")) == "≥ 0"

    def test_unbounded_field_has_no_range(self):
        assert settings_editor.range_text(_field(f"{SECTION}.Note")) == ""

    def test_enum_shows_the_current_choice(self):
        assert "Red" in _rows({})["Color"]
        assert "Blue" in _rows({SECTION: {"Color": "Blue"}})["Color"]

    def test_rows_are_aligned(self):
        rows = list(_rows({}).values())
        assert len({row.index("[default]") for row in rows}) == 1


class TestResolve:
    ROOT: ClassVar = {
        "$defs": {"Thing": {"type": "string", "enum": ["a", "b"], "title": "Thing"}}
    }

    def test_sibling_metadata_survives_a_ref(self):
        merged = settings_editor._resolve(
            {
                "$ref": "#/$defs/Thing",
                "default": "a",
                "description": "mine",
                "title": "My Thing",
            },
            self.ROOT,
        )
        assert merged["enum"] == ["a", "b"]
        assert merged["default"] == "a"
        assert merged["description"] == "mine"
        assert merged["title"] == "My Thing"

    def test_a_plain_schema_is_returned_unchanged(self):
        schema = {"type": "integer"}
        assert settings_editor._resolve(schema, self.ROOT) == schema

    def test_enum_array_with_a_ref_items_is_detected(self):
        field = _field(f"{SECTION}.Colors")
        assert field.kind == "enum-list"
        assert field.choices == ["Red", "Blue"]

    def test_ref_items_are_resolved_against_the_root_defs(self):
        field = settings_editor._Field(
            "x",
            "X",
            {"type": "array", "items": {"$ref": "#/$defs/Thing"}},
            self.ROOT,
        )
        assert field.kind == "enum-list"
        assert field.choices == ["a", "b"]


class TestHelp:
    def test_the_schema_description_wins_over_the_map(self, monkeypatch):
        monkeypatch.setitem(field_help.FIELD_HELP, "Note", "from the map")
        assert settings_editor.help_text(_field(f"{SECTION}.Note")) == "from the schema"

    def test_the_map_fills_in_when_the_schema_has_none(self):
        text = settings_editor.help_text(_field(f"{SECTION}.Attempts"))
        assert text == field_help.FIELD_HELP["Attempts"]

    def test_a_section_override_beats_the_generic_entry(self):
        assert field_help.lookup("Ravaged Realm", "Attempts") != field_help.lookup(
            "Union Campaign", "Attempts"
        )
        assert (
            field_help.lookup("Union Campaign", "Attempts")
            == (field_help.FIELD_HELP["Attempts"])
        )

    def test_an_undocumented_field_has_no_help(self):
        assert field_help.lookup("Anywhere", "No Such Setting") is None
        assert settings_editor.help_text(_field(f"{SECTION}.Zero Based")) == ""

    def test_the_block_carries_default_and_allowed_values(self):
        block = settings_editor.help_block(_field(f"{SECTION}.Attempts"), width=200)
        assert f"Default: 5. Allowed: 1{EN}999." in block

    def test_an_enum_block_lists_the_choices(self):
        block = settings_editor.help_block(_field(f"{SECTION}.Color"), width=200)
        assert "Default: Red. Allowed: Red, Blue." in block


class _Answers:
    """Stand-in for the prompts: scripted answers, recorded calls."""

    def __init__(self, monkeypatch, line=None, choice=None):
        self.asked: list[tuple] = []
        self.messages: list[str] = []
        self.chosen: list[tuple] = []

        def ask_line(text, default=None, help_text=""):
            self.asked.append((text, default, help_text))
            return line if line is not None else default

        def choose(title, options, back="Back", subtitle="", start=0):
            self.chosen.append((title, list(options), subtitle, start))
            return choice if choice is not None else -1

        monkeypatch.setattr(prompt, "ask_line", ask_line)
        monkeypatch.setattr(prompt, "choose", choose)
        monkeypatch.setattr(prompt, "message", self.messages.append)


class TestEditing:
    def test_a_missing_scalar_is_prefilled_with_the_default(self, monkeypatch):
        answers = _Answers(monkeypatch)
        settings_editor._edit_field(_field(f"{SECTION}.Attempts"), {})
        _, default, help_text = answers.asked[0]
        assert default == "5"
        assert "Max battles" in help_text

    def test_prompt_shows_the_allowed_range(self, monkeypatch):
        answers = _Answers(monkeypatch)
        settings_editor._edit_field(_field(f"{SECTION}.Attempts"), {})
        assert f"1{EN}999" in answers.asked[0][0]

    def test_enter_with_no_change_writes_nothing(self, monkeypatch):
        _Answers(monkeypatch)
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Attempts"), data) is False
        assert data == {}

    def test_a_valid_value_is_written(self, monkeypatch):
        _Answers(monkeypatch, line="40")
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Attempts"), data) is True
        assert data == {SECTION: {"Attempts": 40}}

    def test_an_out_of_range_integer_is_rejected_and_the_old_value_kept(
        self, monkeypatch
    ):
        answers = _Answers(monkeypatch, line="1000")
        data = {SECTION: {"Attempts": 7}}
        assert settings_editor._edit_field(_field(f"{SECTION}.Attempts"), data) is False
        assert data == {SECTION: {"Attempts": 7}}
        assert f"Attempts must be 1{EN}999; got 1000" in answers.messages[0]

    def test_below_the_minimum_is_rejected_too(self, monkeypatch):
        answers = _Answers(monkeypatch, line="0")
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Attempts"), data) is False
        assert data == {}
        assert "got 0" in answers.messages[0]

    def test_non_numeric_text_is_rejected(self, monkeypatch):
        answers = _Answers(monkeypatch, line="lots")
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Attempts"), data) is False
        assert data == {}
        assert "whole number" in answers.messages[0]

    def test_a_boolean_is_a_choice_list_with_the_current_marked(self, monkeypatch):
        answers = _Answers(monkeypatch)
        settings_editor._edit_field(_field(f"{SECTION}.Flag"), {})
        _, options, _, start = answers.chosen[0]
        assert [o.split()[0] for o in options] == ["On", "Off"]
        assert "(current)" in options[0]
        assert start == 0

    def test_choosing_off_writes_false(self, monkeypatch):
        _Answers(monkeypatch, choice=1)
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Flag"), data) is True
        assert data == {SECTION: {"Flag": False}}

    def test_choosing_the_current_boolean_writes_nothing(self, monkeypatch):
        _Answers(monkeypatch, choice=0)
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Flag"), data) is False
        assert data == {}

    def test_an_enum_marks_the_current_choice(self, monkeypatch):
        answers = _Answers(monkeypatch)
        settings_editor._edit_field(_field(f"{SECTION}.Color"), {})
        _, options, _, start = answers.chosen[0]
        assert "(current)" in options[0]
        assert "(current)" not in options[1]
        assert start == 0

    def test_an_enum_list_starts_from_the_default_selection(self, monkeypatch):
        answers = _Answers(monkeypatch)
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Colors"), data) is False
        assert answers.chosen[0][1] == ["[x] Red", "[x] Blue"]
        assert data == {}

    def test_toggling_an_enum_list_member_writes_the_new_selection(self, monkeypatch):
        replies = iter([0, -1])  # untick Red, then Done
        monkeypatch.setattr(prompt, "choose", lambda *a, **k: next(replies))
        data: dict = {}
        assert settings_editor._edit_field(_field(f"{SECTION}.Colors"), data) is True
        assert data == {SECTION: {"Colors": ["Blue"]}}


class TestSave:
    def test_a_failed_write_keeps_the_operator_in_the_editor(
        self, monkeypatch, tmp_path
    ):
        messages: list[str] = []
        monkeypatch.setattr(prompt, "message", messages.append)

        def boom(path, data):
            raise OSError("disk full")

        monkeypatch.setattr(settings_file, "write", boom)
        saved = settings_editor._save(tmp_path / "x.toml", {}, Outer, None)
        assert saved is False
        assert "disk full" in messages[0]

    def test_a_good_write_reports_success_and_calls_back(self, tmp_path):
        called: list[bool] = []
        path = tmp_path / "x.toml"
        saved = settings_editor._save(
            path, {SECTION: {"Attempts": 3}}, Outer, lambda: called.append(True)
        )
        assert saved is True
        assert called == [True]
        assert settings_file.read(path) == {SECTION: {"Attempts": 3}}

    def test_invalid_data_is_not_written(self, monkeypatch, tmp_path):
        messages: list[str] = []
        monkeypatch.setattr(prompt, "message", messages.append)
        path = tmp_path / "x.toml"
        saved = settings_editor._save(path, {SECTION: {"Attempts": 5000}}, Outer, None)
        assert saved is False
        assert not path.exists()
        assert "NOT saved" in messages[0]


class TestPromptMessage:
    def test_the_function_exists(self):
        assert callable(prompt.message)

    def test_it_prints_when_there_is_no_screen(self, monkeypatch, capsys):
        monkeypatch.setattr(tui, "is_available", lambda: False)
        prompt.message("hello there")
        assert "hello there" in capsys.readouterr().out

    def test_it_uses_the_screen_when_there_is_one(self, monkeypatch, capsys):
        shown: list[str] = []
        monkeypatch.setattr(tui, "is_available", lambda: True)
        monkeypatch.setattr(tui, "message", shown.append)
        prompt.message("hello there")
        assert shown == ["hello there"]
        assert capsys.readouterr().out == ""

    def test_help_text_is_printed_before_a_fallback_prompt(self, monkeypatch, capsys):
        monkeypatch.setattr(tui, "is_available", lambda: False)
        monkeypatch.setattr("builtins.input", lambda _: "")
        prompt.ask_line("Attempts", "5", help_text="Explains attempts.")
        assert "Explains attempts." in capsys.readouterr().out


class TestUnionCampaignRealModel:
    """The user-reported case: six rows that all read "None"."""

    @pytest.fixture
    def rows(self) -> list[str]:
        fields = [
            f
            for f in settings_editor.collect_fields(Settings)
            if f.section == "Union Campaign"
        ]
        return settings_editor.section_rows(fields, {})

    def test_it_has_six_fields(self, rows):
        assert len(rows) == 6

    def test_every_row_shows_its_default(self, rows):
        expected = [
            ("Attempts", "5"),
            ("Formations", "10"),
            ("Suggested Formations", "On"),
            ("Start with current Formation", "On"),
            ("Skip Manual Formations", "Off"),
            ("Run Manual Formations Last", "On"),
        ]
        for row, (label, value) in zip(rows, expected, strict=True):
            assert row.startswith(label)
            assert row.split()[-1] != "None"
            assert "[default]" in row
            assert value in row.split("[default]")[0].split()[-1:]

    def test_every_field_has_help(self):
        for field in settings_editor.collect_fields(Settings):
            if field.section == "Union Campaign":
                assert settings_editor.help_text(field), field.dotted_key

    def test_spend_gold_stays_hidden(self):
        keys = {f.dotted_key for f in settings_editor.collect_fields(Settings)}
        assert "Union Campaign.Spend Gold" not in keys
