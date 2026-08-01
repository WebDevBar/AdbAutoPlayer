"""The audit is read-only and separates its verdict classes."""

import sqlite3
from pathlib import Path

import pytest

from adb_auto_player.games.afk_journey.services.solstice.frameside import Verdict
from scripts.solstice_frame_side_audit import open_readonly, summarise


def test_connection_is_read_only(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    sqlite3.connect(db).execute("CREATE TABLE t(a INTEGER)")
    con = open_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO t(a) VALUES (1)")


def test_summarise_counts_each_verdict_separately():
    counts = summarise(
        [Verdict.AGREE, Verdict.AGREE, Verdict.MIRRORED, Verdict.INCOMPLETE]
    )
    assert counts[Verdict.AGREE] == 2
    assert counts[Verdict.MIRRORED] == 1
    assert counts[Verdict.INCOMPLETE] == 1
    assert counts[Verdict.PARTIAL] == 0
