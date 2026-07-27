"""Where the database lives.

A hardcoded developer path meant the mode ran on exactly one machine and failed
immediately on anyone else's install - which defeats shipping it to people.
"""

import os
from pathlib import Path

from adb_auto_player.games.afk_journey.services.solstice.paths import (
    bundled_db,
    solstice_db_path,
    user_data_dir,
)


def test_no_developer_path_is_hardcoded():
    """The regression this module exists to prevent."""
    src = Path(__file__).resolve().parents[5] / "adb_auto_player/games/afk_journey"
    for f in (src / "mixins/solstice_clash.py",
              src / "services/solstice/paths.py"):
        assert "/mnt/docs/adbautoplayer" not in f.read_text(), f


def test_user_dir_is_writable_and_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_SOLSTICE_DATA_DIR", str(tmp_path / "custom"))
    assert user_data_dir() == tmp_path / "custom"


def test_xdg_is_honoured_on_linux(tmp_path, monkeypatch):
    monkeypatch.delenv("ADB_SOLSTICE_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    if os.name != "nt":
        assert user_data_dir() == tmp_path / "AdbAutoPlayer"


def test_first_run_seeds_from_the_bundled_database(tmp_path, monkeypatch):
    """A fresh install must get the roster and cell geometry, not an empty file."""
    monkeypatch.setenv("ADB_SOLSTICE_DATA_DIR", str(tmp_path))
    seed = bundled_db()
    assert seed is not None, "bundled database not found"

    target = solstice_db_path()
    assert target.is_file()
    assert target.stat().st_size > 0

    import sqlite3

    assert sqlite3.connect(target).execute(
        "SELECT COUNT(*) FROM hero"
    ).fetchone()[0] > 0


def test_seeding_does_not_overwrite_existing_data(tmp_path, monkeypatch):
    """The contributor's collected matches must survive every later call."""
    monkeypatch.setenv("ADB_SOLSTICE_DATA_DIR", str(tmp_path))
    first = solstice_db_path()
    first.write_bytes(b"sentinel-not-a-real-database")
    assert solstice_db_path().read_bytes() == b"sentinel-not-a-real-database"


def test_the_bundled_copy_is_not_moved(tmp_path, monkeypatch):
    """Seeding copies. Moving would break the next fresh install and, on a
    packaged build, try to write into a read-only location."""
    monkeypatch.setenv("ADB_SOLSTICE_DATA_DIR", str(tmp_path))
    seed = bundled_db()
    solstice_db_path()
    assert seed is not None and seed.is_file()
