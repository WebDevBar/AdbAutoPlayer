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
from pathlib import Path

import cv2
import numpy as np

from .config import Cell, SolsticeConfig
from .icons import IconLibrary

_COLOUR_NDIM = 3  # a BGR frame; 2 means it is already grayscale

# Screen anchors. Brightness-based classification was tried and scored 1/5, so both
# screens are identified by template anchor. Measured separation on the fixture frames:
#   draft_selecting  1.000 on its own screen, 0.432 / 0.450 on the others
#   prematch_locked  1.000 on its own screen, 0.553 / 0.490 on the others
_DRAFT_ANCHOR_AT = (630, 320)  # (y, x) where the anchor was cut
_DRAFT_ANCHOR_MIN = 0.90
_PREMATCH_ANCHOR_AT = (1000, 360)
# Lower than draft on purpose: across other real prematch frames the worst observed
# self-score was 0.873, so 0.90 would reject genuine screens. The colour-split check
# below is the second, independent signal that keeps this safe.
_PREMATCH_ANCHOR_MIN = 0.80
_ANCHOR_PAD = 30  # search window, so matchTemplate can align

# The blue/red team-plate split. This is a structural colour signature, not the
# discredited "overall brightness" heuristic: it correctly identified all 23 prematch
# frames in the capture set.
_PLATE_BAND = (1300, 1420)
_PLATE_SPLIT_X = (500, 580)
_PLATE_MIN_DELTA = 25

# Ban-overlay glyph matching. Measured: 0.709 and 1.000 on the two banned cards in
# the fixture frame, versus 0.117-0.380 on the other eighteen. Do not relax this -
# a failure means a missing glyph variant, not a bad threshold.
BAN_MATCH_THRESHOLD = 0.60


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


def _anchor_score(gray: np.ndarray, anchor: np.ndarray, at: tuple[int, int]) -> float:
    """Match an anchor near where it was cut, with a pad so alignment can drift."""
    y, x = at
    window = gray[
        max(0, y - _ANCHOR_PAD) : y + anchor.shape[0] + _ANCHOR_PAD,
        max(0, x - _ANCHOR_PAD) : x + anchor.shape[1] + _ANCHOR_PAD,
    ]
    if window.shape[0] < anchor.shape[0] or window.shape[1] < anchor.shape[1]:
        return -1.0
    return float(cv2.matchTemplate(window, anchor, cv2.TM_CCOEFF_NORMED).max())


def _has_team_plates(frame: np.ndarray) -> bool:
    """Blue plate on the left, red on the right - the pre-match team banners."""
    if frame.ndim != _COLOUR_NDIM:
        return False
    band = frame[_PLATE_BAND[0] : _PLATE_BAND[1]].astype(np.int16)
    left, right = _PLATE_SPLIT_X
    blue = (band[:, :left, 0] - band[:, :left, 2]).mean()
    red = (band[:, right:, 2] - band[:, right:, 0]).mean()
    return bool(blue > _PLATE_MIN_DELTA and red > _PLATE_MIN_DELTA)


def classify_screen(frame: np.ndarray, anchor_dir: Path) -> str:
    """Return 'draft', 'prematch_locked' or 'unknown'.

    Spectate must never classify as draft - the two do NOT share geometry (the draft
    anchor scores 0.450 on a spectate frame), and reading draft cells off a spectate
    screen would silently produce nonsense.
    """
    gray = (
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == _COLOUR_NDIM else frame
    )

    draft = cv2.imread(str(anchor_dir / "draft_selecting.png"), cv2.IMREAD_GRAYSCALE)
    if (
        draft is not None
        and _anchor_score(gray, draft, _DRAFT_ANCHOR_AT) >= _DRAFT_ANCHOR_MIN
    ):
        return "draft"

    prematch = cv2.imread(str(anchor_dir / "prematch_locked.png"), cv2.IMREAD_GRAYSCALE)
    if prematch is not None:
        score = _anchor_score(gray, prematch, _PREMATCH_ANCHOR_AT)
        # BOTH signals required - either alone is too weak to trust.
        if score >= _PREMATCH_ANCHOR_MIN and _has_team_plates(frame):
            return "prematch_locked"
    return "unknown"


def load_ban_glyphs(anchor_dir: Path) -> list[np.ndarray]:
    """Every ban_glyph_*.png in the anchor directory.

    There are at least THREE variants. Matching only the red/blue pair missed one
    hero's overlay (0.279 / 0.241, under the 0.60 threshold), so a banned card leaked
    into results as a phantom hero.
    """
    return [
        img
        for img in (
            cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            for p in sorted(anchor_dir.glob("ban_glyph_*.png"))
        )
        if img is not None
    ]


def is_banned(
    cell_gray: np.ndarray,
    ban_glyphs: list[np.ndarray],
    threshold: float = BAN_MATCH_THRESHOLD,
) -> bool:
    """Detect the circle-slash overlay on a banned card.

    A colour-cast test was tried first and false-positived on 10 of 20 real cards -
    red-haired heroes trip it. Measured separation with glyph templates: 0.709 and
    1.000 on the two banned cards, versus 0.117-0.380 on the other eighteen.
    """
    for glyph in ban_glyphs:
        if glyph.shape[0] > cell_gray.shape[0] or glyph.shape[1] > cell_gray.shape[1]:
            continue
        score = float(cv2.matchTemplate(cell_gray, glyph, cv2.TM_CCOEFF_NORMED).max())
        if score >= threshold:
            return True
    return False


@dataclass(frozen=True)
class PoolRead:
    """The 20 heroes on offer for one match, and which were banned."""

    slugs: set[str]
    per_slot: dict[int, Identification]
    banned_slots: set[int]


def identify_pool(
    frame: np.ndarray,
    cfg: SolsticeConfig,
    library: IconLibrary,
    anchor_dir: Path,
) -> PoolRead:
    """Read the 5x4 draft grid.

    Capturing this at draft start constrains every later identification from ~121
    candidates to <= 20, and self-validates: a locked pick that is not in the pool is a
    DETECTED error rather than a silent wrong answer. The window is multi-second, since
    a human has to read the grid and decide a ban, so a 3/sec poll catches it.
    """
    glyphs = load_ban_glyphs(anchor_dir)
    per_slot: dict[int, Identification] = {}
    banned: set[int] = set()
    for cell in cfg.cells("draft_card"):
        slot = cell.slot
        if slot is None:
            continue
        gray = extract_cell(frame, cell)
        if is_banned(gray, glyphs):
            banned.add(slot)
            continue
        per_slot[slot] = identify_cell(gray, "draft_card", library, cfg)
    slugs = {r.slug for r in per_slot.values() if r.slug is not None}
    return PoolRead(slugs=slugs, per_slot=per_slot, banned_slots=banned)
