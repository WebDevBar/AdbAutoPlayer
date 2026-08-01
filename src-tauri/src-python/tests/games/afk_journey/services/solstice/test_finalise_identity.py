"""Canonical identity is finalised once the heroes are known.

`record_match()` cannot do this: the spectate flow creates the row before the heroes
exist, so `comps_key` is not computable there. A provisional row keeps `comps_key`
NULL, which the push gate excludes.
"""

# The test directories are NOT packages (no `__init__.py` under `tests/games/`), so a
# relative `from ._support import ...` raises "attempted relative import with no known
# parent package". pytest's prepend import mode puts this directory on `sys.path`, so
# the plain module name is the import that works. Every helper is underscore-prefixed
# and a wildcard import would silently skip all of them.
from _support import (  # noqa: F401 - `store` is a fixture, used by name
    _comps_key,
    _record,
    _record_without_heroes,
    _superseded_by,
    store,
)


def test_second_observation_is_superseded_not_inserted_twice(store):  # noqa: F811
    first = _record(store, at="2026-07-31T08:12:39Z")
    second = _record(store, at="2026-07-31T08:12:46Z", mirrored=True)
    store.finalise_identity(first)
    store.finalise_identity(second)
    assert _superseded_by(store, second) == first
    assert _superseded_by(store, first) is None


def test_a_bridging_capture_coalesces_two_local_occurrences(store):  # noqa: F811
    a = _record(store, at="2026-07-31T09:00:00Z")
    b = _record(store, at="2026-07-31T09:03:01Z")
    store.finalise_identity(a)
    store.finalise_identity(b)
    c = _record(store, at="2026-07-31T09:01:31Z")
    store.finalise_identity(c)
    assert _superseded_by(store, b) == a
    assert _superseded_by(store, c) == a


def test_a_superseded_row_is_still_pushable(store):  # noqa: F811
    """Withholding it would starve the server of the bridging evidence."""
    first = _record(store, at="2026-07-31T08:12:39Z")
    second = _record(store, at="2026-07-31T08:12:46Z")
    store.finalise_identity(first)
    store.finalise_identity(second)
    assert second in {m["local_id"] for m in store.pushable_matches()}


def test_a_provisional_row_without_heroes_is_never_pushable(store):  # noqa: F811
    match_id = _record_without_heroes(store)
    assert match_id not in {m["local_id"] for m in store.pushable_matches()}


def test_event_slug_falls_back_through_theme(store):  # noqa: F811
    match_id = _record(store, event_id=None, theme_id=4)
    store.finalise_identity(match_id)
    assert _comps_key(store, match_id) is not None


def test_no_set_natural_key_call_sites_remain():
    """Both the compete (614) and spectate (1339) paths must have migrated."""
    from pathlib import Path

    # Derive the path from the imported module, never from the CWD.
    from adb_auto_player.games.afk_journey.mixins import solstice_clash

    source = Path(solstice_clash.__file__).read_text()
    assert "set_natural_key(" not in source
    assert "natural_key(" not in source, (
        "the SC-41 backstop at 566-573 still computes an orientation-sensitive key"
    )
    assert source.count("finalise_identity(") >= 2
