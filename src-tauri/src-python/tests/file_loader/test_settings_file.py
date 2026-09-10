"""Editing one setting must not disturb anything else in the file.

The three failure modes these lock in are all real in this repo, not hypothetical:
keys no model declares (App.toml has two), legacy plain-string task entries that a
field_validator rewrites on load, and partial writes that from_toml would swallow
into defaults.
"""

import tomllib
from pathlib import Path

import pytest
from adb_auto_player.file_loader import settings_file
from adb_auto_player.models.pydantic.app_settings import AppSettings
from pydantic import ValidationError

_APP_TOML = """\
[profiles]
profiles = [
    "Default",
]
active_profile = 0

[ui]
theme = "terminus"
locale = "en"
close_should_minimize = false
notifications_enabled = false

[logging]
level = "INFO"
action_log_limit = 0
"""


@pytest.fixture
def app_toml(tmp_path: Path) -> Path:
    path = tmp_path / "App.toml"
    path.write_text(_APP_TOML)
    return path


def test_keys_no_model_declares_survive_a_round_trip(app_toml: Path):
    """notifications_enabled and action_log_limit are declared by NO model anywhere.

    Serialising AppSettings back would drop both. Writing the raw dict keeps them.
    """
    data = settings_file.read(app_toml)
    settings_file.set_in(data, "ui.theme", "catppuccin")
    settings_file.write(app_toml, data)

    written = tomllib.loads(app_toml.read_text())
    assert written["ui"]["theme"] == "catppuccin"
    assert written["ui"]["notifications_enabled"] is False
    assert written["logging"]["action_log_limit"] == 0


def test_untouched_values_keep_their_value(app_toml: Path):
    before = settings_file.read(app_toml)
    data = settings_file.read(app_toml)
    settings_file.set_in(data, "logging.level", "DEBUG")
    settings_file.write(app_toml, data)

    after = settings_file.read(app_toml)
    for section, table in before.items():
        for key, value in table.items():
            if (section, key) == ("logging", "level"):
                continue
            assert after[section][key] == value, f"{section}.{key} changed"


def test_legacy_string_tasks_are_written_back_as_strings(tmp_path: Path):
    """A field_validator coerces plain strings into dicts ON LOAD.

    Going through the model would rewrite the file's shape merely by opening it.
    """
    path = tmp_path / "AFKJourney.toml"
    path.write_text('[custom_routine_one]\ntasks = [\n    "Arena",\n]\n')

    data = settings_file.read(path)
    settings_file.set_in(data, "custom_routine_one.repeat", True)
    settings_file.write(path, data)

    assert tomllib.loads(path.read_text())["custom_routine_one"]["tasks"] == ["Arena"]


def test_validate_raises_rather_than_falling_back_to_defaults():
    """from_toml swallows everything; the save path must not."""
    with pytest.raises(ValidationError):
        settings_file.validate({"logging": {"level": 12345}}, AppSettings)


def test_validate_accepts_a_good_table(app_toml: Path):
    validated = settings_file.validate(settings_file.read(app_toml), AppSettings)
    assert isinstance(validated, AppSettings)


def test_read_raises_on_malformed_toml(tmp_path: Path):
    """A caller about to write must know it is working from a real parse."""
    path = tmp_path / "broken.toml"
    path.write_text("not = = toml [[[")
    with pytest.raises(tomllib.TOMLDecodeError):
        settings_file.read(path)


def test_read_returns_empty_for_a_missing_file(tmp_path: Path):
    assert settings_file.read(tmp_path / "absent.toml") == {}


def test_write_leaves_no_temp_files_behind(app_toml: Path):
    settings_file.write(app_toml, settings_file.read(app_toml))
    assert [p.name for p in app_toml.parent.iterdir()] == ["App.toml"]


def test_a_failed_write_leaves_the_original_intact(app_toml: Path):
    """tomli-w cannot serialise arbitrary objects; the original must survive."""
    data = settings_file.read(app_toml)
    settings_file.set_in(data, "ui.theme", object())

    with pytest.raises(Exception):
        settings_file.write(app_toml, data)

    assert app_toml.read_text() == _APP_TOML
    assert [p.name for p in app_toml.parent.iterdir()] == ["App.toml"]


def test_set_in_creates_missing_tables(tmp_path: Path):
    data: dict = {}
    settings_file.set_in(data, "advanced.template_timeout", 42.0)
    assert data == {"advanced": {"template_timeout": 42.0}}


def test_set_in_replaces_a_non_table_parent(tmp_path: Path):
    data: dict = {"advanced": "not a table"}
    settings_file.set_in(data, "advanced.template_timeout", 1.0)
    assert data == {"advanced": {"template_timeout": 1.0}}


def test_get_in_returns_the_default_for_a_missing_path():
    assert settings_file.get_in({"ui": {}}, "ui.theme", "fallback") == "fallback"
    assert settings_file.get_in({"ui": 3}, "ui.theme", "fallback") == "fallback"
