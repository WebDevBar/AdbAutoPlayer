"""Searching for the crop and scale that identify a hero most confidently.

Only ever called with an identity that a long-press OCR confirmed. Tuning toward an
UNCONFIRMED identity would make a wrong answer score better, potentially pushing it past
the accept threshold and suppressing the very check that would have caught it - the
optimiser amplifies whatever it is pointed at, including an error.

It maximises MARGIN, not raw score: every wrong match observed in Phase 1 had a collapsed
margin of 0.01-0.04, and a hero at 0.78 with 0.20 margin is safer than one at 0.85 with
0.05.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import SolsticeConfig
from .icons import IconLibrary
from .vision import _best_over_scales

# matchTemplate searches x/y offsets internally for free, so the offset is NOT tuned -
# fixing it instead of the scale dropped one hero from 0.978 to 0.408 in Phase 1. What is
# worth tuning is which part of the CARD is cut, and the scale.
DEFAULT_CROPS: tuple[tuple[int, int, int], ...] = tuple(
    (half_w, top, bottom)
    for half_w in (22, 24, 26, 28)
    for top in (14, 16, 18, 20)
    for bottom in (26, 28, 30, 32)
)
DEFAULT_SCALES: tuple[float, ...] = tuple(
    round(0.30 + 0.01 * i, 3) for i in range(56)
)


@dataclass(frozen=True)
class TuneResult:
    scale: float
    crop_half_w: int
    crop_top: int
    crop_bottom: int
    score: float
    margin: float


def tune_cell(
    gray: np.ndarray,
    centre: tuple[int, int],
    truth_slug: str,
    library: IconLibrary,
    cfg: SolsticeConfig,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    crops: tuple[tuple[int, int, int], ...] = DEFAULT_CROPS,
) -> TuneResult | None:
    """Find the crop and scale maximising the margin for `truth_slug`.

    Args:
        gray: the full frame, grayscale.
        centre: (x, y) centre of the card.
        truth_slug: the CONFIRMED hero. Never pass an unconfirmed guess.
        library: icon library.
        cfg: loaded config.
        scales: scale chain to search.
        crops: (half_w, top, bottom) insets to search.

    Returns:
        The best parameters where `truth_slug` actually wins, or None if it never does -
        which means the confirmation and the image disagree, and nothing should be learned.
    """
    cx, cy = centre
    entries = library.entries()
    best: TuneResult | None = None

    for half_w, top, bottom in crops:
        cell = gray[cy - top : cy + bottom, cx - half_w : cx + half_w]
        if cell.size == 0:
            continue

        per_slug: dict[str, float] = {}
        for entry in entries:
            score = _best_over_scales(entry.gray, cell, scales)
            if score > per_slug.get(entry.slug, -1.0):
                per_slug[entry.slug] = score

        ranked = sorted(((v, k) for k, v in per_slug.items()), reverse=True)
        if len(ranked) < 2 or ranked[0][1] != truth_slug:
            continue

        score = ranked[0][0]
        margin = score - ranked[1][0]
        if best is None or margin > best.margin:
            scale = _best_scale_for(
                next(e for e in entries if e.slug == truth_slug).gray, cell, scales
            )
            best = TuneResult(scale, half_w, top, bottom, score, margin)

    return best


def _best_scale_for(
    icon: np.ndarray, cell: np.ndarray, scales: tuple[float, ...]
) -> float:
    """The single scale at which this icon matches this cell best.

    Storing it collapses the scale chain from ~56 steps to 1 on later sightings.
    """
    import cv2

    best_score, best_scale = -1.0, scales[0]
    cell_h, cell_w = cell.shape
    for scale in scales:
        width, height = int(icon.shape[1] * scale), int(icon.shape[0] * scale)
        if width < cell_w or height < cell_h:
            continue
        resized = cv2.resize(icon, (width, height))
        score = float(cv2.matchTemplate(resized, cell, cv2.TM_CCOEFF_NORMED).max())
        if score > best_score:
            best_score, best_scale = score, scale
    return best_scale
