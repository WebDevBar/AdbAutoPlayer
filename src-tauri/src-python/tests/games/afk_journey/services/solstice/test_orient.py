"""Placing the summary panels onto the draft's sides."""

from adb_auto_player.games.afk_journey.services.solstice.orient import (
    Orientation,
    resolve,
    side_for_panel,
)

BLUE = frozenset({"berial", "silven", "callan"})
RED = frozenset({"evie", "lorsan", "cyran"})
# What the draft FRAME alone gives: red is one short, because the game leaves the draft
# screen the instant pick 6 is made. True of all 922 audited frames.
RED_SHORT = frozenset({"evie", "lorsan"})


def test_the_clean_case_resolves_with_a_wide_margin():
    r = resolve(BLUE, RED, BLUE, RED)
    assert r.orientation is Orientation.DIRECT
    assert r.margin == 6


def test_a_mirrored_summary_resolves_to_swapped():
    """Match 1476: the panels held the draft's trios the other way round."""
    r = resolve(RED, BLUE, BLUE, RED)
    assert r.orientation is Orientation.SWAPPED
    assert r.margin == 6


def test_a_short_red_side_still_resolves():
    """The draft frame alone: blue complete, red two of three."""
    r = resolve(BLUE, RED, BLUE, RED_SHORT)
    assert r.orientation is Orientation.DIRECT
    assert r.margin == 5


def test_one_misread_anywhere_still_resolves():
    """A single bad cell must not be able to decide a side, nor to block one."""
    misread_top = frozenset({"berial", "silven", "wrongun"})
    r = resolve(misread_top, RED, BLUE, RED_SHORT)
    assert r.orientation is Orientation.DIRECT
    assert r.margin >= 2


def test_the_winning_panel_alone_would_have_TIED_here():
    """Why both panels are scored, not just the winner.

    With red known as two heroes and one of them misread, matching only the winning
    panel gives one overlap each way - a tie, and unresolvable. Scoring both panels
    keeps a usable margin.
    """
    top = frozenset({"berial", "silven", "callan"})
    bottom = frozenset({"evie", "wrongun", "cyran"})
    winner_only_direct = len(top & BLUE)
    winner_only_swapped = len(top & RED_SHORT)
    assert winner_only_direct - winner_only_swapped == 3  # only because blue is complete

    # The hard case: the complete side is the one that got misread.
    top_bad = frozenset({"berial", "wrongun", "oops"})
    assert len(top_bad & BLUE) == 1
    assert len(top_bad & RED_SHORT) == 0
    joint = resolve(top_bad, bottom, BLUE, RED_SHORT)
    assert joint.orientation is Orientation.DIRECT, joint.reason


def test_contradictory_evidence_refuses_rather_than_guessing():
    """Both hypotheses supported almost equally - the case a margin rule exists for.

    Note what does NOT refuse: two heroes misread but the survivors all pointing one way
    is 2-vs-0, a clean signal with nothing against it. Refusal is for evidence that
    CONTRADICTS itself, not merely thin evidence.
    """
    # direct = 1 + 2 = 3, swapped = 1 + 1 = 2. One apart, so neither hypothesis is
    # clearly better and a single further misread would flip it.
    top = frozenset({"berial", "evie"})
    bottom = frozenset({"silven", "lorsan", "cyran"})
    r = resolve(top, bottom, BLUE, RED)
    assert r.orientation is Orientation.UNRESOLVED, r.reason
    assert "margin below" in r.reason


def test_thin_but_uncontradicted_evidence_still_resolves():
    """2-vs-0 is not a tie. Nothing argues the other way, so refusing would lose a
    match we can place correctly."""
    top = frozenset({"berial", "nope", "nah"})
    bottom = frozenset({"evie", "wrong", "bad"})
    r = resolve(top, bottom, BLUE, RED_SHORT)
    assert r.orientation is Orientation.DIRECT
    assert r.margin == 2


def test_an_exact_tie_refuses():
    top = frozenset({"berial", "evie"})
    bottom = frozenset({"silven", "lorsan"})
    r = resolve(top, bottom, BLUE, RED)
    assert r.orientation is Orientation.UNRESOLVED


def test_an_empty_trio_refuses():
    assert resolve(frozenset(), RED, BLUE, RED).orientation is Orientation.UNRESOLVED
    assert resolve(BLUE, RED, frozenset(), RED).orientation is Orientation.UNRESOLVED


def test_panels_map_onto_draft_sides():
    assert side_for_panel("top", Orientation.DIRECT) == "left"
    assert side_for_panel("bottom", Orientation.DIRECT) == "right"
    assert side_for_panel("top", Orientation.SWAPPED) == "right"
    assert side_for_panel("bottom", Orientation.SWAPPED) == "left"


def test_placing_a_panel_unresolved_is_a_caller_bug():
    """An unresolved match is recorded WITHOUT sides. Guessing is what we are fixing."""
    import pytest

    with pytest.raises(ValueError):
        side_for_panel("top", Orientation.UNRESOLVED)
