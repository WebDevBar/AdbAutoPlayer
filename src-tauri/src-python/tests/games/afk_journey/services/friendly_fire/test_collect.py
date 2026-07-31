"""Frame collection. Must work on a machine that is not the author's."""

import numpy as np
from adb_auto_player.games.afk_journey.services.friendly_fire.collect import (
    archive,
    collection_dir,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode


def test_the_directory_is_NOT_the_authors_vault_mount(monkeypatch):
    """An earlier draft named /mnt/vault, which no end user has."""
    monkeypatch.delenv("ADB_FRIENDLY_FIRE_DIR", raising=False)
    assert "/mnt/vault" not in str(collection_dir())


def test_it_honours_an_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    assert collection_dir() == tmp_path


def test_archiving_writes_a_named_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    out = archive(np.zeros((20, 20, 3), dtype=np.uint8), Mode.ARENA, "flagged-1")
    assert out is not None and out.exists()
    assert "arena" in out.name and "flagged-1" in out.name


def test_a_write_failure_never_raises(monkeypatch):
    """Collection is diagnostics. It must never cost a match."""
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", "/proc/nonexistent/nope")
    assert archive(np.zeros((5, 5, 3), dtype=np.uint8), Mode.ARENA, "x") is None
