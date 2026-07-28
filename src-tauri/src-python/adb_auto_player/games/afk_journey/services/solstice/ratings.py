"""Ladder ratings from the locked-picks screen - pure, no device, no GUI.

The four-digit number beside each player, e.g. 4101 against 4241. It is the single
strongest signal available before a fight and it costs one OCR pass: a player 150 points
ahead is usually ahead for a reason, and unlike hero strengths - 93 parameters over a
few hundred matches - it is ONE number per side that the model can actually learn from.

The columns existed in the schema from the start and nothing ever wrote them: 0 of 277
collected matches carried a rating. This module is what fills them.
"""

from __future__ import annotations

import re

import numpy as np
from adb_auto_player.ocr import OCRBackend

# Bands per screen, on a 1080x1920 frame. Deliberately wide: a tight crop around where
# the number "should" be missed the left one entirely, while a band spanning the
# half-screen reads both every time.
#
# The DRAFT bands matter most. Read there, the gap is available from the first poll and
# can feed the odds while betting is open - which is the whole point. The locked bands
# are the fallback for a match joined after the draft.
DRAFT_BANDS = ((0, 80, 540, 220), (540, 80, 1080, 220))
LOCKED_BANDS = ((0, 740, 540, 850), (540, 740, 1080, 850))

# Ratings are four digits in this event; three would be a misread of a partial number and
# five is not a rating at all. Bounds are a filter, not a guess about the ladder.
_RATING = re.compile(r"^\d{4}$")
MIN_RATING = 1000
MAX_RATING = 9999


def _read_band(frame: np.ndarray, band: tuple[int, int, int, int], ocr: OCRBackend):
    x0, y0, x1, y1 = band
    for block in ocr.detect_text_blocks(frame[y0:y1, x0:x1]):
        text = block.text.strip()
        if _RATING.match(text) and MIN_RATING <= int(text) <= MAX_RATING:
            return int(text)
    return None


def read_ratings(
    frame: np.ndarray, ocr: OCRBackend, screen: str = "draft"
) -> tuple[int | None, int | None]:
    """(left, right) ladder ratings, or None where the number could not be read.

    None rather than a guess: a wrong rating is worse than a missing one, because the
    model would treat it as evidence. Each side is read independently, so one unreadable
    number does not discard the other.
    """
    left_band, right_band = DRAFT_BANDS if screen == "draft" else LOCKED_BANDS
    return _read_band(frame, left_band, ocr), _read_band(frame, right_band, ocr)


def rating_gap(left: int | None, right: int | None) -> int | None:
    """Left minus right, or None if either is missing."""
    if left is None or right is None:
        return None
    return left - right
