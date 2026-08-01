"""The repair flips only sides and outcome, needs two phases, and is atomic."""

import sqlite3
from pathlib import Path

import pytest

from adb_auto_player.games.afk_journey.services.solstice.frameside import (
    Verdict,
    classify,
)
from scripts import solstice_frame_side_audit as mod
from scripts.solstice_frame_side_audit import Row, snapshot, swap_sides

_SCHEMA = """
CREATE TABLE match(
  id INTEGER PRIMARY KEY, outcome TEXT, predicted_left REAL,
  left_player TEXT, right_player TEXT, left_rating INTEGER, right_rating INTEGER,
  left_rank INTEGER, right_rank INTEGER);
CREATE TABLE match_hero(
  id INTEGER PRIMARY KEY, match_id INTEGER, side TEXT, slot INTEGER, hero_slug TEXT,
  UNIQUE(match_id, side, slot));
"""

_LEFT = ("lorsan", "sonja", "valka")
_RIGHT = ("hepler", "silven", "thador")
# A draft frame carries five picks, so the red trio always comes back one short.
_RED_OF_LEFT = ("lorsan", "sonja")
_RED_OF_RIGHT = ("hepler", "silven")

_PROTECTED = (
    "SELECT predicted_left, left_player, right_player, left_rating, right_rating,"
    " left_rank, right_rank FROM match WHERE id=?"
)


def _insert_match(con: sqlite3.Connection, match_id: int) -> None:
    con.execute(
        "INSERT INTO match(id, outcome, predicted_left, left_player, right_player,"
        " left_rating, right_rating, left_rank, right_rank)"
        " VALUES (?,'left',0.72,'MERLIN','Elithes',4341,4382,12,7)",
        (match_id,),
    )
    for slot, slug in enumerate(("sonja", "lorsan", "valka"), start=1):
        con.execute(
            "INSERT INTO match_hero(match_id, side, slot, hero_slug)"
            " VALUES (?,'left',?,?)",
            (match_id, slot, slug),
        )
    for slot, slug in enumerate(("thador", "silven", "hepler"), start=1):
        con.execute(
            "INSERT INTO match_hero(match_id, side, slot, hero_slug)"
            " VALUES (?,'right',?,?)",
            (match_id, slot, slug),
        )


