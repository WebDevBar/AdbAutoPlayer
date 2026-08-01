"""Badge detection: is this opponent a Friend or a Guild Member?

Two properties are load-bearing and both were established by measurement, not choice.

The badge is found by SHAPE, not position. An earlier design anchored a search band
to the player-name row; nothing in a frame identifies that row against the adjacent
score and rank text, so it was not implementable. A badge is a wide short bar and the
green battle button is a blob - aspect 3.0-6.2 against 0.7 - which needs no anchor and
makes the cards' vertical stagger irrelevant.

Area alone does NOT separate them: the largest observed badge is 7948px and the
smallest sword button 8012px. Aspect ratio is what does the work.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import CARD_X_RANGES, Mode

# The colour predicate, named rather than inlined so ruff's magic-value rule passes
# and, more importantly, so the numbers are visible in one place. Changing any of
# these changes what counts as a badge, which is a safety decision.
_GREEN_MIN_FRIEND = 120
_GREEN_MIN_GUILD = 130
_BLUE_MIN_GUILD = 130
_RED_MAX = 110
_GREEN_OVER_RED = 60
_GREEN_OVER_BLUE = 40
_GREEN_BLUE_SPREAD = 45

_SPECK_AREA = 400
_MIN_AREA = 2000
_MIN_WIDTH = 100
_MAX_HEIGHT = 80
_MIN_ASPECT = 2.0
_BADGE_TEXTS = frozenset({"friend", "guild member"})
_OCR_FLOOR = 0.6


@dataclass(frozen=True)
class Badge:
    """One detected badge and where it sits."""

    kind: str  # "friend" or "guild"
    box: tuple[int, int, int, int]  # x0, y0, x1, y1


def _colour_masks(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel masks for the two badge colours.

    Exact predicates rather than a tolerance: a "within N of this RGB" formulation
    flips the answer with N, and one setting attacks the friend.

    Args:
        frame: BGR frame, as `get_screenshot()` returns it.

    Returns:
        The Friend mask and the Guild Member mask.
    """
    # BGR, because that is what the device gives us. Naming the channels rather
    # than indexing by convention is what stops the swap bug coming back.
    blue = frame[:, :, 0].astype(np.int16)
    green = frame[:, :, 1].astype(np.int16)
    red = frame[:, :, 2].astype(np.int16)
    friend = (
        (green > _GREEN_MIN_FRIEND)
        & (red < _RED_MAX)
        & (green - red > _GREEN_OVER_RED)
        & (green - blue > _GREEN_OVER_BLUE)
    )
    guild = (
        (green > _GREEN_MIN_GUILD)
        & (blue > _BLUE_MIN_GUILD)
        & (red < _RED_MAX)
        & (np.abs(green - blue) < _GREEN_BLUE_SPREAD)
        & (green - red > _GREEN_OVER_RED)
    )
    return friend, guild


def find_badges(frame: np.ndarray) -> list[Badge]:
    """Every badge on the frame, found by colour then filtered by shape.

    Args:
        frame: BGR frame, 1080x1920, straight from `get_screenshot()`.

    Returns:
        One `Badge` per qualifying component, in no particular order.
    """
    friend, guild = _colour_masks(frame)
    found: list[Badge] = []
    for kind, mask in (("friend", friend), ("guild", guild)):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8
        )
        for index in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[index])
            if area < _SPECK_AREA:
                continue
            if (
                area >= _MIN_AREA
                and width >= _MIN_WIDTH
                and height <= _MAX_HEIGHT
                and width / height >= _MIN_ASPECT
            ):
                found.append(Badge(kind=kind, box=(x, y, x + width, y + height)))
    return found


def cards_with_badges(
    frame: np.ndarray, mode: Mode, cards: frozenset[int] | None = None
) -> set[int]:
    """Indices of cards carrying a badge.

    Assignment is by x-range OVERLAP. A component overlapping two ranges flags BOTH,
    which errs toward refusing to attack - a centre rule is undefined on a boundary
    and the cost of guessing wrong is attacking a friend.

    Args:
        frame: BGR frame.
        mode: which screen this is.
        cards: restrict to these zero-based indices. Arena never takes card 3, so
            scanning it only produces a flag the caller discards - and a flag on a
            card we cannot take reads, in a log, like a detection we ignored.

    Returns:
        Zero-based card indices.
    """
    flagged: set[int] = set()
    for badge in find_badges(frame):
        bx0, _, bx1, _ = badge.box
        for index, (x0, x1) in enumerate(CARD_X_RANGES[mode]):
            if cards is not None and index not in cards:
                continue
            if bx0 < x1 and x0 < bx1:
                flagged.add(index)
    return flagged


def is_badge_text(text: str) -> bool:
    """Whether one OCR box reads exactly as a badge label.

    Equality on a normalised single box, never a substring of the whole card. The
    OCR rectangle deliberately contains the opponent's NAME row, and names are
    arbitrary: a substring rule flags a player called "Friendzone".

    Args:
        text: raw OCR text for one box.

    Returns:
        True if it is exactly a badge label.
    """
    return " ".join(text.split()).casefold() in _BADGE_TEXTS


def card_has_badge_text(blocks: list) -> bool:
    """Whether ONE card's OCR blocks contain a badge label.

    Takes the blocks for a single card, so no coordinate mapping is needed: the
    caller already cropped to that card. An earlier draft compared crop-local x
    against full-screen card ranges, which silently misassigned every card but the
    first.

    Args:
        blocks: `OCRResult` items from that card's crop only.

    Returns:
        True if any block is a badge label at or above the confidence floor.
    """
    return any(
        float(block.confidence.value) >= _OCR_FLOOR and is_badge_text(block.text)
        for block in blocks
    )
