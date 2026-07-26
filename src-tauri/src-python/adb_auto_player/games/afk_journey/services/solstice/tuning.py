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

import cv2
import numpy as np

from .config import SolsticeConfig
from .icons import IconLibrary
from .store import AuditRow
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


# Only tune reads in this band. Above it there is nothing worth gaining; below it the read
# was rejected outright, so the identity is not trustworthy enough to tune toward.
TUNE_BAND = (0.70, 0.80)


def learn_if_improved(
    *,
    store,
    cfg: SolsticeConfig,
    library: IconLibrary,
    gray: np.ndarray,
    centre: tuple[int, int],
    screen_slug: str,
    image_slug: str | None,
    confirmed_slug: str | None,
    art_ref: str,
    current_score: float,
    current_margin: float,
    audit_id: int | None,
) -> bool:
    """Tune this cell and store the result, but ONLY from confirmed evidence.

    Tuning toward an UNCONFIRMED identity would make a wrong answer score better and could
    push it past the accept threshold, suppressing the very check that would have caught
    it - the optimiser amplifies whatever it is pointed at, including an error.

    Returns:
        True if a transform was stored.
    """
    # The audit row only counts as confirmation when BOTH channels named the same hero.
    # Checking merely that OCR produced a name is not enough: on a real false positive
    # record_audit() writes agreed=0, and learn_transform() would then raise out of the
    # caller mid-write, failing the whole cycle after some rows were already persisted.
    # A disagreement is an expected outcome here, not an error - it returns False.
    if confirmed_slug is None or audit_id is None or confirmed_slug != image_slug:
        return False
    low, high = TUNE_BAND
    if not (low <= current_score < high):
        return False

    tuned = tune_cell(gray, centre, confirmed_slug, library, cfg)
    if tuned is None or tuned.margin <= current_margin:
        # Never store a result that is not an improvement on what we already had.
        return False

    store.learn_transform(
        audit_id, screen_slug, confirmed_slug, art_ref,
        tuned.scale, tuned.score, tuned.margin,
        crop=(tuned.crop_half_w, tuned.crop_top, tuned.crop_bottom),
    )
    return True


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


def confirmed_sides(slots) -> dict[str, set[str]]:
    """Which heroes per side are OCR-CONFIRMED, for use as cross-screen ground truth.

    Deliberately not "which heroes were recorded": HeroSlot.hero_slug is
    `confirmed or hero.slug`, so it is populated even when the long-press failed. Seeding
    the confirmation set from it would launder an unconfirmed image guess into ground
    truth and let it authorise learning on the draft and prematch screens - the exact
    self-confirmation this design exists to prevent.
    """
    confirmed: dict[str, set[str]] = {"blue": set(), "red": set()}
    for slot in slots:
        if slot.hero_slug and slot.identified_by == "longpress_ocr":
            confirmed.setdefault(slot.side, set()).add(slot.hero_slug)
    return confirmed


def train_from_frame(
    *,
    store,
    cfg: SolsticeConfig,
    library: IconLibrary,
    frame: np.ndarray,
    screen_slug: str,
    cell_type: str,
    confirmed_by_side: dict[str, set[str]],
    frame_path: str,
    match_id: int | None,
) -> int:
    """Score one training frame against the summary's confirmed identities.

    Confirmation here is at SIDE-SET level, not per slot. The summary lists three heroes
    per side but does NOT state which draft pick slot each came from, and that mapping has
    never been verified - Blue's picks are slots 1, 4 and 5 while the summary shows a plain
    list of three. Asserting a positional correspondence we have not measured would
    manufacture false disagreements.

    So a read counts as confirmed when the identified hero is in that side's confirmed set
    AND no other cell on the same side claimed the same hero. Uniqueness is what pins the
    cell-to-hero mapping; set membership alone would let two cells both claim one hero.

    Returns:
        The number of audit rows written.
    """
    from .vision import extract_cell, identify_cell

    reads: list[tuple] = []
    for cell in cfg.cells(cell_type):
        result = identify_cell(extract_cell(frame, cell), cell_type, library, cfg)
        reads.append((cell, result))

    written = 0
    for cell, result in reads:
        side = cell.side or ""
        confirmed = confirmed_by_side.get(side, set())
        unique = (
            result.slug is not None
            and sum(
                1
                for other_cell, other in reads
                if (other_cell.side or "") == side and other.slug == result.slug
            )
            == 1
        )
        ocr_slug = result.slug if (result.slug in confirmed and unique) else None

        audit_id = store.record_audit(
            AuditRow(
                screen_slug=screen_slug,
                side=side,
                slot=cell.slot or 0,
                image_slug=result.slug,
                image_art_ref=result.art_ref,
                image_score=result.score,
                image_margin=result.margin,
                ocr_slug=ocr_slug,
                # Training frames are ALWAYS archived, agreements included: by the time a
                # draft read is found wrong the screen is minutes gone and unrecoverable.
                frame_path=frame_path,
                match_id=match_id,
            )
        )
        written += 1

        learn_if_improved(
            store=store, cfg=cfg, library=library,
            gray=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
            centre=((cell.x0 + cell.x1) // 2, (cell.y0 + cell.y1) // 2),
            screen_slug=screen_slug, image_slug=result.slug, confirmed_slug=ocr_slug,
            art_ref=result.art_ref or (ocr_slug or ""),
            current_score=result.score, current_margin=result.margin, audit_id=audit_id,
        )
    return written