def _db(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.executescript(_SCHEMA)
    _insert_match(con, 1)
    con.commit()
    return con


def _db_file(tmp_path: Path) -> Path:
    con = _db(tmp_path)
    con.close()
    return tmp_path / "t.sqlite"


def _state(db: Path, match_id: int = 1) -> tuple:
    """The stored orientation of one match, read through a fresh connection."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        sides = {"left": set(), "right": set()}
        for side, slug in con.execute(
            "SELECT side, hero_slug FROM match_hero WHERE match_id=?", (match_id,)
        ):
            sides[side].add(slug)
        outcome = con.execute(
            "SELECT outcome FROM match WHERE id=?", (match_id,)
        ).fetchone()[0]
    finally:
        con.close()
    return frozenset(sides["left"]), frozenset(sides["right"]), outcome


def _row(db: Path, frame_blue: tuple, frame_red: tuple, match_id: int = 1) -> Row:
    """An audit row for the CURRENT stored state, classified as the audit would."""
    left, right, outcome = _state(db, match_id)
    verdict = classify(frozenset(frame_blue), frozenset(frame_red), left, right)
    return Row(
        match_id=match_id,
        verdict=verdict,
        one_sided=verdict,
        captured_at="2026-07-30T10:00:00Z",
        outcome=outcome,
        row_left=tuple(sorted(left)),
        row_right=tuple(sorted(right)),
        frame_blue=frame_blue,
        frame_red=frame_red,
        natural_key=None,
        predicted_left=0.72,
        left_rating=4341,
        right_rating=4382,
        has_duplicate=False,
    )


def test_naive_single_update_violates_the_unique_constraint(tmp_path):
    con = _db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE match_hero SET side = CASE side WHEN 'left' THEN 'right'"
            " ELSE 'left' END WHERE match_id=1"
        )


def test_swap_flips_sides_and_outcome(tmp_path):
    con = _db(tmp_path)
    swap_sides(con, 1)
    left = {
        r[0]
        for r in con.execute(
            "SELECT hero_slug FROM match_hero WHERE match_id=1 AND side='left'"
        )
    }
    assert left == {"thador", "silven", "hepler"}
    assert con.execute("SELECT outcome FROM match WHERE id=1").fetchone()[0] == "right"


def test_swap_leaves_prediction_players_ratings_and_ranks_alone(tmp_path):
    con = _db(tmp_path)
    before = con.execute(_PROTECTED, (1,)).fetchone()
    swap_sides(con, 1)
    after = con.execute(_PROTECTED, (1,)).fetchone()
    assert before == after


def test_swap_applied_twice_returns_the_original(tmp_path):
    con = _db(tmp_path)
    swap_sides(con, 1)
    swap_sides(con, 1)
    left = {
        r[0]
        for r in con.execute(
            "SELECT hero_slug FROM match_hero WHERE match_id=1 AND side='left'"
        )
    }
    assert left == {"sonja", "lorsan", "valka"}
    assert con.execute("SELECT outcome FROM match WHERE id=1").fetchone()[0] == "left"


def test_swap_does_not_commit(tmp_path):
    """The caller owns the transaction; a per-row commit is what broke atomicity."""
    con = _db(tmp_path)
    swap_sides(con, 1)
    assert _state(tmp_path / "t.sqlite")[0] == frozenset(_LEFT)
    con.rollback()
    con.close()
    assert _state(tmp_path / "t.sqlite") == (
        frozenset(_LEFT),
        frozenset(_RIGHT),
        "left",
    )


def test_snapshot_verifies_the_copy(tmp_path):
    """A snapshot nobody checked is a file, not a snapshot."""
    db = tmp_path / "heroes.sqlite"
    con = _db(tmp_path)
    con.close()
    (tmp_path / "t.sqlite").rename(db)

    backup = snapshot(db)
    assert backup.is_file()
    copy = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    assert copy.execute("SELECT COUNT(*) FROM match_hero").fetchone()[0] == 6


def test_apply_twice_leaves_the_repaired_state_unchanged(tmp_path):
    """The second pass sees `agree` and writes nothing at all."""
    db = _db_file(tmp_path)
    first = _row(db, _RIGHT, _RED_OF_LEFT)
    assert first.verdict is Verdict.MIRRORED
    assert mod.repair(db, [first], tmp_path / "one.log") == 1
    repaired = _state(db)
    assert repaired == (frozenset(_RIGHT), frozenset(_LEFT), "right")

    second = _row(db, _RIGHT, _RED_OF_LEFT)
    assert second.verdict is Verdict.AGREE
    second_log = tmp_path / "two.log"
    assert mod.repair(db, [second], second_log) == 0
    assert _state(db) == repaired
    assert not second_log.exists()


def test_zero_mirrored_rows_means_zero_writes(tmp_path):
    db = _db_file(tmp_path)
    before = db.read_bytes()
    row = _row(db, _LEFT, _RED_OF_RIGHT)
    assert row.verdict is Verdict.AGREE
    log = tmp_path / "none.log"

    assert mod.repair(db, [row], log) == 0
    assert db.read_bytes() == before
    assert not log.exists()
    assert not list(tmp_path.glob("*.bak-*"))


def test_a_failed_snapshot_aborts_before_any_write(tmp_path, monkeypatch):
    db = _db_file(tmp_path)
    before = db.read_bytes()
    row = _row(db, _RIGHT, _RED_OF_LEFT)
    assert row.verdict is Verdict.MIRRORED

    def _boom(_path: Path) -> Path:
        raise RuntimeError("snapshot will not open")

    monkeypatch.setattr(mod, "snapshot", _boom)
    log = tmp_path / "aborted.log"
    with pytest.raises(RuntimeError):
        mod.repair(db, [row], log)
    assert db.read_bytes() == before
    assert not log.exists()


def test_a_failure_partway_through_rolls_the_whole_repair_back(tmp_path):
    """One transaction: no row is left flipped when a later step fails."""
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.executescript(_SCHEMA)
    _insert_match(con, 1)
    _insert_match(con, 2)
    con.commit()
    con.close()
    db = tmp_path / "t.sqlite"
    before = db.read_bytes()

    rows = [_row(db, _RIGHT, _RED_OF_LEFT, 1), _row(db, _RIGHT, _RED_OF_LEFT, 2)]
    assert all(r.verdict is Verdict.MIRRORED for r in rows)

    # The log write is the last step before the commit; a path that cannot exist makes
    # it fail after BOTH matches have been swapped.
    with pytest.raises(OSError):
        mod.repair(db, rows, tmp_path / "no-such-dir" / "r.log")

    assert db.read_bytes() == before
    assert _state(db, 1) == (frozenset(_LEFT), frozenset(_RIGHT), "left")
    assert _state(db, 2) == (frozenset(_LEFT), frozenset(_RIGHT), "left")
