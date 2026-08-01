"""Rows left unclassified on a database that is ALREADY canonical.

`record_match`, `record_heroes` and `finalise_summary` commit separately, so a crash
between them leaves a row with `canonical_state IS NULL` and possibly no heroes and no
pointers. The migration used to bless every such row as `canonical` without looking at
it, which puts a half-written match into the fit and the pool.

The existing reshape tests only build LEGACY-shaped databases, so they passed with that
defect present - which is why these exist separately.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

TRIO_A = ("aliceth", "alna", "alsa")  # sorts first
TRIO_B = ("antandra", "arden", "atalanta")


@pytest.fixture
def migrate():
    root = Path(__file__).resolve()
    while root.name != "adbautoplayer":
        root = root.parent
    spec = importlib.util.spec_from_file_location(
        "_migrate", root / "data" / "solstice_clash" / "migrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def canonical_db(tmp_path, migrate):
    """A fresh database in the canonical shape, with the roster seeded."""
    db = tmp_path / "heroes.sqlite"
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT OR IGNORE INTO event(id, slug, name, game)"
        " VALUES (1, 'solstice-clash', 'Solstice Clash', 'afk-journey')"
    )
    for slug in TRIO_A + TRIO_B:
        con.execute(
            "INSERT OR IGNORE INTO hero(slug, name) VALUES (?, ?)", (slug, slug.title())
        )
    con.commit()
    con.close()
    return db


def _leftover(db, *, heroes=True, winning_trio=None):
    """One row with canonical_state NULL, as an interrupted recording leaves it."""
    con = sqlite3.connect(db)
    cur = con.execute(
        "INSERT INTO match(source, captured_at, origin, winning_trio, event_id)"
        " VALUES ('spectate_summary', '2026-08-01T00:00:00Z', 'local', ?, 1)",
        (winning_trio,),
    )
    match_id = int(cur.lastrowid)
    if heroes:
        for trio, slugs in ((1, TRIO_A), (2, TRIO_B)):
            for slot, slug in enumerate(slugs, 1):
                con.execute(
                    "INSERT INTO match_hero(match_id, trio, slot, hero_slug, status)"
                    " VALUES (?, ?, ?, ?, 'identified')",
                    (match_id, trio, slot, slug),
                )
    con.commit()
    con.close()
    return match_id


def _state(db, match_id):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT canonical_state FROM match WHERE id=?", (match_id,)
        ).fetchone()[0]
    finally:
        con.close()


def test_a_complete_leftover_is_accepted(canonical_db, migrate):
    match_id = _leftover(canonical_db, winning_trio=1)
    migrate.apply(str(canonical_db), quiet=True)
    assert _state(canonical_db, match_id) == "canonical"


def test_a_leftover_with_no_heroes_is_unrepresentable(canonical_db, migrate):
    """The crash-between-commits case. It has a row and nothing else, and calling it
    canonical would put a match with no composition into the fit and the pool.
    """
    match_id = _leftover(canonical_db, heroes=False, winning_trio=1)
    migrate.apply(str(canonical_db), quiet=True)
    assert _state(canonical_db, match_id) == "unrepresentable"


def test_a_leftover_missing_one_hero_is_unrepresentable(canonical_db, migrate):
    match_id = _leftover(canonical_db, winning_trio=1)
    con = sqlite3.connect(canonical_db)
    con.execute(
        "DELETE FROM match_hero WHERE match_id=? AND trio=2 AND slot=3", (match_id,)
    )
    con.commit()
    con.close()
    migrate.apply(str(canonical_db), quiet=True)
    assert _state(canonical_db, match_id) == "unrepresentable"


def test_the_predicate_settles_either_way(canonical_db, migrate):
    """Whatever the verdict, nothing may be left NULL - that is what the migration
    predicate reads as "the reshape has not run", and it would re-run forever.
    """
    _leftover(canonical_db, heroes=False)
    _leftover(canonical_db, winning_trio=1)
    migrate.apply(str(canonical_db), quiet=True)
    con = sqlite3.connect(canonical_db)
    try:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM match WHERE canonical_state IS NULL"
            ).fetchone()[0]
            == 0
        )
    finally:
        con.close()
