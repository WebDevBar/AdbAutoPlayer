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
    """What one frame says about one stored row.

    There is deliberately no `no_row` member. A frame whose match id is absent from the
    database is dropped by the audit's work list rather than classified: it has no
    stored orientation to disagree with, and giving it a verdict would put rows nobody
    audited into the denominator of every rate in the report.
    """

    AGREE = "agree"
    MIRRORED = "mirrored"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"
    INCOMPLETE = "incomplete"


def classify(
    frame_blue: frozenset[str],
    frame_red: frozenset[str],
    row_left: frozenset[str],
    row_right: frozenset[str],
) -> Verdict:
    """Does the frame's orientation agree with the row's?

    THE DATABASE IS THE RECORD; the frame is only evidence about ORIENTATION. No single
    draft frame holds all six picks, so this never tries to rebuild a match from one.

    A draft frame carries FIVE picks, by the game's design and not by mistake. The snake
    order is 1 Blue, 2 Red, 3 Red, 4 Blue, 5 Blue, 6 Red, and the game leaves the draft
    screen the instant pick 6 is made - it is read from the LOCKED screen instead
    (`solstice_clash.py:1448` records a live run that lost it by leaving early;
    `2026-07-27-solstice-clash-odds-design.md:178` states betting closes before it locks).

    So blue is ALWAYS complete - slots 1, 4 and 5 all precede 6 - and red is ALWAYS
    missing exactly one. Requiring three and three would be unsatisfiable by
    construction, and an earlier version of this function did exactly that: it returned
    `unreadable` for all 916 frames and adjudicated nothing.

    The complete blue trio settles orientation on its own; red is checked as a SUBSET,
    which is corroboration rather than the deciding test.

    Args:
        frame_blue: Blue picks read from the draft frame. Expected to be complete.
        frame_red: Red picks read from the draft frame. Expected to be one short.
        row_left: The row's `side='left'` slugs, from the database.
        row_right: The row's `side='right'` slugs, from the database.

    Returns:
        The verdict for this row.
    """
    if len(frame_blue) != TRIO_SIZE or len(frame_red) < TRIO_SIZE - 1:
        return Verdict.UNREADABLE
    if len(row_left) != TRIO_SIZE or len(row_right) != TRIO_SIZE:
        return Verdict.INCOMPLETE
    if frame_blue == row_left and frame_red <= row_right:
        return Verdict.AGREE
    if frame_blue == row_right and frame_red <= row_left:
        return Verdict.MIRRORED
    return Verdict.PARTIAL
