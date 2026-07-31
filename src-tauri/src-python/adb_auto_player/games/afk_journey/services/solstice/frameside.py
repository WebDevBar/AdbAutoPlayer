"""Reading the two hero trios off a DRAFT frame.

The draft screen is the only screen that carries true blue/red plates, which is what
makes it able to adjudicate a summary read. Pure: no device, no database, so it can be
run against saved frames.
"""

from enum import StrEnum

import numpy as np

from .config import SolsticeConfig
from .icons import IconLibrary
from .vision import extract_cell, identify_cell

# The draft screen numbers its cells 1-6 ACROSS both teams and carries the side on the
# cell itself, so grouping is by `cell.side`. Never infer a side from slot arithmetic -
# the per-side screens number 1-3 within a side and the two schemes do not agree.
DRAFT_CELL_TYPE = "draft_pick"
TRIO_SIZE = 3


def read_frame_sides(
    frame: np.ndarray,
    cfg: SolsticeConfig,
    library: IconLibrary,
) -> tuple[frozenset[str], frozenset[str]]:
    """The blue and red hero slugs on one draft frame.

    Args:
        frame: BGR frame, 1080x1920.
        cfg: Solstice config, for the cell geometry.
        library: Icon library to identify against.

    Returns:
        `(blue_slugs, red_slugs)`. Unidentified cells are omitted rather than guessed,
        so a caller can tell a partial read from a complete one by set size.
    """
    sides: dict[str, set[str]] = {"left": set(), "right": set()}
    for cell in cfg.cells(DRAFT_CELL_TYPE):
        if cell.side not in sides:
            continue
        result = identify_cell(extract_cell(frame, cell), DRAFT_CELL_TYPE, library, cfg)
        if result.slug is not None:
            sides[cell.side].add(result.slug)
    return frozenset(sides["left"]), frozenset(sides["right"])


class Verdict(StrEnum):
    """What one frame says about one stored row."""

    AGREE = "agree"
    MIRRORED = "mirrored"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"
    INCOMPLETE = "incomplete"
    NO_ROW = "no_row"


def classify(
    frame_blue: frozenset[str],
    frame_red: frozenset[str],
    row_left: frozenset[str],
    row_right: frozenset[str],
) -> Verdict:
    """Compare a frame's trios against a row's, as SETS, ignoring slot.

    Order of checks matters. The frame is judged first: if we could not read it, we
    have no basis to say anything about the row, and calling that `partial` would put
    a reader failure into the evidence for a summary-reader defect.

    Args:
        frame_blue: Blue trio read from the draft frame.
        frame_red: Red trio read from the draft frame.
        row_left: The row's `side='left'` slugs.
        row_right: The row's `side='right'` slugs.

    Returns:
        The verdict for this row.
    """
    if len(frame_blue) != TRIO_SIZE or len(frame_red) != TRIO_SIZE:
        return Verdict.UNREADABLE
    if len(row_left) != TRIO_SIZE or len(row_right) != TRIO_SIZE:
        return Verdict.INCOMPLETE
    if frame_blue == row_left and frame_red == row_right:
        return Verdict.AGREE
    if frame_blue == row_right and frame_red == row_left:
        return Verdict.MIRRORED
    return Verdict.PARTIAL
