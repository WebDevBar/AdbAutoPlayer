"""Auto-bet: when tokens are staked, and - mostly - when they are not.

This spends the operator's Guess Tokens, so the tests that matter are the ones proving
it does NOT fire: below the confidence line, while the odds are gated, and twice on one
match. A false negative costs nothing; a false positive costs tokens on a call the model
was never confident about.
"""

# ruff: noqa: E402
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("pytauri", MagicMock())
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.games.afk_journey.mixins.solstice_clash import (
    BET_HANDLE_AT,
    SolsticeClashMixin,
)


class _Stub(SolsticeClashMixin):
    """Records swipes instead of performing them.

    `settings` is a read-only property on the real class, hence the override.
    """

    def __init__(self, on=True, threshold=58, offset=5):
        self._s = SimpleNamespace(
            wdb_modes=SimpleNamespace(
                auto_bet=on, auto_bet_threshold=threshold, auto_bet_offset_px=offset
            )
        )
        self.swipes: list[tuple] = []

    @property
    def settings(self):
        return self._s

    def swipe_left(self, y=None, sx=None, ex=None, duration=1.0):
        self.swipes.append(("left", sx, ex, y))

    def swipe_right(self, y=None, sx=None, ex=None, duration=1.0):
        self.swipes.append(("right", sx, ex, y))


def _prediction(p_left):
    return SimpleNamespace(p_mid=p_left)


def test_stakes_on_the_favoured_side_and_drags_from_centre():
    bot = _Stub()
    bot._auto_bet(_prediction(0.62), None)
    assert bot.swipes == [
        ("left", BET_HANDLE_AT.x, BET_HANDLE_AT.x - 5, BET_HANDLE_AT.y)
    ]

    bot = _Stub()
    bot._auto_bet(_prediction(0.38), None)
    assert bot.swipes == [
        ("right", BET_HANDLE_AT.x, BET_HANDLE_AT.x + 5, BET_HANDLE_AT.y)
    ]


@pytest.mark.parametrize(
    "label,prediction,gate,kwargs",
    [
        ("below the line", _prediction(0.55), None, {}),
        ("gated however confident", _prediction(0.90), "not enough matches", {}),
        ("toggle off", _prediction(0.90), None, {"on": False}),
        ("no prediction at all", None, None, {}),
    ],
)
def test_stakes_nothing_when_it_should_not(label, prediction, gate, kwargs):
    bot = _Stub(**kwargs)
    bot._auto_bet(prediction, gate)
    assert bot.swipes == [], label


def test_a_gated_prediction_is_never_staked_even_at_high_confidence():
    """A gate means the model is saying it does not know.

    Staking there is worse than staking at random, because the number looks confident
    while resting on almost no data.
    """
    bot = _Stub()
    bot._auto_bet(_prediction(0.99), "only 12 matches from this theme")
    assert bot.swipes == []


def test_stakes_once_per_match():
    """The odds are recomputed as picks land; only the first may stake."""
    bot = _Stub()
    bot._auto_bet(_prediction(0.62), None)
    bot._auto_bet(_prediction(0.62), None)
    bot._auto_bet(_prediction(0.71), None)
    assert len(bot.swipes) == 1


def test_the_threshold_and_offset_are_configurable():
    bot = _Stub(threshold=52)
    bot._auto_bet(_prediction(0.55), None)
    assert bot.swipes, "55% must stake once the line is lowered to 52"

    bot = _Stub(offset=40)
    bot._auto_bet(_prediction(0.62), None)
    assert bot.swipes[0][2] == BET_HANDLE_AT.x - 40


def test_a_placed_bet_announces_the_side_in_its_colour(caplog):
    """The operator asked for this line specifically: after the handle is dragged, say
    which side the tokens went on, coloured to match the bubble."""
    import logging

    bot = _Stub()
    with caplog.at_level(logging.INFO):
        bot._auto_bet(_prediction(0.62), None)
    line = next(r.message for r in caplog.records if "BETTING" in r.message)
    assert '<span class="sc-blue">BETTING BLUE</span>' in line

    bot2 = _Stub()
    with caplog.at_level(logging.INFO):
        bot2._auto_bet(_prediction(0.38), None)
    line2 = next(
        r.message for r in caplog.records if "BETTING RED" in r.message
    )
    assert '<span class="sc-red">BETTING RED</span>' in line2


def test_a_failed_swipe_never_propagates():
    """Losing a recorded match over a bet would be a bad trade."""

    class Exploding(_Stub):
        def swipe_left(self, **kwargs):
            raise RuntimeError("device gone")

    Exploding()._auto_bet(_prediction(0.62), None)  # must not raise
