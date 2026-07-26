"""Cell extraction and hero identification.

## The matching rule, and why it is what it is

**Fix the SCALE, not the offset.** `matchTemplate` searches offsets internally, for
free. Fixing the offset instead dropped one hero from 0.978 to 0.408, because the
correct offset varies per hero. Slide the *cell* across the *scaled icon* - the icon
is the larger image, the opposite orientation to `game_find_template_match`.

**Accept only when score >= accept_score AND margin >= accept_margin.** The margin is
what catches errors: every wrong match observed had a collapsed margin of 0.01-0.04,
while plenty of correct ones sat at 0.70-0.80. Score alone would admit the bad ones
and reject good ones.

Measured baselines this must not regress: locked_pick 54/54 correct across 9 matches
(median 0.9731, min 0.9249); draft_card 18/18 above 0.90.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from .config import Cell, SolsticeConfig
from .icons import IconLibrary

_COLOUR_NDIM = 3  # a BGR frame; 2 means it is already grayscale


@dataclass(frozen=True)
class Identification:
    """The result of matching one cell.

    `runner_up_*` and `candidate_scope`/`pool_miss` are provenance: without them a
    bad pool read and a legitimate out-of-pool hero are indistinguishable later.
    """

    slug: str | None
    art_ref: str | None
    score: float
    margin: float
    status: str  # 'identified' | 'unknown'
    runner_up_slug: str | None = None
    runner_up_score: float | None = None
    candidate_scope: str | None = None  # 'pool' | 'full_library'
    pool_miss: int | None = None


def extract_cell(frame: np.ndarray, cell: Cell) -> np.ndarray:
    """Crop one registered cell from a frame and return it grayscale."""
    crop = frame[cell.y0 : cell.y1, cell.x0 : cell.x1]
    if crop.shape[:2] != (cell.height, cell.width):
        raise ValueError(
            f"cell {cell.name} does not fit the frame - is it 1080x1920? "
            f"got {crop.shape[:2]}, expected {(cell.height, cell.width)}"
        )
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == _COLOUR_NDIM else crop


def _best_over_scales(
    icon: np.ndarray, cell_gray: np.ndarray, scales: tuple[float, ...]
) -> float:
    """Best correlation of the cell against the icon, over the scale chain.

    The cell is the template and the scaled icon is the search space, so matchTemplate
    finds the alignment itself.
    """
    best = -1.0
    cell_h, cell_w = cell_gray.shape
    for scale in scales:
        width, height = int(icon.shape[1] * scale), int(icon.shape[0] * scale)
        if width < cell_w or height < cell_h:
            continue
        resized = cv2.resize(icon, (width, height))
        score = float(cv2.matchTemplate(resized, cell_gray, cv2.TM_CCOEFF_NORMED).max())
        best = max(best, score)
    return best


def identify_cell(
    cell_gray: np.ndarray,
    cell_type: str,
    library: IconLibrary,
    cfg: SolsticeConfig,
    candidates: set[str] | None = None,
) -> Identification:
    """Identify the hero in one cell, or return status 'unknown'.

    `unknown` is a first-class outcome meaning "sit this round out" - never a guess.
    """
    entries = library.for_slugs(candidates) if candidates else library.entries()
    if not entries:
        return Identification(None, None, 0.0, 0.0, "unknown")

    scales = cfg.scale_chain(cell_type)
    best_per_slug: dict[str, tuple[float, str]] = {}
    for entry in entries:
        score = _best_over_scales(entry.gray, cell_gray, scales)
        current = best_per_slug.get(entry.slug)
        if current is None or score > current[0]:
            best_per_slug[entry.slug] = (score, entry.art_ref)

    ranked = sorted(
        ((score, slug, art) for slug, (score, art) in best_per_slug.items()),
        reverse=True,
    )
    top_score, top_slug, top_art = ranked[0]
    runner_up_score = ranked[1][0] if len(ranked) > 1 else None
    runner_up_slug = ranked[1][1] if len(ranked) > 1 else None
    margin = top_score - (runner_up_score if runner_up_score is not None else -1.0)

    accepted = top_score >= cfg.tunable_float(
        "accept_score"
    ) and margin >= cfg.tunable_float("accept_margin")
    return Identification(
        slug=top_slug if accepted else None,
        art_ref=top_art if accepted else None,
        score=top_score,
        margin=margin,
        status="identified" if accepted else "unknown",
        runner_up_slug=runner_up_slug,
        runner_up_score=runner_up_score,
    )


def identify_with_pool(
    cell_gray: np.ndarray,
    cell_type: str,
    library: IconLibrary,
    cfg: SolsticeConfig,
    pool: set[str] | None,
) -> Identification:
    """Tier 1: the match pool. Tier 2: the full library. Then unknown.

    The result records WHICH tier answered, so a bad pool read is distinguishable from a
    legitimate hero outside the pool.
    """
    if pool:
        first = identify_cell(cell_gray, cell_type, library, cfg, candidates=pool)
        if first.status == "identified":
            return replace(first, candidate_scope="pool", pool_miss=0)
    fallback = identify_cell(cell_gray, cell_type, library, cfg)
    return replace(fallback, candidate_scope="full_library", pool_miss=1 if pool else 0)
