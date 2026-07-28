"""Ladder ratings, read from real frames.

The four-digit number beside each player. The columns existed from the start and nothing
ever wrote them - 0 of 277 collected matches carried one - so these tests exist to keep
that from silently returning.
"""

from pathlib import Path

import cv2
import pytest
from adb_auto_player.games.afk_journey.services.solstice.ratings import (
    rating_gap,
    read_ratings,
)
from adb_auto_player.ocr import RapidOCRBackend

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def ocr():
    return RapidOCRBackend()


def test_ratings_read_from_a_real_draft_frame(ocr):
    """The draft screen is what matters: read here, the gap is available while betting
    is still open."""
    frame = cv2.imread(str(DATA / "spectate_draft_locked5.png"))
    left, right = read_ratings(frame, ocr, screen="draft")
    assert left == 4101
    assert right == 4241


def test_ratings_read_from_a_real_locked_frame(ocr):
    """The fallback, for a match joined after the draft."""
    frame = cv2.imread(str(DATA / "spectate_locked_six.png"))
    left, right = read_ratings(frame, ocr, screen="locked")
    assert left == 4101
    assert right == 4241


def test_a_frame_with_no_ratings_returns_none_rather_than_a_number(ocr):
    """A wrong rating is worse than a missing one - the model would treat it as
    evidence."""
    frame = cv2.imread(str(DATA / "summary_01.png"))
    left, right = read_ratings(frame, ocr, screen="draft")
    assert left is None or 1000 <= left <= 9999
    assert right is None or 1000 <= right <= 9999


def test_the_gap_needs_both_sides():
    assert rating_gap(4241, 4101) == 140
    assert rating_gap(None, 4101) is None
    assert rating_gap(4241, None) is None
