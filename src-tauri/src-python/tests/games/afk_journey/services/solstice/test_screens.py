"""Screen detection, measured against real captured frames.

The old detector was the "Current Theme:" label. One frame that lost it was read as
"the draft ended", which cut the watch about ten seconds short and capped every live
run at four of six picks.
"""

from pathlib import Path

import cv2
import pytest
from adb_auto_player.games.afk_journey.services.solstice.screens import (
    is_draft_screen,
    is_locked_screen,
    load_templates,
    on_betting_screen,
)

DATA = Path(__file__).parent / "data"
TEMPLATES = (
    Path(__file__).resolve().parents[5]
    / "adb_auto_player/games/afk_journey/templates"
)


@pytest.fixture(scope="module")
def templates():
    return load_templates(TEMPLATES)


def _frame(name: str):
    frame = cv2.imread(str(DATA / name))
    assert frame is not None, f"missing fixture {name}"
    return frame


def test_a_real_draft_frame_is_the_draft_screen(templates):
    assert is_draft_screen(_frame("spectate_draft_locked5.png"), templates)


def test_a_real_draft_frame_is_not_the_locked_screen(templates):
    """The two must never both be true - that is what the VS art is for."""
    assert not is_locked_screen(_frame("spectate_draft_locked5.png"), templates)


def test_a_real_locked_frame_is_the_locked_screen(templates):
    assert is_locked_screen(_frame("spectate_locked_six.png"), templates)


def test_a_real_locked_frame_is_not_the_draft_screen(templates):
    assert not is_draft_screen(_frame("spectate_locked_six.png"), templates)


def test_both_screens_carry_the_all_in_tiles(templates):
    """They corroborate, they do not discriminate - identical coordinates on both."""
    assert on_betting_screen(_frame("spectate_draft_locked5.png"), templates)
    assert on_betting_screen(_frame("spectate_locked_six.png"), templates)


def test_a_summary_frame_is_neither(templates):
    frame = _frame("summary_01.png")
    assert not is_draft_screen(frame, templates)
    assert not is_locked_screen(frame, templates)
