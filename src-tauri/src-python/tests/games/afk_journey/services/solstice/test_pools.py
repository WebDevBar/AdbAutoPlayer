"""The betting market, read from real frames.

The crowd's money is a market, and a market usually beats one model - which matters
when the model has 3 matches per hero parameter and no demonstrated edge.
"""

from pathlib import Path

import cv2
import pytest
from adb_auto_player.games.afk_journey.services.solstice.pools import (
    DEFAULT_RAKE,
    PoolRead,
    implied_rake,
    other_odds,
    read_pools,
)
from adb_auto_player.ocr import RapidOCRBackend

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def ocr():
    return RapidOCRBackend()


def test_both_pools_and_both_odds_read_from_a_real_draft(ocr):
    frame = cv2.imread(str(DATA / "spectate_draft_locked5.png"))
    read = read_pools(frame, ocr)
    assert read.left_pool and read.right_pool
    assert read.left_odds and read.right_odds
    # Side comes from position, not reading order.
    assert read.left_pool != read.right_pool


def test_the_crowd_probability_comes_from_the_pools_not_the_odds():
    """The pools need no assumption about the house cut; the odds do."""
    read = PoolRead(left_pool=125924, right_pool=118227, left_odds=1.76, right_odds=1.85)
    assert abs(read.crowd_probability - 0.5158) < 0.001


def test_the_odds_can_stand_in_when_the_pools_are_unreadable():
    """A player betting all-in covers part of the row with their own UI."""
    read = PoolRead(left_odds=1.76, right_odds=1.85)
    assert read.crowd_probability is not None
    assert 0.5 < read.crowd_probability < 0.55


def test_nothing_readable_means_no_opinion():
    assert PoolRead().crowd_probability is None
    assert PoolRead().confidence_weight == 0.0


def test_a_bigger_pool_is_believed_more():
    """More money means more independent opinions behind the same split."""
    thin = PoolRead(left_pool=2_000, right_pool=1_000)
    thick = PoolRead(left_pool=200_000, right_pool=100_000)
    assert thin.crowd_probability == thick.crowd_probability
    assert thin.confidence_weight < thick.confidence_weight


def test_the_house_rake_is_measurable():
    """1/1.76 + 1/1.85 = 1.109 - the excess over a fair book."""
    assert abs(implied_rake(1.76, 1.85) - 0.109) < 0.002


def test_a_missing_side_can_be_reconstructed_from_the_rake():
    """Only sound while the rake holds - which is why it is measured, not assumed."""
    assert abs(other_odds(1.76, DEFAULT_RAKE) - 1.85) < 0.05


def test_an_impossible_odds_value_reconstructs_to_nothing():
    """A misread number must produce None, not a fabricated opposite side."""
    assert other_odds(0.5) is None
