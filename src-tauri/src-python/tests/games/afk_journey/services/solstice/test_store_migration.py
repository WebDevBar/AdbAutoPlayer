"""Schema migration for canonical identity.

The columns are added by `data/solstice_clash/migrate.py` (ADD_COLUMNS), which the
store loads dynamically - there is deliberately only ONE migration list, because two
copies drift and the one that drifts is the one nobody runs by hand.
"""

from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore


def test_migration_adds_the_identity_columns(tmp_path):
    store = MatchStore(tmp_path / "h.sqlite")
    cols = {r[1] for r in store._connect().execute("PRAGMA table_info(match)")}
    assert {
        "comps_key",
        "superseded_by",
        "captures_min_at",
        "captures_max_at",
    } <= cols


def test_comps_key_is_not_unique(tmp_path):
    """Local mirrored pairs must coexist until the server reconciles them."""
    store = MatchStore(tmp_path / "h.sqlite")
    con = store._connect()
    con.execute(
        "INSERT INTO match(source, captured_at, comps_key)"
        " VALUES ('spectate_summary','2026-07-31T08:00:00Z','sha256:x')"
    )
    con.execute(
        "INSERT INTO match(source, captured_at, comps_key)"
        " VALUES ('spectate_summary','2026-07-31T08:00:07Z','sha256:x')"
    )
    con.commit()  # must not raise


def test_comps_key_is_indexed(tmp_path):
    """The lookup is by comps_key, so it must not be a table scan."""
    store = MatchStore(tmp_path / "h.sqlite")
    con = store._connect()
    names = [r[1] for r in con.execute("PRAGMA index_list(match)")]
    cols_by_index = {
        name: {c[2] for c in con.execute(f"PRAGMA index_info({name})")}
        for name in names
    }
    assert any("comps_key" in cols for cols in cols_by_index.values()), cols_by_index


def test_migration_is_idempotent(tmp_path):
    """`_schema_ensured` is a CLASS-level cache, so a second MatchStore is a no-op.

    Without the discard this passes while proving nothing.
    """
    path = tmp_path / "h.sqlite"
    MatchStore(path)
    MatchStore._schema_ensured.discard(path)
    MatchStore(path)  # must not raise "duplicate column name"


def test_install_gains_a_supersession_cursor(tmp_path):
    store = MatchStore(tmp_path / "h.sqlite")
    cols = {r[1] for r in store._connect().execute("PRAGMA table_info(install)")}
    assert "supersession_cursor" in cols


def test_the_two_cursors_move_independently(tmp_path):
    store = MatchStore(tmp_path / "h.sqlite")
    store.set_pull_cursor(120)
    store.set_supersession_cursor(3)
    assert store.pull_cursor() == 120
    assert store.supersession_cursor() == 3
    store.set_pull_cursor(200)
    assert store.supersession_cursor() == 3, (
        "the match cursor must not drag the supersession cursor with it"
    )
