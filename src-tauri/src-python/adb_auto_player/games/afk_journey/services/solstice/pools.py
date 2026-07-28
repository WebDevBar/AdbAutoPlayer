"""The crowd's money, read from the draft screen - pure, no device, no GUI.

Spectators bet on the match, and the split of that money is the crowd's belief. It is a
market, and a market with many participants is usually better than any one model - which
matters here because our own model has 3 matches per hero parameter and no demonstrated
edge.

Two things shape how this is read:

- **The pools beat the odds.** `125924 / (125924 + 118227)` is the crowd's implied
  probability directly. The displayed odds need the house rake backed out first: on a
  real frame they read 1:1.76 and 1:1.85, whose implied probabilities sum to 1.109, so
  about 10.9% is margin rather than belief.
- **The last reading before lock is the one that counts.** The pools move until the
  final second of the draft, and a player betting all-in covers part of the odds text
  with their own UI. So every successful read replaces the previous one and the last
  survivor is stored - an occluded frame simply leaves the previous value standing.

Reading costs about 436ms, which is why it happens when a pick lands rather than on
every poll: the six-cell hero read already costs ~2s against a ~20s draft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from adb_auto_player.ocr import OCRBackend

# The row carrying both pool totals and both odds, on a 1080x1920 draft screen.
POOL_BAND = (0, 1290, 1080, 1400)
SPECTATOR_BAND = (560, 1740, 900, 1860)

# Pools are large integers; a three-digit reading is a partial number, not a pool.
_POOL = re.compile(r"^\d{3,9}$")
# "Odds: 1:176" - OCR drops the decimal point, so the digits are recovered by scale.
_ODDS = re.compile(r"odds[^0-9]*1[^0-9]*(\d+)", re.IGNORECASE)
_SPECTATORS = re.compile(r"^\d{1,5}$")

# Measured on a real frame: 1/1.76 + 1/1.85 = 1.109. If the house takes a constant cut,
# one visible odds value implies the other - which is what makes a half-occluded row
# still usable. Treated as an ESTIMATE to be refined from collected pairs, never as a
# constant to trust: see `implied_rake`.
DEFAULT_RAKE = 0.109


@dataclass(frozen=True)
class PoolRead:
    """One sample of the betting market."""

    left_pool: int | None = None
    right_pool: int | None = None
    left_odds: float | None = None
    right_odds: float | None = None
    spectators: int | None = None

    @property
    def crowd_probability(self) -> float | None:
        """The crowd's implied P(left wins), or None if it cannot be derived.

        From the pools when both are present, because that needs no assumption about
        the rake. From the odds otherwise, where the rake has to be normalised out -
        the two implied probabilities sum to more than 1 by exactly the house margin.
        """
        if self.left_pool is not None and self.right_pool is not None:
            total = self.left_pool + self.right_pool
            return self.left_pool / total if total > 0 else None
        if self.left_odds and self.right_odds:
            left, right = 1.0 / self.left_odds, 1.0 / self.right_odds
            return left / (left + right)
        return None

    @property
    def confidence_weight(self) -> float:
        """How much this market deserves to be believed, 0 to 1.

        More money means more independent opinions behind the split. A pool of a few
        thousand is one or two bettors; a quarter of a million is a crowd. Saturating
        rather than linear, because past a point more money adds no more information.
        """
        total = (self.left_pool or 0) + (self.right_pool or 0)
        if total <= 0:
            return 0.0
        return float(total / (total + POOL_HALF_WEIGHT))


# The pool size at which the market is believed half as much as it eventually will be.
# 100k is roughly the middle of what a live draft showed; refine once collected.
POOL_HALF_WEIGHT = 100_000.0


def implied_rake(left_odds: float, right_odds: float) -> float:
    """How much the two displayed odds overshoot a fair book.

    Collected across matches, this says whether the rake is a constant we can rely on
    to reconstruct a missing side - or whether it moves, in which case a single visible
    odds value is not enough.
    """
    return 1.0 / left_odds + 1.0 / right_odds - 1.0


def other_odds(known: float, rake: float = DEFAULT_RAKE) -> float | None:
    """The opposite side's odds, assuming the given rake.

    Used only when one side is unreadable - typically because the player's own all-in
    UI covers it. Returns None where the arithmetic has no solution, which is what a
    wrong rake or a misread number produces.
    """
    remaining = (1.0 + rake) - (1.0 / known)
    if remaining <= 0:
        return None
    return 1.0 / remaining


def _numbers(frame: np.ndarray, band: tuple[int, int, int, int], ocr: OCRBackend):
    x0, y0, x1, y1 = band
    return [(b.text.strip(), b.box.center.x + x0) for b in ocr.detect_text_blocks(
        frame[y0:y1, x0:x1]
    )]


def read_pools(frame: np.ndarray, ocr: OCRBackend, width: int = 1080) -> PoolRead:
    """Read the betting row. Every field is independent - a missing one stays None.

    Side is decided by horizontal position, not by reading order: OCR returns blocks in
    whatever order it finds them, and the left pool is simply the one on the left half.
    """
    left_pool = right_pool = None
    left_odds = right_odds = None
    middle = width / 2

    for text, x in _numbers(frame, POOL_BAND, ocr):
        odds = _ODDS.search(text)
        if odds:
            # "1:176" is 1.76 - OCR drops the point, and the digit count restores it.
            digits = odds.group(1)
            value = int(digits) / (10 ** (len(digits) - 1))
            if x < middle:
                left_odds = value
            else:
                right_odds = value
        elif _POOL.match(text):
            if x < middle:
                left_pool = int(text)
            else:
                right_pool = int(text)

    return PoolRead(
        left_pool=left_pool,
        right_pool=right_pool,
        left_odds=left_odds,
        right_odds=right_odds,
    )


def read_spectators(frame: np.ndarray, ocr: OCRBackend) -> int | None:
    """The spectator count. Read once at the end - it costs 305ms and moves slowly."""
    for text, _x in _numbers(frame, SPECTATOR_BAND, ocr):
        if _SPECTATORS.match(text):
            return int(text)
    return None
