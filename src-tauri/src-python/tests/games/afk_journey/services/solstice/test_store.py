"""Match store tests.

Match data is LOCALLY EARNED - a wiki refresh must leave it completely alone.
"""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from adb_auto_player.games.afk_journey.services.solstice.matchkey import comps_key
from adb_auto_player.games.afk_journey.services.solstice.store import (
    EVENT_SLUG,
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
            left_player="GameRetro",
            right_player="Dan",
        )
    )
    store.record_heroes(
        mid,
        [
            HeroSlot("left", 1, "dionel", "spui_herohead_48", "identified", 0.97, 0.34),
            HeroSlot("right", 6, None, None, "unknown", 0.41, 0.02),
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
        [HeroSlot("left", i, None, None, "unknown", 0.5, 0.0) for i in (1, 2, 3)],
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
                "left",
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
                "left",
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
    store.record_heroes(mid, [HeroSlot("left", 1, "sonja", "x", "identified")])
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
        mid, [HeroSlot("left", 1, "sonja", "x", "identified", 0.9, 0.3)]
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
        store.record_heroes(mid, [HeroSlot("left", 1, "sonja", "x", "maybe")])


def test_rejects_status_that_disagrees_with_the_data(tmp_db):
    """status='unknown' with a hero_slug set is contradictory and must not persist."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(source="compete", captured_at="x"))
    with pytest.raises(ValueError, match="disagrees"):
        store.record_heroes(mid, [HeroSlot("left", 1, "sonja", "x", "unknown")])
    with pytest.raises(ValueError, match="disagrees"):
        store.record_heroes(mid, [HeroSlot("left", 2, None, None, "identified")])
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


def test_hero_stats_round_trip(tmp_db):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        HeroSlot,
        MatchRecord,
        MatchStore,
    )

    store = MatchStore(tmp_db)
    match_id = store.record_match(
        MatchRecord(source="spectate_summary", captured_at="2026-07-26")
    )
    store.record_heroes(
        match_id,
        [
            HeroSlot(
                side="left",
                slot=1,
                hero_slug="atalanta",
                art_ref="Atalanta",
                status="identified",
                score=0.87,
                margin=0.36,
                stat_sword=699_000,
                stat_heart=0,
                stat_shield=2_924_000,
                power=490_000,
                identified_by="longpress_ocr",
            ),
        ],
    )
    got = store.heroes_for(match_id)[0]
    assert got.stat_sword == 699_000
    assert got.stat_shield == 2_924_000
    assert got.identified_by == "longpress_ocr"


def test_record_audit_computes_agreement(tmp_db):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow,
        MatchStore,
    )

    store = MatchStore(tmp_db)
    agreed_before, total_before = store.audit_agreement_rate("solstice_summary")
    same = store.record_audit(
        AuditRow(
            screen_slug="solstice_summary",
            side="left",
            slot=1,
            image_slug="atalanta",
            image_art_ref="Atalanta",
            image_score=0.87,
            image_margin=0.36,
            ocr_slug="atalanta",
            frame_path=None,
        )
    )
    differ = store.record_audit(
        AuditRow(
            screen_slug="solstice_summary",
            side="right",
            slot=1,
            image_slug="igor",
            image_art_ref="Igor",
            image_score=0.72,
            image_margin=0.11,
            ocr_slug="thoran",
            frame_path="/x.png",
        )
    )
    assert same != differ
    # Measure the DELTA, not absolutes. tmp_db is a copy of the SHIPPED database, which
    # now carries real audit rows from live collection, so asserting on totals makes the
    # test fail as soon as anyone records a match.
    agreed, total = store.audit_agreement_rate("solstice_summary")
    assert (agreed - agreed_before, total - total_before) == (1, 2)


def test_learn_transform_requires_agreement(tmp_db):
    """A disagreeing audit row must not be usable as confirmation."""
    import pytest
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow,
        MatchStore,
    )

    store = MatchStore(tmp_db)
    bad = store.record_audit(
        AuditRow(
            screen_slug="solstice_summary",
            side="left",
            slot=1,
            image_slug="igor",
            image_art_ref="Igor",
            image_score=0.72,
            image_margin=0.11,
            ocr_slug="thoran",
            frame_path=None,
        )
    )
    with pytest.raises(ValueError):
        store.learn_transform(bad, "solstice_summary", "igor", "Igor", 0.55, 0.72, 0.11)


def test_learn_transform_roundtrip_and_retune(tmp_db):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow,
        MatchStore,
    )

    store = MatchStore(tmp_db)
    good = store.record_audit(
        AuditRow(
            screen_slug="solstice_summary",
            side="left",
            slot=1,
            image_slug="atalanta",
            image_art_ref="Atalanta",
            image_score=0.87,
            image_margin=0.36,
            ocr_slug="atalanta",
            frame_path=None,
        )
    )
    store.learn_transform(
        good,
        "solstice_summary",
        "atalanta",
        "Atalanta",
        0.55,
        0.87,
        0.36,
        crop=(22, 18, 26),
    )
    got = store.transform_for("solstice_summary", "atalanta")
    assert got["scale"] == 0.55 and got["crop_half_w"] == 22

    store.learn_transform(
        good,
        "solstice_summary",
        "atalanta",
        "Atalanta",
        0.58,
        0.89,
        0.40,
        crop=(24, 16, 28),
    )
    got = store.transform_for("solstice_summary", "atalanta")
    assert got["scale"] == 0.58 and got["margin"] == 0.40


def test_unknown_source_is_rejected(tmp_db):
    import pytest
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        MatchRecord,
        MatchStore,
    )

    store = MatchStore(tmp_db)
    store.record_match(MatchRecord(source="spectate_summary", captured_at="2026-07-26"))
    with pytest.raises(ValueError):
        store.record_match(MatchRecord(source="history", captured_at="2026-07-26"))


# --- Mode B source ---------------------------------------------------------


def test_compete_summary_is_an_allowed_source(tmp_db):
    """Mode B records the details screen of a match the user played."""
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete_summary", captured_at="2026-07-25T12:00:00+00:00")
    )
    assert mid > 0


def test_an_unknown_source_is_still_rejected(tmp_db):
    """The set is enforced, not decorative - a typo must not persist silently."""
    with pytest.raises(ValueError):
        MatchStore(tmp_db).record_match(
            MatchRecord(source="comptee", captured_at="2026-07-25T12:00:00+00:00")
        )


def test_instance_uuid_is_created_on_a_fresh_install(tmp_db):
    """The bug that silently cost a contributor every match they collected.

    Seeding deletes the bundled `install` row, and nothing recreated it - a
    shipped build never runs `migrate.py`. The client then sent an empty
    `X-Instance-Id` and the server answered 400 to every sync request.
    """
    with sqlite3.connect(tmp_db) as con:
        con.execute("DELETE FROM install")

    store = MatchStore(tmp_db)
    first = store.instance_uuid()
    assert first, "a fresh install must mint an identity, not return nothing"
    assert store.instance_uuid() == first, "the identity must be stable"

    with sqlite3.connect(tmp_db) as con:
        rows = con.execute("SELECT COUNT(*) FROM install").fetchone()[0]
    assert rows == 1, "exactly one install row, whatever the call count"


def test_instance_uuid_keeps_an_existing_identity(tmp_db):
    """Regenerating it would orphan everything this install already pushed."""
    with sqlite3.connect(tmp_db) as con:
        con.execute("DELETE FROM install")
        con.execute(
            "INSERT INTO install(id,instance_uuid,created_at) VALUES(1,'mine','x')"
        )

    assert MatchStore(tmp_db).instance_uuid() == "mine"


def test_adopting_a_pooled_window_never_overwrites_one_already_recorded(tmp_path):
    """This install may have observed a rotation the pool has not been told about, and
    overwriting a recorded boundary would silently re-file matches already attributed."""
    import sqlite3

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore

    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE event(id INTEGER PRIMARY KEY, slug TEXT);"
        "CREATE TABLE theme(id INTEGER PRIMARY KEY, event_id INT, slug TEXT,"
        " name TEXT, starts_at TEXT, ends_at TEXT, is_default INT DEFAULT 0,"
        " UNIQUE(event_id, slug));"
        "INSERT INTO event(id,slug) VALUES(1,'solstice-clash');"
        "INSERT INTO theme(event_id,slug,name,starts_at,ends_at)"
        " VALUES(1,'a','A','2026-01-01T00:00:00Z',NULL);"
    )
    con.commit()
    con.close()

    store = MatchStore(db)
    filled = store.adopt_theme_windows([
        {"event_slug": "solstice-clash", "slug": "a", "name": "A",
         "starts_at": "2026-06-06T00:00:00+00:00", "ends_at": "2026-02-02T00:00:00+00:00"},
    ])
    con = sqlite3.connect(db)
    starts, ends = con.execute(
        "SELECT starts_at, ends_at FROM theme WHERE slug='a'"
    ).fetchone()
    con.close()
    assert starts == "2026-01-01T00:00:00Z", "a recorded boundary is never overwritten"
    # The offset form is normalised to Z: the window comparison is a STRING comparison,
    # so mixing the two would compare wrongly at exactly the boundary it exists to mark.
    assert ends == "2026-02-02T00:00:00Z"
    assert filled == 1


def test_a_theme_the_pool_knows_and_we_do_not_is_inserted(tmp_path):
    """A theme arriving mid-event must not have to wait for a release."""
    import sqlite3

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore

    db = tmp_path / "t.sqlite"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE event(id INTEGER PRIMARY KEY, slug TEXT);"
        "CREATE TABLE theme(id INTEGER PRIMARY KEY, event_id INT, slug TEXT,"
        " name TEXT, starts_at TEXT, ends_at TEXT, is_default INT DEFAULT 0,"
        " UNIQUE(event_id, slug));"
        "INSERT INTO event(id,slug) VALUES(1,'solstice-clash');"
    )
    con.commit()
    con.close()

    MatchStore(db).adopt_theme_windows([
        {"event_slug": "solstice-clash", "slug": "brand-new", "name": "Brand New",
         "starts_at": "2026-09-09T00:00:00Z", "ends_at": None},
    ])
    con = sqlite3.connect(db)
    row = con.execute("SELECT slug, starts_at FROM theme WHERE slug='brand-new'").fetchone()
    con.close()
    assert row == ("brand-new", "2026-09-09T00:00:00Z")


# ---------------------------------------------------------------- derived views


def _record_3v3(store, key, left, right, winner, theme_id=None):
    """One complete match. Returns its id."""
    mid = store.record_match(
        MatchRecord(
            source="compete",
            captured_at="2026-07-29T10:00:00",
            natural_key=key,
            theme_id=theme_id,
            outcome=winner,
        )
    )
    store.record_heroes(
        mid,
        [HeroSlot("left", i + 1, h, None, "identified") for i, h in enumerate(left)]
        + [
            HeroSlot("right", i + 4, h, None, "identified")
            for i, h in enumerate(right)
        ],
    )
    return mid


def test_hero_matchup_view_exists_on_a_database_that_predates_it(tmp_db):
    """The client must create it: a shipped build never runs migrate.py.

    `solstice_db_path` hands back an existing user database untouched, so a view defined
    only in schema.sql would reach the machine that ran the migration and nowhere else.
    """
    with sqlite3.connect(tmp_db) as con:
        con.execute("DROP VIEW IF EXISTS hero_matchup")
    MatchStore(tmp_db)  # constructing the store is what ensures the view
    with sqlite3.connect(tmp_db) as con:
        views = con.execute(
            "SELECT COUNT(*) FROM sqlite_master"
            " WHERE type='view' AND name='hero_matchup'"
        ).fetchone()[0]
    assert views == 1


def test_hero_matchup_canonical_ordering_and_tally(tmp_db):
    """A vs B and B vs A are ONE row, and the tally is oriented onto hero_a."""
    store = MatchStore(tmp_db)
    # 'alsa' < 'bryon' alphabetically, so alsa is always hero_a whichever side it took.
    left = ["alsa", "cecia", "dionel"]
    right = ["bryon", "eironn", "antandra"]
    _record_3v3(store, "m1", left, right, "left")
    # alsa on the RIGHT this time, and its side loses.
    _record_3v3(
        store,
        "m2",
        ["bryon", "cecia", "dionel"],
        ["alsa", "eironn", "antandra"],
        "left",
    )
    _record_3v3(store, "m3", left, right, "left")

    with sqlite3.connect(tmp_db) as con:
        row = con.execute(
            "SELECT hero_a, hero_b, a_wins, b_wins, tally, observations"
            "  FROM hero_matchup WHERE hero_a='alsa' AND hero_b='bryon'"
        ).fetchone()
    # m1 and m3: alsa's side won. m2: alsa was on the right and lost.
    assert row == ("alsa", "bryon", 2, 1, 1, 3)


def test_hero_matchup_ignores_incomplete_and_mirror_pairs(tmp_db):
    """The corpus rule must match the odds model's, or the two disagree forever.

    A 2v3 comp would teach that two heroes beat three; a mirror pick is not a matchup.
    """
    store = MatchStore(tmp_db)
    # A 2v3: only two identified heroes on the left.
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-29T10:00:00",
                    natural_key="partial", outcome="left")
    )
    store.record_heroes(
        mid,
        [HeroSlot("left", 1, "alsa", None, "identified"),
         HeroSlot("left", 2, "cecia", None, "identified"),
         HeroSlot("left", 3, None, None, "unknown"),
         HeroSlot("right", 4, "bryon", None, "identified"),
         HeroSlot("right", 5, "eironn", None, "identified"),
         HeroSlot("right", 6, "antandra", None, "identified")],
    )
    # A complete match where 'cecia' is picked by BOTH sides.
    _record_3v3(store, "mirror", ["cecia", "alsa", "dionel"],
                ["cecia", "eironn", "antandra"], "left")

    with sqlite3.connect(tmp_db) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM hero_matchup WHERE hero_a='alsa' AND hero_b='bryon'"
        ).fetchone()[0] == 0, "a 2v3 must not contribute"
        assert con.execute(
            "SELECT COUNT(*) FROM hero_matchup WHERE hero_a='cecia' AND hero_b='cecia'"
        ).fetchone()[0] == 0, "a mirror pick is not a matchup"


def test_hero_matchup_counts_draws_separately(tmp_db):
    """Draws are excluded from wins/losses but not thrown away.

    The odds model drops them; keeping the count means a later formula that wants them
    does not need a schema change to get them.
    """
    store = MatchStore(tmp_db)
    left = ["alsa", "cecia", "dionel"]
    right = ["bryon", "eironn", "antandra"]
    _record_3v3(store, "d1", left, right, "draw")
    _record_3v3(store, "d2", left, right, "left")

    with sqlite3.connect(tmp_db) as con:
        row = con.execute(
            "SELECT a_wins, b_wins, tally, draws, observations"
            "  FROM hero_matchup WHERE hero_a='alsa' AND hero_b='bryon'"
        ).fetchone()
    assert row == (1, 0, 1, 1, 1)


# ------------------------------------------------------------- schema migration


def _strip_predicted_columns(db):
    """Turn the fixture into a pre-2026-07 database: no predicted_* columns.

    ALTER TABLE DROP COLUMN, not a hand-rolled rebuild. `CREATE TABLE AS SELECT` copies
    rows but not constraints, and the copy silently lost both `INTEGER PRIMARY KEY` and
    `UNIQUE(natural_key)` - which then failed in ways that looked like bugs in the code
    under test rather than in the fixture.
    """
    con = sqlite3.connect(db)
    try:
        # The view reads `match`; dropping columns underneath it breaks every later
        # statement that revalidates the schema. A database this old had no view anyway.
        con.execute("DROP VIEW IF EXISTS hero_matchup")
        for column in ("predicted_left", "predicted_source", "predicted_locked",
                       "predicted_at"):
            con.execute(f"ALTER TABLE match DROP COLUMN {column}")
        con.commit()
    finally:
        # close(), not just `with` - a context manager COMMITS a sqlite connection but
        # does not close it, and the lingering lock blocks the migration under test.
        con.close()


def test_opening_an_old_database_adds_the_missing_columns(tmp_db):
    """The failure a real contributor hit: every match lost to "no such column".

    A shipped build never runs migrate.py and `solstice_db_path` returns an existing
    database untouched, so before this the schema froze at whatever seeded it - and
    updating the app did not help, because nothing ever added the column.
    """
    _strip_predicted_columns(tmp_db)
    with sqlite3.connect(tmp_db) as con:
        present = {r[1] for r in con.execute("PRAGMA table_info(match)")}
    assert not [c for c in present if c.startswith("predicted_")]

    MatchStore(tmp_db)  # constructing the store is what repairs it

    with sqlite3.connect(tmp_db) as con:
        after = {r[1] for r in con.execute("PRAGMA table_info(match)")}
    assert {"predicted_left", "predicted_source", "predicted_locked",
            "predicted_at"} <= after


def test_the_repaired_database_accepts_the_write_that_used_to_fail(tmp_db):
    _strip_predicted_columns(tmp_db)
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-29T10:00:00",
                    natural_key="after-migration", outcome="left")
    )
    assert mid
    with sqlite3.connect(tmp_db) as con:
        con.execute("UPDATE match SET predicted_left=0.62 WHERE id=?", (mid,))
        assert con.execute(
            "SELECT predicted_left FROM match WHERE id=?", (mid,)
        ).fetchone()[0] == 0.62


def test_migration_preserves_the_matches_already_collected(tmp_db):
    """Repair must not cost the user their data.

    That is the whole point of fixing the database in place rather than telling them to
    delete it and start over.
    """
    store = MatchStore(tmp_db)
    mid = store.record_match(
        MatchRecord(source="compete", captured_at="2026-07-29T09:00:00",
                    natural_key="collected-before-the-upgrade", outcome="right")
    )
    store.record_heroes(mid, [HeroSlot("left", 1, "alsa", None, "identified")])
    _strip_predicted_columns(tmp_db)
    MatchStore._schema_ensured.discard(tmp_db)  # force a real re-run in-process

    MatchStore(tmp_db)

    with sqlite3.connect(tmp_db) as con:
        row = con.execute(
            "SELECT outcome FROM match WHERE natural_key='collected-before-the-upgrade'"
        ).fetchone()
        heroes = con.execute(
            "SELECT COUNT(*) FROM match_hero WHERE match_id=?", (mid,)
        ).fetchone()[0]
    assert row == ("right",), "the match survived the migration"
    assert heroes == 1, "its heroes survived too"

def test_a_pulled_row_gets_a_comps_key(tmp_db):
    """The SC-41 backstop asks by comps_key, so a synced row without one is invisible.

    Found live on 2026-08-01: the first real pull brought 50 matches and every one of
    them had a NULL comps_key, so a match another contributor had already pushed would
    have been recorded a second time locally. The backstop used to ask by natural_key,
    which synced rows DO have - moving it to comps_key regressed this.
    """
    store = MatchStore(tmp_db)
    MatchStore._schema_ensured.discard(tmp_db)
    left = ["aliceth", "alna", "alsa"]
    right = ["antandra", "arden", "atalanta"]
    match_id = store.upsert_synced({
        "natural_key": "sha256:deadbeef:0",
        "source": "spectate_summary",
        "captured_at": "2026-08-01T04:51:37Z",
        "outcome": "left",
        "theme_slug": None,
        "heroes": (
            [{"side": "left", "slot": i, "hero_slug": h} for i, h in enumerate(left)]
            + [{"side": "right", "slot": i, "hero_slug": h} for i, h in enumerate(right)]
        ),
    })
    assert match_id is not None
    with store._connect() as con:
        stored = con.execute(
            "SELECT comps_key FROM match WHERE id=?", (match_id,)
        ).fetchone()[0]
    assert stored == comps_key(EVENT_SLUG, left, right)
