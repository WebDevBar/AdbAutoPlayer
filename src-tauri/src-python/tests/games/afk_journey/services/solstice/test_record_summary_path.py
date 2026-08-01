"""The complete recording path, exercised end to end.

Every one of these covers a defect that unit tests missed and a code review found:
the path crashed on `HeroSlot.side`, silently dropped every odds sample through a
`TypeError` the broad handler swallowed, and announced HIT/MISS against the summary
panel rather than against our own draft - which is match 1476's defect, live, in the
one place a person actually reads.

They drive `_record_summary` itself rather than its parts, because all three bugs
lived in the seams between parts that each passed on their own.
"""

import types

import numpy as np
import pytest

from adb_auto_player.games.afk_journey.services.solstice.store import (
    MatchStore,
)

TRIO_A = ("aliceth", "alna", "alsa")  # sorts first: canonically trio 1
TRIO_B = ("antandra", "arden", "atalanta")


def _hero(side, slot, slug):
    return types.SimpleNamespace(
        side=side,
        slot=slot,
        slug=slug,
        art_ref=slug,
        score=0.9,
        margin=0.3,
        stats=types.SimpleNamespace(sword=1, heart=2, shield=3),
    )


def _read(top, bottom, winner):
    return types.SimpleNamespace(
        heroes=[_hero("left", i, s) for i, s in enumerate(top, 1)]
        + [_hero("right", i, s) for i, s in enumerate(bottom, 1)],
        winner=winner,
        left_player="Us",
        right_player="Them",
    )


@pytest.fixture
def store(tmp_path):
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
        for slug in TRIO_A + TRIO_B:
            con.execute(
                "INSERT OR IGNORE INTO hero(slug, name) VALUES (?, ?)",
                (slug, slug.title()),
            )
    return store


@pytest.fixture
def bot(store, monkeypatch, tmp_path):
    """A real mixin instance with only the screen reads stubbed.

    A real instance because the carried state IS the thing under test, and a real
    store because the assertions read what was actually written.
    """
    from adb_auto_player.games.afk_journey.mixins import solstice_clash as mod

    # A subclass, so the read-only `settings` property can be overridden without
    # touching the class under test.
    class _Bot(mod.SolsticeClashMixin):
        settings = types.SimpleNamespace(wdb_modes=None)

    bot = _Bot.__new__(_Bot)
    # These are cached PROPERTIES, so the caches are what a test may set.
    bot._store_cache = store
    bot._cfg_cache = None
    bot._lib_cache = None
    bot._ocr_cache = None
    bot._pending_frame = None
    bot._first_locked_frame = None
    bot._pool_read = None
    bot._spectators = None
    bot._draft_ratings = (None, None)
    bot._pending_prediction = None
    bot._pending_draft_trios = None

    monkeypatch.setattr(mod, "learn_if_improved", lambda **_kw: False)
    monkeypatch.setattr(mod.MatchStore, "record_audit", lambda *_a, **_k: 1)
    return bot


def _record(bot, monkeypatch, read):
    from adb_auto_player.games.afk_journey.mixins import solstice_clash as mod

    monkeypatch.setattr(mod, "read_summary", lambda *_a, **_k: read)
    bot._record_summary(
        draft_frame=None,
        prematch_frame=None,
        theme=None,
        frame=np.zeros((4, 4, 3), dtype=np.uint8),
    )


def _last(store):
    with store._connect() as con:
        return dict(
            zip(
                ("id", "winning_trio", "blue_trio", "comps_key", "canonical_state"),
                con.execute(
                    "SELECT id, winning_trio, blue_trio, comps_key, canonical_state"
                    " FROM match ORDER BY id DESC LIMIT 1"
                ).fetchone(),
                strict=True,
            )
        )


def test_a_normal_summary_records_and_earns_a_comps_key(bot, store, monkeypatch):
    """The path used to raise on HeroSlot.side AFTER canonical_state was committed,
    so the row looked finished to the migration predicate while staying unpushable
    forever - its comps_key was never set.
    """
    bot._pending_draft_trios = (frozenset(TRIO_A), frozenset(TRIO_B))
    _record(bot, monkeypatch, _read(TRIO_A, TRIO_B, "left"))

    row = _last(store)
    assert row["canonical_state"] == "canonical"
    assert row["winning_trio"] == 1
    assert row["blue_trio"] == 1
    assert row["comps_key"], "a complete match must earn a comps_key or it never syncs"


