"""Badge detection, against the eight fixture frames.

Frames are loaded with cv2.imread and NEVER converted: that gives BGR, which is
exactly what `get_screenshot()` delivers. An earlier design converted to RGB here
only, which would have left the suite green while production applied the predicate
to swapped channels.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.friendly_fire.detect import (
    card_has_badge_text,
    cards_with_badges,
    find_badges,
    is_badge_text,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode

DATA = Path(__file__).parent / "data"


def _frame(name: str) -> np.ndarray:
    path = next(DATA.glob(f"{name}*.png"))
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None, path
    return frame  # BGR, as the device delivers it - do NOT convert


@pytest.mark.parametrize(
    ("frame_name", "mode", "expected"),
    [
        ("01", Mode.ARENA, {1}),
        ("02", Mode.ARENA, {1}),
        ("03", Mode.ARENA, {0}),
        ("04", Mode.ARENA, set()),
        ("05", Mode.SUPREME_ARENA, set()),
        ("06", Mode.SUPREME_ARENA, {2}),
        ("07", Mode.SUPREME_ARENA, {2}),
        ("08", Mode.SUPREME_ARENA, {1}),
    ],
)
def test_each_fixture_flags_exactly_the_right_cards(frame_name, mode, expected):
    assert cards_with_badges(_frame(frame_name), mode) == expected


def test_the_baseline_frame_yields_no_badge_at_all():
    """05 is the frame that proves an empty screen reads empty."""
    assert find_badges(_frame("05")) == []


def test_sword_buttons_are_rejected_by_SHAPE_not_by_area():
    """The largest badge is 7948px and the smallest sword button 8012px, so an
    area-only rule passes every frame in this set while being wrong. If anyone
    relaxes the height or aspect bound, this must fail."""
    badges = find_badges(_frame("01"))
    assert len(badges) == 1
    x0, y0, x1, y1 = badges[0].box
    assert (x1 - x0) / (y1 - y0) >= 2.0
    assert (y1 - y0) <= 80


def test_cyan_guild_badges_are_detected_not_only_green_friend_ones():
    """A green-only predicate sails straight past Guild Member."""
    assert cards_with_badges(_frame("02"), Mode.ARENA) == {1}
    assert cards_with_badges(_frame("07"), Mode.SUPREME_ARENA) == {2}


def test_exact_badge_text_matches():
    assert is_badge_text("Friend")
    assert is_badge_text("Guild Member")


def test_matching_is_case_and_whitespace_insensitive():
    assert is_badge_text("  friend ")
    assert is_badge_text("GUILD   MEMBER")


def test_a_player_named_Friendzone_is_NOT_a_friend():
    """The OCR rectangle contains the opponent NAME row by design, and names are
    arbitrary player strings. A substring rule flags strangers and burns refreshes."""
    assert not is_badge_text("Friendzone")
    assert not is_badge_text("BestFriend")
    assert not is_badge_text("Guild Membership")


def test_unrelated_text_does_not_match():
    for text in ("Refresh : 7/7", "Top 122", "MorganaLaFey", "1491", ""):
        assert not is_badge_text(text)


class _Block:
    def __init__(self, text, confidence):
        from adb_auto_player.models import ConfidenceValue

        self.text = text
        self.confidence = ConfidenceValue(confidence)


def test_low_confidence_boxes_are_ignored():
    """A shaky read must not flag a card and drive the refresh/forfeit ladder."""
    assert card_has_badge_text([_Block("Friend", 0.9)])
    assert not card_has_badge_text([_Block("Friend", 0.4)])


def test_blocks_are_per_card_so_no_coordinate_mapping_is_needed():
    assert not card_has_badge_text([_Block("Bobo", 0.9), _Block("Top 71", 0.9)])
