"""Live draft pick logging - pure, no device, no GUI.

Phase 1 of Mode C. It shows nothing predictive: it only says which hero it believes
just locked, in draft order, as it happens. That is deliberately the smallest thing
that proves the input the odds model will depend on, and it doubles as the measurement
Task 7 asks for - a wrong name in the log is a wrong name in the model.

The registered geometries belong to DIFFERENT screens, which is not what it looked
like at first - they cover the same six positions about 20px apart, so they read like
rival registrations of one screen. Measured on the committed fixtures:

    spectate_draft.png     draft_pick 3/6 (best 0.98)   draft_locked_pick 1/6 (0.80)
    spectate_prematch.png  prematch_pick 6/6 (0.99)     locked_pick 2/6 (0.80)
    prematch_locked.png    locked_pick 6/6 (0.99)       prematch_pick 0/6 (0.88)

Reading the wrong geometry for a screen does not fail cleanly - it returns confident
nonsense, which is worse than nothing because nonsense reaches the model. Mode C
spectates, so it reads the spectate geometries and nothing else.

The 39% accept rate on collected `draft_pick` audit rows is therefore not evidence of
bad geometry: a draft frame is captured while picks are still landing, and an empty
slot cannot identify. Three of six identified on a mid-draft frame is the expected
shape, not a defect.
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
# Three heroes a side, always.
MAX_PICKS_PER_SIDE = 3


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


def side_positions(reads: list[PickRead]) -> dict[tuple[str, int], PickRead]:
    """Key reads by side and position within that side.

    The two screens number their cells differently and merging on the raw slot silently
    mixes the sides: the draft numbers 1-6 across both teams in snake order (1 left,
    2 right, 3 right, 4 left, 5 left, 6 right) while the locked screen numbers 1-3 per
    side. Keyed on slot alone, draft slot 2 (a red pick) lands on top of locked left-1,
    and a real run printed four heroes on Blue and two on Red with one lost entirely.

    Position within a side is the one thing both schemes agree on.
    """
    keyed: dict[tuple[str, int], PickRead] = {}
    for side in ("left", "right"):
        ordered = sorted((r for r in reads if r.side == side), key=lambda r: r.slot)
        for position, read in enumerate(ordered, start=1):
            keyed[(side, position)] = read
    return keyed


def merge_screens(draft: list[PickRead], locked: list[PickRead]) -> list[PickRead]:
    """The three heroes each side ended up with, from both screens.

    A UNION per side, not an alignment by position. Two earlier attempts failed here:
    merging on the raw slot mixed the sides, because the draft numbers 1-6 across both
    teams and the locked screen numbers 1-3 within each; merging on position within a
    side then printed `Red: Lorsan, Lorsan` on a live run, because the screens do not
    order a side's picks identically either.

    A side is a set of three distinct heroes, and that is the only structure both
    screens agree on. The locked screen leads because it sees the finished draft; the
    draft fills what the locked read missed. Duplicates are impossible by construction:
    a slug already present is never added twice.
    """
    out: list[PickRead] = []
    for side in ("left", "right"):
        chosen: dict[str, PickRead] = {}
        for read in [r for r in locked if r.side == side] + [
            r for r in draft if r.side == side
        ]:
            if not read.identified or read.slug in chosen:
                continue
            chosen[read.slug] = read
        out.extend(chosen.values())
        # Keep the unread positions visible rather than silently shortening the side.
        missing = MAX_PICKS_PER_SIDE - len(chosen)
        out.extend(
            PickRead(
                slot=0,
                side=side,
                cell_type="none",
                slug=None,
                name=None,
                score=0.0,
                margin=0.0,
            )
            for _ in range(max(0, missing))
        )
    return out


def format_merged(reads: list[PickRead]) -> str:
    """The locked six, three per side, unknowns shown as `?`."""
    by_side: dict[str, list[str]] = {"left": [], "right": []}
    for read in reads:
        if read.side in by_side:
            by_side[read.side].append(read.name or read.slug or "?")
    left = ", ".join(by_side["left"]) or "-"
    right = ", ".join(by_side["right"]) or "-"
    return f"locked - {SIDE_LABELS['left']}: {left} | {SIDE_LABELS['right']}: {right}"
