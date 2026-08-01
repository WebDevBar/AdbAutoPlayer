"""Origin-aware adoption, tombstone retirement, and superseded-free analysis.

Adoption may drop a clashing SYNCED copy - that is someone else's row and we hold
our own observation of it - but never a clashing LOCAL one, which carries this
install's hero evidence. Retirement has to clear every field that gates a push, or
the row is permanently unpushable. And the fit and the scoring must see one
observation per match, not one per capture.
"""

# The test directories are NOT packages (no `__init__.py` under `tests/games/`), so a
# relative `from ._support import ...` raises "attempted relative import with no known
# parent package". pytest's prepend import mode puts this directory on `sys.path`, so
# the plain module name is the import that works.
from adb_auto_player.games.afk_journey.services.solstice.sync import (
    SyncClient,
    SyncConfig,
)

from _support import (  # noqa: F401 - `store` is a fixture, used by name
    _duplicate_pair_one_superseded,
    _exists,
    _hero_count,
    _insert_synced_row,
    _local_row,
    _natural_key,
    _pushed_local_row,
    _row_for_key,
    _superseded_by,
    _synced_row,
    _two_local_rows_sharing_a_comps_key,
    store,
)


def test_adoption_never_deletes_a_local_row(store):  # noqa: F811
    a, b = _two_local_rows_sharing_a_comps_key(store)
    store.adopt_canonical(a, "sha256:k:0", "flourishing-wilds", "window")
    store.adopt_canonical(b, "sha256:k:0", "flourishing-wilds", "window")
    assert _exists(store, a) and _exists(store, b)
    assert _superseded_by(store, b) == a
    assert _hero_count(store, b) == 6


def test_adoption_still_deletes_a_clashing_synced_row(store):  # noqa: F811
    local = _local_row(store)
    synced = _synced_row(store, natural_key="sha256:k:0")
    store.adopt_canonical(local, "sha256:k:0", None, None)
    assert not _exists(store, synced)
    assert _natural_key(store, local) == "sha256:k:0"


def test_retiring_a_local_row_makes_it_pushable_again_exactly_once(store):  # noqa: F811
    row = _pushed_local_row(store)
    store.retire_for_tombstone(row)
    assert row in {m["local_id"] for m in store.pushable_matches()}
    store.adopt_canonical(row, "sha256:k:0", None, None)
    assert row not in {m["local_id"] for m in store.pushable_matches()}


def test_retiring_a_locally_superseded_row_is_a_no_op(store):  # noqa: F811
    """The push/retire cycle must not restart on a row we already folded away.

    `pushable_matches` deliberately carries no supersession term - a folded row is
    still sent, because it is the bridging evidence the server dedupes on. What must
    not happen is retirement reopening it: it is already marked sent, and clearing
    that would have it re-pushed and re-retired on every single pull.
    """
    a, b = _duplicate_pair_one_superseded(store)
    store.adopt_canonical(a, "sha256:k:0", None, None)
    store.adopt_canonical(b, "sha256:k:0", None, None)
    assert b not in {m["local_id"] for m in store.pushable_matches()}

    assert store.retire_for_tombstone(b) is False
    assert _superseded_by(store, b) == a
    assert b not in {m["local_id"] for m in store.pushable_matches()}


def test_the_fit_and_the_scoring_exclude_superseded_rows(store):  # noqa: F811
    """One match's worth of evidence, not two.

    `matches_for_fit()` returns one row PER IDENTIFIED HERO, so a three-a-side match
    is six rows, not one - assert on distinct match ids instead. And
    `scored_predictions()` selects only rows with `predicted_left IS NOT NULL`, so
    the prediction has to be recorded explicitly or the count is zero either way.
    """
    a, b = _duplicate_pair_one_superseded(store)
    for match_id in (a, b):
        store.record_prediction(match_id, 0.72, "r+h", 6, "2026-07-31T08:12:40Z")

    fit_match_ids = {row[0] for row in store.matches_for_fit()}
    assert fit_match_ids == {a}, "the superseded row must not contribute hero evidence"

    assert len(store.scored_predictions()) == 1, (
        "a duplicate observation must not be scored twice"
    )


def test_the_supersession_cursor_advances_independently(store, monkeypatch):  # noqa: F811
    """A tombstone is consumed and its cursor advances without the match cursor.

    No fake HTTP server: `SyncClient._request` is the seam. Stubbing it keeps the
    test on the cursor logic, which is what is under test, and avoids standing up a
    transport harness that would itself need testing.
    """
    # SyncConfig is a frozen dataclass with FOUR required fields and no defaults.
    client = SyncClient(
        store,
        SyncConfig(
            base_url="http://test.invalid", api_key="k", enabled=True, timeout=1.0
        ),
        client_version="test",
    )
    seen: list[str] = []

    def fake_request(method, path, body=None):
        seen.append(path)
        return {
            "matches": [],
            "superseded": [
                {
                    "natural_key": "sha256:old",
                    "superseded_by_natural_key": "sha256:new:0",
                }
            ],
            "supersession_cursor": 7,
        }

    monkeypatch.setattr(client, "_request", fake_request)
    _insert_synced_row(store, natural_key="sha256:old")
    store.set_pull_cursor(500)

    client.pull()

    assert "supersession_since=0" in seen[0], "the second cursor must be sent"
    assert store.supersession_cursor() == 7
    assert store.pull_cursor() == 500, (
        "an empty match page must not move the match cursor"
    )
    assert _row_for_key(store, "sha256:old") is None


def test_adoption_out_of_order_does_not_create_a_cycle(store):  # noqa: F811
    """B already superseded by A locally, then B adopts the canonical key FIRST."""
    a, b = _two_local_rows_sharing_a_comps_key(store)
    store.finalise_identity(a)
    store.finalise_identity(b)  # sets B.superseded_by = A
    store.adopt_canonical(b, "sha256:k:0", None, None)
    store.adopt_canonical(a, "sha256:k:0", None, None)

    roots = [r for r in (a, b) if _superseded_by(store, r) is None]
    assert len(roots) == 1, "exactly one row must remain active"
    assert _natural_key(store, roots[0]) == "sha256:k:0", (
        "the sole active root must own the canonical key"
    )
    other = next(r for r in (a, b) if r != roots[0])
    assert _natural_key(store, other) is None
    # Distinct match ids, not row count: matches_for_fit returns one row per
    # identified hero, so one three-a-side match is six rows.
    assert {row[0] for row in store.matches_for_fit()} == {roots[0]}
