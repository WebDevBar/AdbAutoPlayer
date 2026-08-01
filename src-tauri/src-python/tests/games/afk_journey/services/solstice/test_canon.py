"""The one sort, and the one write boundary.

`assert_canonical` is the only place canonical ORDERING is enforced - SQLite cannot
express a cross-row comparison as a CHECK - so every rule below has a test that
deliberately breaches it. A constraint nobody has tried to break is a comment.
"""

import importlib.util
from pathlib import Path

import pytest

from adb_auto_player.games.afk_journey.services.solstice.canon import (
    assert_canonical,
    canonical_trios,
    map_side_pair,
    trio_index_for,
)


def _rows(pairs):
    return [
        {"trio": t, "slot": s, "hero_slug": g, "status": "identified"}
        for t, s, g in pairs
    ]


def test_canonical_trios_matches_the_comps_key_sort():
    by_side = {
        "left": ["zandrok", "brutus", "hepler"],
        "right": ["mikola", "atalanta", "sonja"],
    }
    t1, t2 = canonical_trios(by_side)
    assert t1 == ["atalanta", "mikola", "sonja"]
    assert t2 == ["brutus", "hepler", "zandrok"]


def test_trio_index_for_and_map_side_pair_agree():
    by_side = {"left": ["m", "n", "o"], "right": ["a", "b", "c"]}
    t1, _ = canonical_trios(by_side)
    assert trio_index_for("left", t1, by_side) == 2
    assert map_side_pair("L", "R", 2) == ("R", "L")


def test_assert_canonical_accepts_a_good_match():
    assert_canonical(
        _rows(
            [
                (1, 1, "a"),
                (1, 2, "b"),
                (1, 3, "c"),
                (2, 1, "m"),
                (2, 2, "n"),
                (2, 3, "o"),
            ]
        )
    )


def test_assert_canonical_rejects_inverted_numbering():
    """The round-7 hole: every other constraint passes while trio 1 holds the
    lexicographically LARGER composition, so every pointer means the other trio.
    """
    with pytest.raises(ValueError, match="not canonically ordered"):
        assert_canonical(
            _rows(
                [
                    (1, 1, "m"),
                    (1, 2, "n"),
                    (1, 3, "o"),
                    (2, 1, "a"),
                    (2, 2, "b"),
                    (2, 3, "c"),
                ]
            )
        )


def test_assert_canonical_rejects_a_hero_in_both_trios():
    with pytest.raises(ValueError, match="both trios"):
        assert_canonical(
            _rows(
                [
                    (1, 1, "a"),
                    (1, 2, "b"),
                    (1, 3, "c"),
                    (2, 1, "a"),
                    (2, 2, "n"),
                    (2, 3, "o"),
                ]
            )
        )


def test_assert_canonical_rejects_a_bad_slot():
    with pytest.raises(ValueError, match="slot"):
        assert_canonical(
            _rows(
                [
                    (1, 1, "a"),
                    (1, 2, "b"),
                    (1, 99, "c"),
                    (2, 1, "m"),
                    (2, 2, "n"),
                    (2, 3, "o"),
                ]
            )
        )


def test_assert_canonical_rejects_a_duplicate_slot():
    with pytest.raises(ValueError, match="slot"):
        assert_canonical(
            _rows(
                [
                    (1, 1, "a"),
                    (1, 1, "b"),
                    (1, 3, "c"),
                    (2, 1, "m"),
                    (2, 2, "n"),
                    (2, 3, "o"),
                ]
            )
        )


def test_assert_canonical_rejects_a_bad_trio_number():
    with pytest.raises(ValueError, match="trio"):
        assert_canonical(
            _rows(
                [
                    (3, 1, "a"),
                    (1, 2, "b"),
                    (1, 3, "c"),
                    (2, 1, "m"),
                    (2, 2, "n"),
                    (2, 3, "o"),
                ]
            )
        )


def test_assert_canonical_allows_an_incomplete_unidentified_read():
    """An in-progress match is not a violation - only a COMPLETE one is checked."""
    assert_canonical([{"trio": 1, "slot": 1, "hero_slug": None, "status": "unknown"}])


def test_the_two_canonical_implementations_agree():
    """`migrate.py` runs standalone and cannot import the package, so the three pure
    functions exist twice on purpose. This is what stops the copies drifting.
    """
    root = Path(__file__).resolve()
    while root.name != "adbautoplayer":
        root = root.parent
    spec = importlib.util.spec_from_file_location(
        "_canon_rows", root / "data" / "solstice_clash" / "canon_rows.py"
    )
    std = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(std)

    cases = [
        {"left": ["m", "n", "o"], "right": ["a", "b", "c"]},
        {"left": ["a", "b", "c"], "right": ["m", "n", "o"]},
        {"left": ["zandrok", "brutus", "hepler"], "right": ["mikola", "atalanta", "x"]},
    ]
    for by_side in cases:
        assert std.canonical_trios(by_side) == canonical_trios(by_side)
        t1, _ = canonical_trios(by_side)
        for side in ("left", "right"):
            assert std.trio_index_for(side, t1, by_side) == trio_index_for(
                side, t1, by_side
            )
        assert std.map_side_pair(1, 2, 2) == map_side_pair(1, 2, 2)
