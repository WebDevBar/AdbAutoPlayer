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
    """Callers already handle absence; the resolver must not turn it into an error.

    With no App.toml anywhere the layout is treated as FLAT, so the path returned is
    inside the given directory rather than its parent. This reverses the earlier
    two-branch rule on purpose: that rule was a READ rule, and using it to decide
    where to WRITE meant a flat directory whose App.toml did not exist yet would be
    classed two-level, and the settings editor would create App.toml one level too
    high - misdetecting that directory permanently afterwards.
    """
    target = tmp_path / "0"
    SettingsLoader.set_app_config_dir(target)
    assert SettingsLoader.app_settings_path() == target / "App.toml"


def test_active_profile_selects_the_profile_directory(tmp_path: Path):
    """The profile comes from App.toml, never from a hardcoded 0."""
    (tmp_path / "App.toml").write_text("[profiles]\nactive_profile = 1\n")
    (tmp_path / "0").mkdir()
    (tmp_path / "1").mkdir()
    SettingsLoader.set_app_config_dir(tmp_path)

    root, profile_dir = SettingsLoader.resolve_layout()

    assert root == tmp_path
    assert profile_dir == tmp_path / "1"


def test_active_profile_defaults_to_zero_when_absent(tmp_path: Path):
    (tmp_path / "App.toml").write_text('[ui]\ntheme = "terminus"\n')
    (tmp_path / "0").mkdir()
    SettingsLoader.set_app_config_dir(tmp_path)

    assert SettingsLoader.resolve_layout()[1] == tmp_path / "0"


def test_flat_layout_ignores_active_profile_with_no_numbered_dir(cli_layout: Path):
    """<repo>/src-tauri/settings has App.toml but no 0/ - root and profile coincide."""
    SettingsLoader.set_app_config_dir(cli_layout)

    root, profile_dir = SettingsLoader.resolve_layout()

    assert root == cli_layout
    assert profile_dir == cli_layout


def test_profile_dir_input_still_resolves_the_root(gui_layout: Path):
    """The GUI passes the profile dir per task; that must keep working."""
    SettingsLoader.set_app_config_dir(gui_layout / "0")

    root, profile_dir = SettingsLoader.resolve_layout()

    assert root == gui_layout
    assert profile_dir == gui_layout / "0"


def test_unreadable_app_toml_falls_back_to_profile_zero(tmp_path: Path):
    """A corrupt App.toml must not make the resolver raise while locating settings."""
    (tmp_path / "App.toml").write_text("this is not = = valid toml [[[")
    (tmp_path / "0").mkdir()
    SettingsLoader.set_app_config_dir(tmp_path)

    assert SettingsLoader.resolve_layout()[1] == tmp_path / "0"
