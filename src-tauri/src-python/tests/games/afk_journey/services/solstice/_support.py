"""Shared fixtures and row builders for the identity tests.

Imported by `test_finalise_identity.py` and `test_adopt_and_tombstones.py`. Every
helper is underscore-prefixed, so `from _support import *` would silently skip all
of them - import them BY NAME.
"""

import pytest
from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore

_LEFT = ("aliceth", "alna", "alsa")
_RIGHT = ("antandra", "arden", "atalanta")


@pytest.fixture
def store(tmp_path):
    """A fresh, migrated database with its foreign keys satisfied.

    `store.py` enables `PRAGMA foreign_keys = ON` on every connection, and
    `match.event_id`, `match.theme_id` and `match_hero.hero_slug` are all real
    foreign keys. Without these reference rows the very first `_record()` raises
    `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
    """
    store = MatchStore(tmp_path / "heroes.sqlite")
    with store._connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO event(id, slug, name, game)"
            " VALUES (1, 'solstice-clash', 'Solstice Clash', 'afk-journey')"
        )
        con.execute(
            "INSERT OR IGNORE INTO theme(id, event_id, slug, name, is_default)"
            " VALUES (4, 1, 'flourishing-wilds', 'Flourishing Wilds', 0)"
        )
        # `hero.name` is NOT NULL - a slug-only insert fails.
        for slug in _LEFT + _RIGHT:
            con.execute(
                "INSERT OR IGNORE INTO hero(slug, name) VALUES (?, ?)",
                (slug, slug.title()),
            )
    return store


def _record(
    store,
    *,
    at="2026-07-31T08:12:39Z",
    mirrored=False,
    event_id=1,
    theme_id=4,
    origin="local",
):
    """Insert one complete match with six identified heroes.

    Returns:
        The new local match id.
    """
    left, right = (_RIGHT, _LEFT) if mirrored else (_LEFT, _RIGHT)
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin, outcome,"
            " event_id, theme_id)"
            " VALUES ('spectate_summary', ?, ?, 'left', ?, ?)",
            (at, origin, event_id, theme_id),
        )
        match_id = int(cur.lastrowid)
        for side, slugs in (("left", left), ("right", right)):
            for slot, slug in enumerate(slugs, start=1):
                con.execute(
                    "INSERT INTO match_hero(match_id, side, slot, hero_slug, status)"
                    " VALUES (?, ?, ?, ?, 'identified')",
                    (match_id, side, slot, slug),
                )
    return match_id


def _record_without_heroes(store, at="2026-07-31T08:12:39Z"):
    """A provisional row: created before its heroes were read."""
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin)"
            " VALUES ('spectate_summary', ?, 'local')",
            (at,),
        )
        return int(cur.lastrowid)


def _two_local_rows_sharing_a_comps_key(store):
    """Two local observations of one match, mirrored, seven seconds apart."""
    a = _record(store, at="2026-07-31T08:12:39Z")
    b = _record(store, at="2026-07-31T08:12:46Z", mirrored=True)
    return a, b


def _duplicate_pair_one_superseded(store):
    a, b = _two_local_rows_sharing_a_comps_key(store)
    store.finalise_identity(a)
    store.finalise_identity(b)
    return a, b


def _local_row(store):
    return _record(store, origin="local")


def _synced_row(store, natural_key):
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin, natural_key)"
            " VALUES ('spectate_summary', '2026-07-31T08:00:00Z', 'synced', ?)",
            (natural_key,),
        )
        return int(cur.lastrowid)


def _pushed_local_row(store):
    match_id = _record(store)
    store.finalise_identity(match_id)
    store.adopt_canonical(match_id, "sha256:k:0", None, None)
    return match_id


def _scalar(store, sql, *params):
    with store._connect() as con:
        row = con.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _superseded_by(store, match_id):
    return _scalar(store, "SELECT superseded_by FROM match WHERE id=?", match_id)


def _comps_key(store, match_id):
    return _scalar(store, "SELECT comps_key FROM match WHERE id=?", match_id)


def _natural_key(store, match_id):
    return _scalar(store, "SELECT natural_key FROM match WHERE id=?", match_id)


def _exists(store, match_id):
    return _scalar(store, "SELECT 1 FROM match WHERE id=?", match_id) is not None


def _hero_count(store, match_id):
    return _scalar(store, "SELECT COUNT(*) FROM match_hero WHERE match_id=?", match_id)


def _row_for_key(store, natural_key):
    """The local row id holding this key, or None once it has been retired."""
    return _scalar(store, "SELECT id FROM match WHERE natural_key=?", natural_key)


def _insert_synced_row(store, natural_key):
    """A pulled row, as `upsert_synced` would have left it."""
    return _synced_row(store, natural_key)
