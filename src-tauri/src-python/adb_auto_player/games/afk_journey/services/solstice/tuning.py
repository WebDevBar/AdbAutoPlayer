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
DEFAULT_SCALES: tuple[float, ...] = tuple(round(0.30 + 0.01 * i, 3) for i in range(56))


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
        audit_id,
        screen_slug,
        confirmed_slug,
        art_ref,
        tuned.scale,
        tuned.score,
        tuned.margin,
        crop=(tuned.crop_half_w, tuned.crop_top, tuned.crop_bottom),
    )
    return True


def _best_scale_for(
    icon: np.ndarray, cell: np.ndarray, scales: tuple[float, ...]
) -> float:
    """The single scale at which this icon matches this cell best.

    Storing it collapses the scale chain from ~56 steps to 1 on later sightings.
    """
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
    # Keyed "left"/"right" because that is what the CELL GEOMETRY calls its two
    # halves - this is about where on the summary screen a card sat, not about which
    # composition it belonged to. `HeroSlot.trio` is the composition; trio 1 occupies
    # the cells this code has always called "left".
    confirmed: dict[str, set[str]] = {"left": set(), "right": set()}
    for slot in slots:
        if slot.hero_slug and slot.identified_by == "longpress_ocr":
            half = "left" if slot.trio == 1 else "right"
            confirmed.setdefault(half, set()).add(slot.hero_slug)
    return confirmed


@dataclass(frozen=True)
class TrainResult:
    """What one training frame produced. All three counters are for LOGGING only.

    written: audit rows recorded.
    deduced: cells whose true hero was pinned by elimination (see train_from_frame) and
        recorded as a disagreement, never as a confirmation.
    set_consistent: reads that were in that side's confirmed set AND unique among that
        side's reads. A plausibility measurement, not confirmation - a swap of two
        same-side heroes is also set-consistent (see train_from_frame's docstring).
    """

    written: int
    deduced: int
    set_consistent: int


def train_from_frame(
    *,
    store,
    cfg: SolsticeConfig,
    library: IconLibrary,
    frame: np.ndarray,
    screen_slug: str,
    cell_type: str,
    confirmed_by_side: dict[str, set[str]],
    frame_path: str = "",
    # Kept as a parameter, always empty, because the column still exists. Frames
    # are no longer archived: nothing ever read one back. train_from_frame takes
    # the frame IN MEMORY (`frame: np.ndarray` above) and learn_if_improved takes
    # `gray`, so learning never needed the file - the archive was a write-only
    # side-record costing ~345MB/day, 72% of it byte-identical duplicates.
    match_id: int | None,
) -> TrainResult:
    """Record one training frame's reads against the summary's confirmed identities.

    This screen has NO per-cell ground truth. The summary lists three heroes per side but
    does NOT state which draft pick slot each came from, and that mapping has never been
    verified - Blue's picks are slots 1, 4 and 5 while the summary shows a plain list of
    three.

    Set membership plus uniqueness (no other cell on the same side claiming the same hero)
    only tells us a read is PLAUSIBLE - consistent with the confirmed set - it cannot tell
    a correct read apart from a SWAP: if the image matcher mis-assigns two heroes that are
    both genuinely on that side, cell 1 reads B when it is really A and cell 2 reads A when
    it is really B, each read is still in the confirmed set and still unique on that side.
    A swap is still a bijection with the set, so no amount of extra set logic closes this
    gap - such rows record ocr_slug=None, never a confirmation.

    The ONE case with genuine per-cell truth is elimination: if exactly one cell on a side
    reads something NOT in that side's confirmed set, and exactly one confirmed hero is
    unaccounted for by every other cell's read on that side, then that hero must be the
    odd cell's true content - there is nowhere else for it to be. That row's ocr_slug is
    set to the deduced hero. By construction this always differs from the image read (the
    read was, after all, not in the set), so record_audit always writes agreed=0 for it -
    intentional: this exists to be counted and analysed, never to teach the matcher a
    transform. learn_if_improved is deliberately never called from here; it stays available
    only where real per-cell truth exists, the long-press-confirmed summary screen, via the
    call in the mixin's summary loop.

    ASSUMPTION, stated plainly: elimination is sound only when AT MOST ONE read per side is
    wrong. Two simultaneous errors can pin the WRONG hero - e.g. true C/B/A read as A/B/X
    would conclude the odd cell is C when it is actually A. This is not detectable from a
    single frame; it is a known limitation of the deduction, not a bug.

    Returns:
        A TrainResult with written/deduced/set_consistent counts, for logging only.
    """
    from .vision import extract_cell, identify_cell

    reads: list[tuple] = []
    for cell in cfg.cells(cell_type):
        result = identify_cell(extract_cell(frame, cell), cell_type, library, cfg)
        reads.append((cell, result))

    # Per-cell deduced truth, computed once per side before the recording pass below.
    deduced_ocr_slug: dict[int, str] = {}  # keyed by index into `reads`
    sides_present = {(cell.side or "") for cell, _ in reads}
    for side in sides_present:
        confirmed = confirmed_by_side.get(side, set())
        side_reads = [
            (i, result)
            for i, (cell, result) in enumerate(reads)
            if (cell.side or "") == side
        ]
        odd_out = [
            (i, result) for i, result in side_reads if result.slug not in confirmed
        ]
        if len(odd_out) != 1:
            continue  # zero or multiple reads outside the set: nothing is pinned
        odd_i, _odd_result = odd_out[0]
        accounted = {
            result.slug
            for i, result in side_reads
            if i != odd_i and result.slug is not None
        }
        unaccounted = confirmed - accounted
        if len(unaccounted) == 1:
            deduced_ocr_slug[odd_i] = next(iter(unaccounted))

    written = 0
    deduced_count = 0
    set_consistent = 0
    for i, (cell, result) in enumerate(reads):
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
        if result.slug is not None and result.slug in confirmed and unique:
            set_consistent += 1

        ocr_slug = deduced_ocr_slug.get(i)
        if ocr_slug is not None:
            deduced_count += 1

        store.record_audit(
            AuditRow(
                screen_slug=screen_slug,
                side=side,
                slot=cell.slot or 0,
                image_slug=result.slug,
                image_art_ref=result.art_ref,
                image_score=result.score,
                image_margin=result.margin,
                ocr_slug=ocr_slug,
                # Training frames are ALWAYS archived: by the time a draft read is found
                # wrong the screen is minutes gone and unrecoverable.
                frame_path=frame_path,
                match_id=match_id,
            )
        )
        written += 1

    return TrainResult(
        written=written, deduced=deduced_count, set_consistent=set_consistent
    )
