"""The bottom-right control, and the give-up dialog.

One of these two states forfeits a daily attempt, so nothing here guesses: the
control is positively matched as Refresh or X, and anything else is "unknown",
which the caller must treat as a reason to stop rather than to tap.
"""

import logging
from pathlib import Path

import cv2
import numpy as np

from adb_auto_player.models.geometry import Point

from .geometry import (
    CONFIDENCE_FLOOR,
    CONTROL_REGION,
    GIVE_UP_TICK_REGION,
    Mode,
)

_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

# The refresh glyph is NOT shared between modes: Arena's arrow is anticlockwise and
# thin, Supreme Arena's clockwise and thick, and cross-matching scores 0.36 against a
# 0.9 floor. The X IS shared - it cross-matches at 1.00 - so it has no per-mode variant.
_REFRESH_TEMPLATE: dict[Mode, str] = {
    Mode.ARENA: "arena/refresh_glyph.png",
    Mode.SUPREME_ARENA: "supreme_arena/refresh_glyph.png",
}
_GIVE_UP_TEMPLATE = "arena/give_up_glyph.png"
_TICK_TEMPLATE = "arena/give_up_confirm.png"


def _load(name: str) -> np.ndarray:
    """Load a template in BGR, matching what the device and cv2.imread both give."""
    template = cv2.imread(str(_TEMPLATES / name), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(_TEMPLATES / name)
    return template


def _best(region: np.ndarray, template: np.ndarray) -> tuple[float, Point]:
    """Best match of `template` within `region`, with its centre."""
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    centre = Point(
        location[0] + template.shape[1] // 2, location[1] + template.shape[0] // 2
    )
    return float(score), centre


def classify_control(frame: np.ndarray, mode: Mode) -> str:
    """Whether the bottom-right control is Refresh, the X, or unrecognised.

    Both-match and neither-match are BOTH "unknown". The two glyphs are visually
    unalike, so a double match means the read is wrong - and resolving it by higher
    confidence would spend a daily attempt on a coin toss.

    Args:
        frame: BGR frame.
        mode: which screen this is.

    Returns:
        "refresh", "give_up" or "unknown".
    """
    x0, y0, x1, y1 = CONTROL_REGION[mode]
    region = frame[y0:y1, x0:x1]
    try:
        refresh, _ = _best(region, _load(_REFRESH_TEMPLATE[mode]))
        give_up, _ = _best(region, _load(_GIVE_UP_TEMPLATE))
    except (FileNotFoundError, cv2.error) as exc:
        logging.warning(f"[FF-20] could not classify the control: {exc}")
        return "unknown"

    is_refresh = refresh >= CONFIDENCE_FLOOR
    is_give_up = give_up >= CONFIDENCE_FLOOR
    if is_refresh == is_give_up:
        return "unknown"
    return "refresh" if is_refresh else "give_up"


def find_give_up_tick(frame: np.ndarray) -> Point | None:
    """The green confirm tick of the "Give up this challenge?" dialog.

    Detected by the tick rather than the dialog sheet: the sheet is blank (pixel
    std 4.8 against the tick's 54.5) so it carries no information, and the crop that
    looked language-independent actually contained the sentence. The tick is an icon,
    it is feature-rich, and it IS the tap target - so detection and action cannot
    disagree.

    Args:
        frame: BGR frame.

    Returns:
        The matched centre to tap, or None if the dialog is not up.
    """
    x0, y0, x1, y1 = GIVE_UP_TICK_REGION
    region = frame[y0:y1, x0:x1]
    try:
        score, centre = _best(region, _load(_TICK_TEMPLATE))
    except (FileNotFoundError, cv2.error) as exc:
        logging.warning(f"[FF-21] could not look for the give-up tick: {exc}")
        return None
    if score < CONFIDENCE_FLOOR:
        return None
    return Point(x0 + centre.x, y0 + centre.y)
