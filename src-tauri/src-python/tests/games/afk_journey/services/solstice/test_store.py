"""Match store tests.

Match data is LOCALLY EARNED - a wiki refresh must leave it completely alone.
"""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from adb_auto_player.games.afk_journey.services.solstice.store import (
    HeroSlot,
    MatchRecord,
    MatchStore,
    OddsSample,
    PoolSlot,
)


@pytest.fixture
def tmp_db(tmp_path, db_path):
    target = tmp_path / "heroes.sqlite"
    shutil.copy(db_path, target)
    return target


def test_records_a_match_and_its_heroes(tmp_db):
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(
            source="compete",
            captured_at="2026-07-26T10:00:00",
            natural_key="t1",
            theme="Fierce Duel",
            blue_player="GameRetro",
            red_player="Dan",
        )
    )
    store.record_heroes(
        mid,
        [
            HeroSlot("blue", 1, "dionel", "spui_herohead_48", "identified", 0.97, 0.34),
            HeroSlot("red", 6, None, None, "unknown", 0.41, 0.02),
        ],
    )
    rows = store.heroes_for(mid)
    assert len(rows) == 2
    assert rows[0].hero_slug == "dionel"
    assert rows[1].status == "unknown" and rows[1].hero_slug is None


def test_natural_key_dedupes(tmp_db):
    """Mode B sees the same match on consecutive polls; it must not duplicate."""
    store = MatchStore(tmp_db)
    first = store.record_match(
        MatchRecord(
            source="spectate", captured_at="2026-07-26T10:00:00", natural_key="same"
        )
    )
    second = store.record_match(
        MatchRecord(
            source="spectate", captured_at="2026-07-26T10:05:00", natural_key="same"
        )
    )
    assert first == second
    assert store.match_by_natural_key("same") == first


def test_a_match_can_exist_without_a_natural_key(tmp_db):
    """Mid-draft observations have no stable key yet but must still record."""
    store = MatchStore(tmp_db)
    first = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-26T10:00:00")
    )
    second = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-26T10:00:01")
    )
    assert first != second, "unkeyed observations must not collapse into one row"
    store.set_natural_key(first, "later-key")
    assert store.match_by_natural_key("later-key") == first


def test_unknown_heroes_are_stored_not_dropped(tmp_db):
    """Dropping an unidentified slot would make a 3v3 look like a 2v3."""
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-26T10:00:00")
    )
    store.record_heroes(
        mid,
        [HeroSlot("blue", i, None, None, "unknown", 0.5, 0.0) for i in (1, 2, 3)],
    )
    assert len(store.heroes_for(mid)) == 3


def test_pool_is_recorded_including_banned_slots(tmp_db):
    """The pool must be persisted, not computed and discarded.

    Discarding it would be the most expensive omission to retrofit.
    """
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-26T10:00:00")
    )
    store.record_pool(
        mid,
        [
            PoolSlot(1, "indris", "spui_herohead_87", "identified", 0, 0.95, 0.34),
            PoolSlot(6, None, None, "banned", 1),
            PoolSlot(20, None, None, "banned", 1),
        ],
    )
    rows = store.pool_for(mid)
    assert len(rows) == 3
    assert {r.slot for r in rows if r.banned} == {6, 20}
    assert rows[0].hero_slug == "indris"


def test_pool_fallback_provenance_is_recorded(tmp_db):
    """A pool miss and a legitimate out-of-pool hero must stay distinguishable."""
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-26T10:00:00")
    )
    store.record_heroes(
        mid,
        [
            HeroSlot(
                "blue",
                1,
                "sonja",
                "x",
                "identified",
                0.97,
                0.4,
                cell_type="locked_pick",
                cell_name="locked_pick_1",
                candidate_scope="pool",
                pool_miss=0,
            ),
            HeroSlot(
                "blue",
                2,
                "zorya",
                "y",
                "identified",
                0.92,
                0.5,
                cell_type="locked_pick",
                cell_name="locked_pick_2",
                candidate_scope="full_library",
                pool_miss=1,
            ),
        ],
    )
    rows = store.heroes_for(mid)
    assert rows[0].candidate_scope == "pool" and rows[0].pool_miss == 0
    assert rows[1].candidate_scope == "full_library" and rows[1].pool_miss == 1


def test_odds_samples_accumulate(tmp_db):
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="spectate", captured_at="2026-07-26T10:00:00")
    )
    store.record_odds(
        mid, OddsSample("2026-07-26T10:00:01", 1000, 2000, 2.93, 1.45, 12)
    )
    store.record_odds(
        mid, OddsSample("2026-07-26T10:00:05", 1500, 2000, 2.30, 1.70, 15)
    )
    assert len(store.odds_for(mid)) == 2