def test_the_odds_sample_is_actually_written(bot, store, monkeypatch):
    """It was constructed with the removed side-relative keywords, raising a
    TypeError that the broad handler swallowed - so every sample was lost silently
    while the store's own tests passed against the canonical dataclass.
    """
    bot._pending_draft_trios = (frozenset(TRIO_A), frozenset(TRIO_B))
    bot._pool_read = types.SimpleNamespace(
        left_pool=100,
        right_pool=50,
        left_odds=1.5,
        right_odds=2.5,
        crowd_probability=0.66,
    )
    bot._spectators = 12
    _record(bot, monkeypatch, _read(TRIO_A, TRIO_B, "left"))

    samples = store.odds_for(_last(store)["id"])
    assert samples, "the market reading must survive to the database"
    assert samples[0].trio_1_pool == 100  # blue is trio 1 here, so it maps straight


def test_a_mirrored_summary_still_stores_our_side_correctly(bot, store, monkeypatch):
    """1476's shape: the panels arrive the other way round from the draft."""
    bot._pending_draft_trios = (frozenset(TRIO_A), frozenset(TRIO_B))
    # Our blue (TRIO_A) is on the BOTTOM panel this time, and it lost.
    _record(bot, monkeypatch, _read(TRIO_B, TRIO_A, "left"))

    row = _last(store)
    assert row["blue_trio"] == 1  # TRIO_A is ours whichever panel it sat in
    assert row["winning_trio"] == 2  # the top panel won, and that is TRIO_B


def test_the_call_result_is_stated_in_our_frame_not_the_panels(
    bot, store, monkeypatch, caplog
):
    """The defect this whole change exists to remove, in the one line a person reads.

    `p_left` is draft-blue's probability while `read.winner` names a summary panel.
    For a mirrored read they are opposite, so comparing them directly inverts the
    verdict - and it was being compared directly, before orientation was resolved.
    """
    bot._pending_draft_trios = (frozenset(TRIO_A), frozenset(TRIO_B))
    bot._pending_prediction = (0.9, "model", 6)  # we called OUR side, confidently
    # Mirrored: our blue (TRIO_A) is on the bottom panel, and it WON.
    with caplog.at_level("INFO"):
        _record(bot, monkeypatch, _read(TRIO_B, TRIO_A, "right"))

    text = "\n".join(caplog.messages)
    assert "HIT" in text, f"we called our side at 90% and it won: {text}"
    assert "MISS" not in text


def test_an_unresolvable_orientation_withholds_the_verdict(
    bot, store, monkeypatch, caplog
):
    """Refused, not guessed. A coin-flip HIT/MISS is worse than none.

    The market is supplied here ON PURPOSE. An earlier version of this test omitted
    it and therefore passed while `blue_trio == 1` was quietly treating None as
    "blue is trio 2", filing blue's own pools against the other composition.
    """
    bot._pending_prediction = (0.9, "model", 6)
    bot._pending_draft_trios = None  # nothing carried: no draft to anchor against
    bot._pool_read = types.SimpleNamespace(
        left_pool=100,
        right_pool=50,
        left_odds=1.5,
        right_odds=2.5,
        crowd_probability=0.66,
    )
    bot._spectators = 12
    with caplog.at_level("INFO"):
        _record(bot, monkeypatch, _read(TRIO_A, TRIO_B, "left"))

    text = "\n".join(caplog.messages)
    assert "withheld" in text
    row = _last(store)
    assert row["blue_trio"] is None
    # The trios and the winner still stand - they need no orientation.
    assert row["winning_trio"] == 1
    # The market does NOT. Every field, not just the ones that happen to differ.
    samples = store.odds_for(row["id"])
    assert samples
    assert samples[0].trio_1_pool is None
    assert samples[0].trio_2_pool is None
    assert samples[0].trio_1_odds is None
    assert samples[0].trio_2_odds is None
