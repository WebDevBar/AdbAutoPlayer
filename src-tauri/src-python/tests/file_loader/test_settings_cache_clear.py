"""Clearing a settings cache group must actually re-read the file.

`SettingsLoader.adb_settings` and `.app_settings` used to apply
`@profile_aware_cache` to the OUTER function and `@register_cache` to an inner
`_load` closure. The closure has no `cache_clear` attribute, so the registry held
something the clear loop skipped and every clear was a silent no-op.

That was invisible in the GUI because every task runs in a fresh subprocess. It
matters the moment one interpreter serves several runs, which is what the CLI menu
does: edit ADB.toml, run a task, and the task would use the OLD device.

`cache_clear` also cannot be imported from `decorators` here - that package pulls in
`util`, which imports `file_loader`. It lives in `registries` for that reason, and
this test locks in both the ordering and the import path.
"""

from pathlib import Path

import pytest

from adb_auto_player.file_loader import SettingsLoader
from adb_auto_player.models.decorators import CacheGroup
from adb_auto_player.registries import CACHE_REGISTRY, cache_clear

# Deliberately NOT the model default ("127.0.0.1:5555"): a test using the default
# passes even when the file is never read at all.
_FIRST = "127.0.0.1:7771"
_SECOND = "127.0.0.1:7772"


def _write_adb_toml(directory: Path, device_id: str) -> None:
    (directory / "ADB.toml").write_text(f'[device]\nid = "{device_id}"\n')


@pytest.fixture
def settings_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A flat config dir holding ADB.toml, with the cache cleared around the test."""
    _write_adb_toml(tmp_path, _FIRST)
    monkeypatch.setattr(
        SettingsLoader, "get_app_config_dir", staticmethod(lambda: tmp_path)
    )
    cache_clear(CacheGroup.ADB_SETTINGS)
    yield tmp_path
    cache_clear(CacheGroup.ADB_SETTINGS)


def test_registered_function_exposes_cache_clear() -> None:
    """The registry must hold the cached wrapper, not a bare closure."""
    registered = CACHE_REGISTRY.get(CacheGroup.ADB_SETTINGS, [])
    assert registered, "adb_settings did not register under ADB_SETTINGS"
    for func, _ in registered:
        assert hasattr(func, "cache_clear"), (
            f"{func!r} has no cache_clear, so clearing this group is a no-op. "
            "@register_cache must sit BELOW @staticmethod and ABOVE "
            "@profile_aware_cache - a staticmethod object does not proxy attributes."
        )


def test_cache_clear_makes_the_next_read_see_the_new_file(settings_dir: Path) -> None:
    """Edit ADB.toml, clear the group, and the next read must return the new value."""
    assert SettingsLoader.adb_settings().device.id == _FIRST

    _write_adb_toml(settings_dir, _SECOND)
    assert SettingsLoader.adb_settings().device.id == _FIRST, "expected a cached read"

    cache_clear(CacheGroup.ADB_SETTINGS)
    assert SettingsLoader.adb_settings().device.id == _SECOND


def test_app_settings_is_registered_the_same_way() -> None:
    """app_settings had the identical inversion and must not regress either."""
    registered = CACHE_REGISTRY.get(CacheGroup.APP_SETTINGS, [])
    assert registered, "app_settings did not register under APP_SETTINGS"
    for func, _ in registered:
        assert hasattr(func, "cache_clear")
