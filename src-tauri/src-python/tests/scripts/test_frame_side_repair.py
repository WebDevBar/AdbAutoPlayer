"""The repair flips only sides and outcome, and needs two phases to do it."""

import sqlite3
from pathlib import Path

import pytest

from scripts.solstice_frame_side_audit import snapshot, swap_sides

_SCHEMA = """
CREATE TABLE match(
  id INTEGER PRIMARY KEY, outcome TEXT, predicted_left REAL,
  left_player TEXT, right_player TEXT, left_rating INTEGER, right_rating INTEGER);
CREATE TABLE match_hero(
  id INTEGER PRIMARY KEY, match_id INTEGER, side TEXT, slot INTEGER, hero_slug TEXT,
  UNIQUE(match_id, side, slot));
"""


def _db(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO match(id, outcome, predicted_left, left_player, right_player,"
        " left_rating, right_rating)"
        " VALUES (1,'left',0.72,'MERLIN','Elithes',4341,4382)"
    )
    for slot, slug in ((1, "sonja"), (2, "lorsan"), (3, "valka")):
        con.execute(
            "INSERT INTO match_hero(match_id, side, slot, hero_slug)"
            " VALUES (1,'left',?,?)",
            (slot, slug),
        )
    for slot, slug in ((1, "thador"), (2, "silven"), (3, "hepler")):
        con.execute(
            "INSERT INTO match_hero(match_id, side, slot, hero_slug)"
            " VALUES (1,'right',?,?)",
            (slot, slug),
        )
    con.commit()
    return con


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


def test_swap_leaves_prediction_players_and_ratings_alone(tmp_path):
    con = _db(tmp_path)
    before = con.execute(
        "SELECT predicted_left, left_player, right_player, left_rating, right_rating"
        " FROM match WHERE id=1"
    ).fetchone()
    swap_sides(con, 1)
    after = con.execute(
        "SELECT predicted_left, left_player, right_player, left_rating, right_rating"
        " FROM match WHERE id=1"
    ).fetchone()
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
