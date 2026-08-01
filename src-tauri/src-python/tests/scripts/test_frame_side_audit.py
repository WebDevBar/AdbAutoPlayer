"""The audit is read-only and separates its verdict classes."""

import sqlite3
from pathlib import Path

import pytest

from adb_auto_player.games.afk_journey.services.solstice.frameside import Verdict
from scripts import solstice_frame_side_audit as mod
from scripts.solstice_frame_side_audit import open_readonly, parse_timestamp, summarise

_SCHEMA = """
CREATE TABLE event(id INTEGER PRIMARY KEY, slug TEXT);
CREATE TABLE match(
  id INTEGER PRIMARY KEY, event_id INTEGER, natural_key TEXT, captured_at TEXT,
  outcome TEXT, origin TEXT, pushed_at TEXT, predicted_left REAL,
  left_rating INTEGER, right_rating INTEGER);
CREATE TABLE match_hero(
  id INTEGER PRIMARY KEY, match_id INTEGER, side TEXT, slot INTEGER, hero_slug TEXT);
"""

_LEFT = ("lorsan", "sonja", "valka")
_RIGHT = ("hepler", "silven", "thador")


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


def test_a_frame_with_no_stored_row_is_dropped_not_classified(tmp_path, monkeypatch):
    """There is no `no_row` verdict: such a frame never reaches `classify`."""
    db = tmp_path / "heroes.sqlite"
    con = sqlite3.connect(db)
    con.executescript(_SCHEMA)
    con.execute("INSERT INTO event(id, slug) VALUES (1,'solstice-clash')")
    con.execute(
        "INSERT INTO match(id, event_id, natural_key, captured_at, outcome, origin,"
        " pushed_at, predicted_left, left_rating, right_rating)"
        " VALUES (1,1,'nk-1','2026-07-30T10:00:00Z','left','local',NULL,0.72,1,2)"
    )
    for side, trio in (("left", _LEFT), ("right", _RIGHT)):
        for slot, slug in enumerate(trio, start=1):
            con.execute(
                "INSERT INTO match_hero(match_id, side, slot, hero_slug)"
                " VALUES (1,?,?,?)",
                (side, slot, slug),
            )
    con.commit()
    con.close()

    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "draft-1.png").write_bytes(b"")
    (frames / "draft-999.png").write_bytes(b"")

    handed_to_the_reader: list[int] = []

    def _fake_reads(work, db_path, icon_dir, workers):
        handed_to_the_reader.extend(match_id for match_id, _ in work)
        return [(match_id, list(_LEFT), list(_RIGHT[:2])) for match_id, _ in work]

    monkeypatch.setattr(mod, "solstice_icon_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "_run_reads", _fake_reads)

    rows = mod.audit(db, frames, parse_timestamp("2026-12-31T00:00:00Z"))

    assert handed_to_the_reader == [1]
    assert [row.match_id for row in rows] == [1]
    assert rows[0].verdict is Verdict.AGREE
    assert "NO_ROW" not in Verdict.__members__
