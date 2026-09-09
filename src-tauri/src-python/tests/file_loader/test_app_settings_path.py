"""App.toml must be found in BOTH layouts, or the CLI silently runs on defaults.

The GUI keeps `App.toml` in the config root and everything else in a per-profile
subdirectory, so `app_config_dir` means the profile directory. The CLI is handed a flat
directory holding all three TOMLs. Callers hardcoded `get_app_config_dir().parent`,
which is right for the GUI and walks one level too high for the CLI.

The consequence was invisible: `Game.app_settings` catches every exception and
`from_toml` returns defaults for a missing file, so on 2026-09-09 every CLI run used
default `template_timeout`, `action_delay` and `navigation_delay` no matter what the
operator's App.toml said. Nothing failed; the settings just did not apply.
"""

from pathlib import Path

import pytest

from adb_auto_player.file_loader import SettingsLoader
from adb_auto_player.models.pydantic.app_settings import AppSettings

_TIMEOUT = 42.0


@pytest.fixture
def gui_layout(tmp_path: Path) -> Path:
    """Config root with App.toml, plus a profile subdirectory."""
    (tmp_path / "App.toml").write_text(f"[advanced]\ntemplate_timeout = {_TIMEOUT}\n")
    (tmp_path / "0").mkdir()
    return tmp_path


@pytest.fixture
def cli_layout(tmp_path: Path) -> Path:
    """One flat directory holding App.toml alongside the rest."""
    (tmp_path / "App.toml").write_text(f"[advanced]\ntemplate_timeout = {_TIMEOUT}\n")
    return tmp_path


def test_gui_layout_finds_app_toml_in_the_parent(gui_layout: Path):
    SettingsLoader.set_app_config_dir(gui_layout / "0")
    found = SettingsLoader.app_settings_path()
    assert found == gui_layout / "App.toml"
    assert AppSettings.from_toml(found).advanced.template_timeout == _TIMEOUT


def test_cli_layout_finds_app_toml_alongside(cli_layout: Path):
    """The case that was broken: no profile directory, so no parent to walk to."""
    SettingsLoader.set_app_config_dir(cli_layout)
    found = SettingsLoader.app_settings_path()
    assert found == cli_layout / "App.toml"
    assert AppSettings.from_toml(found).advanced.template_timeout == _TIMEOUT


def test_a_missing_file_still_returns_a_path_rather_than_raising(tmp_path: Path):
    """Callers already handle absence; the resolver must not turn it into an error."""
    SettingsLoader.set_app_config_dir(tmp_path / "0")
    assert SettingsLoader.app_settings_path() == tmp_path / "App.toml"
