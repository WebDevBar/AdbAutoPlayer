"""Classifying the bottom-right control, and finding the give-up tick.

This decides whether we tap a control that forfeits a daily attempt, so the
fixtures matter more here than anywhere else in the suite.
"""

from pathlib import Path

import cv2
from adb_auto_player.games.afk_journey.services.friendly_fire.control import (
    classify_control,
    find_give_up_tick,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    CONTROL_REGION,
    GIVE_UP_CANCEL_CENTRE,
    GLYPH_TEMPLATE_BOX,
    Mode,
)

DATA = Path(__file__).parent / "data"
TEMPLATES = (
    Path(__file__).parents[5] / "adb_auto_player/games/afk_journey/templates"
)


def _frame(name):
    frame = cv2.imread(str(next(DATA.glob(f"{name}*.png"))), cv2.IMREAD_COLOR)
    assert frame is not None
    return frame


def test_every_template_fits_inside_its_search_region():
    """A 170px template into a 169px region cannot be matched at all."""
    for mode in Mode:
        rx0, ry0, rx1, ry1 = CONTROL_REGION[mode]
        tx0, ty0, tx1, ty1 = GLYPH_TEMPLATE_BOX[mode]
        assert tx1 - tx0 < rx1 - rx0
        assert ty1 - ty0 < ry1 - ry0


def test_arena_refresh_and_x_are_told_apart():
    assert classify_control(_frame("01"), Mode.ARENA) == "refresh"
    assert classify_control(_frame("03"), Mode.ARENA) == "give_up"


def test_supreme_arena_is_classified_with_its_OWN_template():
    """The Arena refresh glyph scores 0.36 here. Deleting the Supreme Arena template
    and pointing at Arena's must break this test, because the symptom in production
    is a mode that quits instead of refreshing."""
    assert classify_control(_frame("05"), Mode.SUPREME_ARENA) == "refresh"
    assert classify_control(_frame("06"), Mode.SUPREME_ARENA) == "give_up"


def test_supreme_arena_refresh_template_actually_exists_and_differs():
    """The regression guard for the shared-artwork claim."""
    arena = cv2.imread(str(TEMPLATES / "arena/refresh_glyph.png"), cv2.IMREAD_COLOR)
    supreme = cv2.imread(
        str(TEMPLATES / "supreme_arena/refresh_glyph.png"), cv2.IMREAD_COLOR
    )
    assert arena is not None and supreme is not None
    x0, y0, x1, y1 = CONTROL_REGION[Mode.SUPREME_ARENA]
    region = _frame("05")[y0:y1, x0:x1]
    own = cv2.matchTemplate(region, supreme, cv2.TM_CCOEFF_NORMED).max()
    cross = cv2.matchTemplate(region, arena, cv2.TM_CCOEFF_NORMED).max()
    assert own >= 0.9
    assert cross < 0.9


def test_a_screen_with_no_control_is_unknown_not_guessed():
    """The dialog frame has no bottom-right control. Unknown must never tap.

    This is the case a 0.8 floor gets wrong: the X scores 0.8027 here.
    """
    assert classify_control(_frame("04"), Mode.ARENA) == "unknown"


def test_the_give_up_tick_is_found_and_is_the_tap_target():
    point = find_give_up_tick(_frame("04"))
    assert point is not None
    assert abs(point.x - 866) <= 12
    assert abs(point.y - 1241) <= 12


def test_the_tick_is_not_the_cancel_button():
    point = find_give_up_tick(_frame("04"))
    assert abs(point.x - GIVE_UP_CANCEL_CENTRE.x) > 100


def test_no_tick_on_a_screen_without_the_dialog():
    for name in ("01", "05"):
        assert find_give_up_tick(_frame(name)) is None
