"""Schema v3: the confirmation gate must be enforced by the DATABASE, not by callers.

Every test here is a bypass attempt. If any of them succeeds, a learned transform could
be based on evidence that does not confirm it, which silently corrupts identification.
"""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[7]
MIGRATE = REPO / "data" / "solstice_clash" / "migrate.py"
SHIPPED_DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A migrated copy of the shipped database. Never migrate the shipped file itself."""
    target = tmp_path / "heroes.sqlite"
    shutil.copy(SHIPPED_DB, target)
    subprocess.run(
        [sys.executable, str(MIGRATE), str(target)], check=True, capture_output=True
    )
    return target


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def screen_id(con: sqlite3.Connection, slug: str) -> int:
    return int(con.execute("SELECT id FROM screen WHERE slug=?", (slug,)).fetchone()[0])


def test_screens_are_seeded(db: Path) -> None:
    con = connect(db)
    slugs = {r[0] for r in con.execute("SELECT slug FROM screen")}
    assert {"solstice_summary", "spectate_draft_picks", "spectate_prematch"} <= slugs


def test_match_hero_has_stat_columns(db: Path) -> None:
    con = connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(match_hero)")}
    assert {
        "stat_sword",
        "stat_heart",
        "stat_shield",
        "power",
        "identified_by",
    } <= cols


def test_migration_is_idempotent(db: Path) -> None:
    """schema.sql is executed on every run, so every CREATE needs IF NOT EXISTS."""
    before = connect(db).execute("SELECT COUNT(*) FROM screen").fetchone()[0]
    subprocess.run(
        [sys.executable, str(MIGRATE), str(db)], check=True, capture_output=True
    )
    after = connect(db).execute("SELECT COUNT(*) FROM screen").fetchone()[0]
    assert before == after


def _audit(con: sqlite3.Connection, **kw) -> int:
    row = {
        "match_id": None,
        "screen_id": screen_id(con, "solstice_summary"),
        "side": "left",
        "slot": 1,
        "image_slug": "atalanta",
        "image_art_ref": "Atalanta",
        "image_score": 0.87,
        "image_margin": 0.36,
        "ocr_slug": "atalanta",
        "agreed": 1,
        "frame_path": None,
        "created_at": "2026-07-26T00:00:00",
    }
    row.update(kw)
    cols = ",".join(row)
    marks = ",".join("?" * len(row))
    cur = con.execute(
        f"INSERT INTO identification_audit({cols}) VALUES({marks})", tuple(row.values())
    )
    return int(cur.lastrowid)


def _transform(con: sqlite3.Connection, audit_id, hero="atalanta", scr=None, cb="longpress_ocr"):
    con.execute(
        "INSERT INTO hero_screen_transform"
        "(screen_id,hero_slug,art_ref,scale,score,margin,confirmed_by,audit_id,verified_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            scr if scr is not None else screen_id(con, "solstice_summary"),
            hero, "Atalanta", 0.55, 0.87, 0.36, cb, audit_id, "2026-07-26T00:00:00",
        ),
    )


def test_agreed_cannot_contradict_the_slugs(db: Path) -> None:
    """agreed=1 must MEAN the two channels matched, or it launders a contradiction."""
    con = connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        _audit(con, image_slug="igor", ocr_slug="thoran", agreed=1)


def test_transform_requires_confirming_evidence(db: Path) -> None:
    con = connect(db)
    good = _audit(con)
    _transform(con, good)  # must succeed

    disagreed = _audit(con, image_slug="igor", ocr_slug=None, agreed=0)
    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, disagreed, hero="igor")

    other_screen = _audit(con, screen_id=screen_id(con, "spectate_prematch"),
                          image_slug="pippa", ocr_slug="pippa")
    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, other_screen, hero="pippa")  # audit is for a different screen

    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, good, hero="thoran")  # audit names a different hero

    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, good, hero="lyca", cb="self")  # self-confirmation banned

    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, None, hero="lyca")  # no evidence at all


def test_transform_update_also_requires_evidence(db: Path) -> None:
    """Re-tuning is an UPDATE; a BEFORE INSERT trigger alone would leak here."""
    con = connect(db)
    good = _audit(con)
    _transform(con, good)
    bad = _audit(con, image_slug="igor", ocr_slug=None, agreed=0)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE hero_screen_transform SET audit_id=?", (bad,))

    con.execute("UPDATE hero_screen_transform SET scale=0.62")  # legitimate re-tune
    assert con.execute("SELECT scale FROM hero_screen_transform").fetchone()[0] == 0.62


def test_deleting_a_match_does_not_break_transform_evidence(db: Path) -> None:
    """CASCADE here would abort the delete once a transform references the audit row."""
    con = connect(db)
    con.execute(
        "INSERT INTO match(source,captured_at) VALUES('spectate_summary','2026-07-26')"
    )
    # Scope to the row THIS test just made. The shipped database now carries real matches
    # and audit rows from live collection, so an unqualified SELECT picks up one of those.
    match_id = int(con.execute("SELECT MAX(id) FROM match").fetchone()[0])
    good = _audit(con, match_id=match_id)
    _transform(con, good)

    con.execute("DELETE FROM match WHERE id=?", (match_id,))

    assert (
        con.execute(
            "SELECT match_id FROM identification_audit WHERE id=?", (good,)
        ).fetchone()[0]
        is None
    )
    assert (
        con.execute(
            "SELECT COUNT(*) FROM hero_screen_transform WHERE audit_id=?", (good,)
        ).fetchone()[0]
        == 1
    )
