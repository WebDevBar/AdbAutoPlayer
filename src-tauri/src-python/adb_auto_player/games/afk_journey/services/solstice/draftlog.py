"""Live draft pick logging - pure, no device, no GUI.

Phase 1 of Mode C. It shows nothing predictive: it only says which hero it believes
just locked, in draft order, as it happens. That is deliberately the smallest thing
that proves the input the odds model will depend on, and it doubles as the measurement
Task 7 asks for - a wrong name in the log is a wrong name in the model.

Two cell geometries exist for the same six on-screen positions, registered a day apart
and offset by about 20px:

    draft / draft_locked_pick        (2026-07-25)  y 428-502
    spectate_draft_picks / draft_pick (2026-07-26)  y 410-495

They cannot both be right. The audit rows collected so far pass the accept rule on only
39% of `draft_pick` reads against 100% on the prematch and summary screens, so this
module reads BOTH and reports which one answered. Picking the winner is then a fact
about collected data rather than a guess about which registration was more careful.
"""

from __future__ import annotations

from dataclasses import dataclass

# The log mirrors the screen: the game labels the teams Blue and Red, and the person
# reading this is watching that screen. `left`/`right` remain the stored values
# everywhere else - a colour is an observation channel, and the model must never key on
# one - but a log that says `left` when the screen says Blue is harder to check, not
# easier. The slot number rides along in the evidence bracket so a line can still be
# traced back to a specific cell.
SIDE_LABELS = {"left": "Blue", "right": "Red"}

# 1 left, 2 right, 3 right, 4 left, 5 left, 6 right - a snake draft. The registry
# carries this per cell; it is repeated here only so the log can be ordered before any
# cell has been read.
DRAFT_ORDER = (1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class PickRead:
    """One cell, as read on one poll."""

    slot: int
    side: str
    cell_type: str
    slug: str | None
    name: str | None
    score: float
    margin: float

    @property
    def identified(self) -> bool:
        return self.slug is not None


def better(a: PickRead | None, b: PickRead | None) -> PickRead | None:
    """The read to trust when two geometries disagree about the same slot.

    An identified read always beats an unidentified one, whatever the scores: the
    accept rule has already applied both thresholds, so a read that failed it is not
    'weaker evidence', it is no evidence. Between two identified reads, higher score
    wins; ties go to the larger margin, because margin is what separates a confident
    match from two heroes that look alike.
    """
    if a is None or not a.identified:
        return b if (b is not None and b.identified) else (a or b)
    if b is None or not b.identified:
        return a
    if (a.score, a.margin) >= (b.score, b.margin):
        return a
    return b


def newly_locked(seen: dict[int, str], reads: list[PickRead]) -> list[PickRead]:
    """Picks not yet logged, in draft order. Mutates `seen`.

    Joining mid-draft is the normal case, not an edge case: everything already on
    screen comes back on the first poll, in slot order, which is the order it was
    picked in. A slot that changes its mind - a re-read that lands on a different hero -
    is logged again, because a silent correction would leave the log disagreeing with
    what the model was fed.
    """
    fresh: list[PickRead] = []
    for read in sorted(reads, key=lambda r: r.slot):
        if not read.identified:
            continue
        assert read.slug is not None
        if seen.get(read.slot) == read.slug:
            continue
        seen[read.slot] = read.slug
        fresh.append(read)
    return fresh


def format_pick(read: PickRead) -> str:
    """One log line: who picked what, and how sure the read is.

    `[score/margin]`. Score is how well the icon matched, 0 to 1. Margin is the gap to
    the runner-up, which is the number that catches lookalikes: two similar heroes can
    both score 0.9, and only the margin says whether the top answer was actually
    distinguishable. Both are kept because the accept rule applies a threshold to each,
    and a line that says only 'picked: Lorsan' cannot be audited afterwards.

    Which of the two geometries produced the read is NOT in the line - it is counted
    and reported in the heartbeat, so per-pick noise stays out of the log.
    """
    label = SIDE_LABELS.get(read.side, read.side)
    shown = read.name or read.slug or "?"
    return f"{label} picked: {shown} [{read.score:.3f}/{read.margin:.3f}]"


def format_final(reads: list[PickRead]) -> str:
    """The locked screen, as one line per side, in draft order."""
    by_side: dict[str, list[str]] = {"left": [], "right": []}
    for read in sorted(reads, key=lambda r: r.slot):
        if read.side in by_side:
            by_side[read.side].append(read.name or read.slug or "?")
    left = ", ".join(by_side["left"]) or "-"
    right = ", ".join(by_side["right"]) or "-"
    return f"locked - {SIDE_LABELS['left']}: {left} | {SIDE_LABELS['right']}: {right}"
