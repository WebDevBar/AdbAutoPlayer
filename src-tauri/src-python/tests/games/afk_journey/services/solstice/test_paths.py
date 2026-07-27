"""Where the database lives.

A hardcoded developer path meant the mode ran on exactly one machine and failed
immediately on anyone else's install - which defeats shipping it to people.
"""

import os
from pathlib import Path

from adb_auto_player.games.afk_journey.services.solstice.paths import (
    bundled_db,
    solstice_db_path,
    solstice_icon_dir,
    user_data_dir,
)


def test_no_developer_path_is_hardcoded():
    """The regression this module exists to prevent."""
    src = Path(__file__).resolve().parents[5] / "adb_auto_player/games/afk_journey"
    banned = (
        "/mnt/docs/adbautoplayer",
        "/home/toshe/Dev",
        "Dev/webdevbar/adbautoplayer",
        # The icon library was built from this vault path, which exists on one
        # machine. Everywhere else the library was empty and every hero read
        # came back `unknown` - silently.
        "/mnt/vault",
    )
    for f in (src / "mixins/solstice_clash.py",
              src / "services/solstice/paths.py"):
        text = f.read_text()
        for path in banned:
            assert path not in text, f"{f} hardcodes {path}"


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


def test_the_committed_seed_carries_no_per_machine_data():
    """The seed ships to contributors. Anything machine-specific in it travels.

    identification_audit once held 548 rows with absolute paths under the
    developer's vault, and an install row whose UUID every contributor would
    then claim as their own.
    """
    import sqlite3

    seed = bundled_db()
    assert seed is not None
    con = sqlite3.connect(seed)
    for table in ("install", "match", "match_hero", "match_pool", "match_odds",
                  "identification_audit", "hero_screen_transform"):
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n == 0, f"{table} has {n} rows - run data/solstice_clash/strip_seed.py"


def test_the_committed_seed_still_carries_the_reference_data():
    """The pooled API serves matches only - no roster, no cell geometry. Strip
    too much and a fresh install cannot identify a hero at all."""
    import sqlite3

    con = sqlite3.connect(bundled_db())
    for table, minimum in (("hero", 100), ("solstice_roster", 100),
                           ("cell_registry", 20), ("art_transform", 1),
                           ("theme", 1)):
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert n >= minimum, f"{table} has only {n}"


def test_seeding_scrubs_any_per_machine_rows_that_slipped_through(tmp_path, monkeypatch):
    """Belt and braces: even if a polluted bundle shipped, the copy is cleaned."""
    import sqlite3

    polluted = tmp_path / "bundle.sqlite"
    import shutil as _shutil

    _shutil.copy2(bundled_db(), polluted)
    con = sqlite3.connect(polluted)
    con.execute(
        "INSERT INTO install(id,instance_uuid,created_at) VALUES(1,'someone-else','x')"
    )
    con.commit()
    con.close()

    monkeypatch.setenv("ADB_SOLSTICE_BUNDLED_DB", str(polluted))
    monkeypatch.setenv("ADB_SOLSTICE_DATA_DIR", str(tmp_path / "user"))
    target = solstice_db_path()

    rows = sqlite3.connect(target).execute("SELECT COUNT(*) FROM install").fetchone()[0]
    assert rows == 0, "a bundled install identity would be claimed by every contributor"


def test_the_hero_icons_are_bundled():
    """Without these every cell reads `unknown` and every match is worthless.

    The failure is silent at the call site: `identify_cell` cannot tell an empty
    library from an unreadable frame, so a build with no icons collects matches
    that hold no heroes, never earn a natural_key, and never sync.
    """
    icons = solstice_icon_dir()
    assert icons is not None, "bundled icon directory not found"
    found = list((icons / "hero").glob("spui_herohead_*.png"))
    assert len(found) > 100, f"only {len(found)} hero icons bundled"


def test_the_icon_directory_honours_its_override(tmp_path, monkeypatch):
    """Kept so a developer can point at a fuller extract without editing code."""
    (tmp_path / "hero").mkdir()
    monkeypatch.setenv("ADB_SOLSTICE_ICON_DIR", str(tmp_path))
    assert solstice_icon_dir() == tmp_path


def test_a_missing_icon_directory_is_reported_not_guessed(tmp_path, monkeypatch):
    """An override pointing nowhere must fall through, never return a bad path."""
    monkeypatch.setenv("ADB_SOLSTICE_ICON_DIR", str(tmp_path / "nope"))
    resolved = solstice_icon_dir()
    assert resolved is None or (resolved / "hero").is_dir()
