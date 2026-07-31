"""Measured screen geometry for the friendly-fire guard.

Every constant here came from a fixture frame at 1080x1920, which is the only
resolution the app permits. Nothing in this module is a guess; where a number was
inferred rather than observed, the comment says so.
"""

from enum import StrEnum

from adb_auto_player.models.geometry import Point


class Mode(StrEnum):
    """The two modes this guard serves. They are different SCREENS, not variants."""

    ARENA = "arena"
    SUPREME_ARENA = "supreme_arena"


# Horizontal extent of each opponent card, left to right. A badge component is
# assigned to every card whose range it OVERLAPS - overlap rather than centre,
# because a centre rule is undefined for a component sitting on a boundary and
# guessing wrong marks the wrong card safe.
CARD_X_RANGES: dict[Mode, tuple[tuple[int, int], ...]] = {
    Mode.ARENA: ((40, 340), (395, 700), (755, 1060)),
    Mode.SUPREME_ARENA: ((60, 400), (390, 720), (720, 1050)),
}

# Vertical extent for the OCR crop, per MODE rather than per card: wide enough to
# contain any card's content at any stagger, so it needs no anchoring.
OCR_Y_RANGE: dict[Mode, tuple[int, int]] = {
    Mode.ARENA: (900, 1300),
    Mode.SUPREME_ARENA: (950, 1500),
}

# The bottom-right control, which is Refresh or the X. Identical box in both states.
CONTROL_REGION: dict[Mode, tuple[int, int, int, int]] = {
    Mode.ARENA: (882, 1724, 1052, 1864),
    Mode.SUPREME_ARENA: (860, 1718, 1029, 1859),
}

# Where each glyph template is cut from, so the assets can be re-cut identically.
GLYPH_TEMPLATE_BOX: dict[Mode, tuple[int, int, int, int]] = {
    Mode.ARENA: (930, 1745, 1010, 1825),
    Mode.SUPREME_ARENA: (905, 1735, 985, 1815),
}

# Centre of the bottom-right control, per mode, derived from CONTROL_REGION.
CONTROL_TAP: dict[Mode, Point] = {
    Mode.ARENA: Point(967, 1794),
    Mode.SUPREME_ARENA: Point(944, 1788),
}

# The give-up dialog is detected by its green tick: language-independent,
# feature-rich (std 54.5 against the blank sheet's 4.8), and it IS the tap target.
GIVE_UP_TICK_TEMPLATE_BOX: tuple[int, int, int, int] = (786, 1163, 947, 1321)
GIVE_UP_TICK_REGION: tuple[int, int, int, int] = (700, 1100, 1010, 1380)
GIVE_UP_CANCEL_CENTRE = Point(639, 1240)  # recorded so tests can assert we miss it

# Supreme Arena taps fixed points; Arena locates its cards by template.
SA_TAP_POINTS: tuple[Point, ...] = (Point(165, 950), Point(540, 950), Point(915, 950))

# 0.8 collides: the X template scores 0.8027 against frame 04's no-control region,
# which would classify an unrelated screen as "refreshes exhausted" - the state that
# leads to forfeiting. Real X 1.00, Refresh 0.4252, so 0.9 separates with margin.
CONFIDENCE_FLOOR = 0.9
OCR_CONFIDENCE_FLOOR = 0.6