def test_deleting_a_match_cascades(tmp_db):
    """Deleting a match removes its children.

    ON DELETE CASCADE needs PRAGMA foreign_keys per connection, not just in the schema.
    """
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-26T10:00:00")
    )
    store.record_heroes(mid, [HeroSlot("blue", 1, "sonja", "x", "identified")])
    store.record_pool(mid, [PoolSlot(1, "sonja", "x", "identified")])
    store.record_odds(mid, OddsSample("2026-07-26T10:00:01"))
    con = sqlite3.connect(tmp_db)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("DELETE FROM match WHERE id=?", (mid,))
    con.commit()
    con.close()
    assert store.heroes_for(mid) == []
    assert store.pool_for(mid) == []
    assert store.odds_for(mid) == []


@pytest.mark.network
def test_build_hero_db_does_not_touch_match_data(tmp_db):
    """A full wiki refresh must leave match data alone.

    This runs build_hero_db.py for real against the temp copy - it hits the Fandom API,
    so it is marked `network`. Running only migrate.py here would NOT test the claim,
    since migrate.py never writes rows.
    """
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(
            source="compete", captured_at="2026-07-26T10:00:00", natural_key="keep"
        )
    )
    store.record_heroes(
        mid, [HeroSlot("blue", 1, "sonja", "x", "identified", 0.9, 0.3)]
    )
    store.record_pool(mid, [PoolSlot(1, "sonja", "x", "identified")])
    store.record_odds(mid, OddsSample("2026-07-26T10:00:01", 100, 200, 2.9, 1.4, 5))

    builder = (
        Path(__file__).resolve().parents[7]
        / "data"
        / "solstice_clash"
        / "build_hero_db.py"
    )
    # Pass the temp database EXPLICITLY. build_hero_db.py resolves its default path
    # relative to the script, so a cwd-based redirect would rewrite the SHIPPED database
    # and leave these assertions checking an untouched copy - passing trivially.
    subprocess.run([sys.executable, str(builder), str(tmp_db)], check=True)

    assert store.match_by_natural_key("keep") == mid
    assert len(store.heroes_for(mid)) == 1
    assert len(store.pool_for(mid)) == 1
    assert len(store.odds_for(mid)) == 1


def test_rejects_invalid_enum_values(tmp_db):
    """The schema documents these in comments only, so the store must enforce them."""
    store = MatchStore(tmp_db)
    with pytest.raises(ValueError, match="invalid source"):
        store.record_match(MatchRecord(source="comptee", captured_at="x"))
    with pytest.raises(ValueError, match="invalid outcome"):
        store.record_match(
            MatchRecord(source="compete", captured_at="x", outcome="purple")
        )
    mid = store.record_match(MatchRecord(source="compete", captured_at="x"))
    with pytest.raises(ValueError, match="invalid side"):
        store.record_heroes(mid, [HeroSlot("blu", 1, "sonja", "x", "identified")])
    with pytest.raises(ValueError, match="invalid status"):
        store.record_heroes(mid, [HeroSlot("blue", 1, "sonja", "x", "maybe")])


def test_rejects_status_that_disagrees_with_the_data(tmp_db):
    """status='unknown' with a hero_slug set is contradictory and must not persist."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(source="compete", captured_at="x"))
    with pytest.raises(ValueError, match="disagrees"):
        store.record_heroes(mid, [HeroSlot("blue", 1, "sonja", "x", "unknown")])
    with pytest.raises(ValueError, match="disagrees"):
        store.record_heroes(mid, [HeroSlot("blue", 2, None, None, "identified")])
    with pytest.raises(ValueError, match="disagrees"):
        store.record_pool(mid, [PoolSlot(1, None, None, "banned", 0)])


def test_rejects_out_of_range_pool_slots(tmp_db):
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(source="compete", captured_at="x"))
    with pytest.raises(ValueError, match="out of range"):
        store.record_pool(mid, [PoolSlot(21, "sonja", "x", "identified")])
    with pytest.raises(ValueError, match="out of range"):
        store.record_pool(mid, [PoolSlot(0, "sonja", "x", "identified")])


def test_pool_is_complete_detects_a_partial_pool(tmp_db):
    """A partial pool under-constrains identification and must be detectable."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(source="compete", captured_at="x"))
    store.record_pool(
        mid, [PoolSlot(i, "sonja", "x", "identified") for i in range(1, 5)]
    )
    assert store.pool_is_complete(mid) is False
    store.record_pool(
        mid, [PoolSlot(i, "sonja", "x", "identified") for i in range(5, 21)]
    )
    assert store.pool_is_complete(mid) is True
