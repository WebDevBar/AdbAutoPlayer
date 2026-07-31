"""The decision, as a pure function. No device, no frames."""

import pytest
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode
from adb_auto_player.games.afk_journey.services.friendly_fire.select import (
    Action,
    decide,
    preference_order,
)
from adb_auto_player.games.afk_journey.settings import OpponentPosition


def test_arena_order_is_always_card_1_then_2():
    assert preference_order(Mode.ARENA, OpponentPosition.Left) == (0, 1)
    assert preference_order(Mode.ARENA, OpponentPosition.Right) == (0, 1)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (OpponentPosition.Left, (0, 1)),
        (OpponentPosition.Middle, (1, 0)),
        (OpponentPosition.Right, (2, 0, 1)),
    ],
)
def test_supreme_arena_respects_the_configured_position(position, expected):
    """The toggle must never silently override a setting the user chose."""
    assert preference_order(Mode.SUPREME_ARENA, position) == expected


def test_card_3_is_never_a_fallback():
    """Right offers card 3 FIRST, but a flagged card 1 must not fall back onto it."""
    assert 2 not in preference_order(Mode.SUPREME_ARENA, OpponentPosition.Left)
    assert 2 not in preference_order(Mode.SUPREME_ARENA, OpponentPosition.Middle)


def test_first_unflagged_card_in_order_is_taken():
    d = decide((0, 1), set(), set(), "refresh")
    assert d.action is Action.TAKE and d.card == 0


def test_a_flagged_first_choice_falls_through_to_the_second():
    d = decide((0, 1), {0}, set(), "refresh")
    assert d.action is Action.TAKE and d.card == 1


def test_either_signal_alone_is_enough_to_skip():
    assert decide((0, 1), {0}, set(), "refresh").card == 1
    assert decide((0, 1), set(), {0}, "refresh").card == 1


def test_all_flagged_with_refreshes_left_refreshes():
    assert decide((0, 1), {0, 1}, {0, 1}, "refresh").action is Action.REFRESH


def test_all_flagged_and_exhausted_gives_up_when_both_signals_agree():
    assert decide((0, 1), {0, 1}, {0, 1}, "give_up").action is Action.GIVE_UP


def test_exhausted_with_a_single_signal_flag_STOPS_rather_than_forfeiting():
    """A persistent false positive drains refreshes and would otherwise spend a daily
    attempt on a false read. One signal may skip a card; it may never forfeit."""
    assert decide((0, 1), {0, 1}, {0}, "give_up").action is Action.STOP


def test_an_unknown_control_never_taps_anything():
    assert decide((0, 1), {0, 1}, {0, 1}, "unknown").action is Action.STOP


def test_three_cards_all_flagged_is_handled_not_just_two():
    """'both are flagged' assumed two cards; Right evaluates three."""
    d = decide((2, 0, 1), {0, 1, 2}, {0, 1, 2}, "refresh")
    assert d.action is Action.REFRESH


def test_an_excluded_card_is_never_taken_again():
    """A card rejected by the confirming read must stay rejected, even if a later
    frame reads clear - otherwise the loop can attack it on the next pass."""
    d = decide((0, 1), set(), set(), "refresh", excluded=frozenset({0}))
    assert d.action is Action.TAKE and d.card == 1


def test_an_excluded_card_can_never_justify_forfeiting():
    """Exclusion is one-signal evidence by definition, so it must not satisfy the
    both-signals precondition for spending an attempt."""
    d = decide((0, 1), {1}, {1}, "give_up", excluded=frozenset({0}))
    assert d.action is Action.STOP
