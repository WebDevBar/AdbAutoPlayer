"""Which summary panel is the draft's blue side?

The summary screen numbers its two hero panels top and bottom. Nothing on that screen
says which is the draft's blue - the panel order is viewer-relative and mirrors the draft
in about 8% of matches, measured across 922 audited frames. Reading it as geometry stored
the wrong side, and because `predicted_left` is draft-relative while the outcome came
from the panels, 76 stored HIT/MISS values were inverted.

The only facts on that screen worth trusting are the two TRIOS and which one won. So the
panels are matched against the trios the draft already gave us, as sorted sets, and every
side label is derived from that match rather than from where anything sat on screen.
"""

from dataclasses import dataclass
from enum import StrEnum

# Both panels are scored together, not just the winner. The draft screen never shows the
# sixth pick, so a draft-frame red side is only ever two heroes - true of all 922 audited
# frames. Matching the winning panel alone then gives 1-vs-1 on a single misread, which is
# a tie and unresolvable. Scoring both panels gives 5-vs-0 clean and still 4-vs-1 with one
# misread anywhere.
MIN_MARGIN = 2


class Orientation(StrEnum):
    """How the summary panels map onto the draft's sides."""

    DIRECT = "direct"  # top panel is blue
    SWAPPED = "swapped"  # top panel is red
    UNRESOLVED = "unresolved"  # refuse rather than guess


@dataclass(frozen=True)
class Resolution:
    """The mapping, and how strongly the evidence supported it."""

    orientation: Orientation
    margin: int
    reason: str

    @property
    def resolved(self) -> bool:
        return self.orientation is not Orientation.UNRESOLVED


def resolve(
    panel_top: frozenset[str],
    panel_bottom: frozenset[str],
    draft_blue: frozenset[str],
    draft_red: frozenset[str],
) -> Resolution:
    """Decide which summary panel holds the draft's blue trio.

    Scores both hypotheses over BOTH panels and requires a clear win. A margin rule is
    what makes a single misidentified hero survivable and two misidentifications a
    refusal, rather than letting one bad cell decide a side.

    Args:
        panel_top: Hero slugs read from the summary's top panel.
        panel_bottom: Hero slugs read from the summary's bottom panel.
        draft_blue: The draft's blue trio, ideally merged draft+locked so it is complete.
        draft_red: The draft's red trio.

    Returns:
        The resolution. `UNRESOLVED` whenever the evidence is thin, tied, or contradictory
        - the caller must then record the match without an outcome rather than guess.
    """
    if not draft_blue or not draft_red or not panel_top or not panel_bottom:
        return Resolution(Orientation.UNRESOLVED, 0, "a trio was empty")

    direct = len(panel_top & draft_blue) + len(panel_bottom & draft_red)
    swapped = len(panel_top & draft_red) + len(panel_bottom & draft_blue)
    margin = abs(direct - swapped)

    if margin < MIN_MARGIN:
        return Resolution(
            Orientation.UNRESOLVED,
            margin,
            f"direct {direct} vs swapped {swapped}, margin below {MIN_MARGIN}",
        )
    if direct > swapped:
        return Resolution(Orientation.DIRECT, margin, f"direct {direct} vs {swapped}")
    return Resolution(Orientation.SWAPPED, margin, f"swapped {swapped} vs {direct}")


def side_for_panel(panel: str, orientation: Orientation) -> str:
    """The draft side a summary panel belongs to.

    Args:
        panel: "top" or "bottom".
        orientation: A resolved orientation. Never call this with UNRESOLVED.

    Returns:
        "left" for the draft's blue, "right" for its red.

    Raises:
        ValueError: If the orientation is unresolved, which is a caller bug - an
            unresolved match must be recorded without sides, not guessed at.
    """
    if orientation is Orientation.UNRESOLVED:
        raise ValueError("cannot place a panel with an unresolved orientation")
    if orientation is Orientation.DIRECT:
        return "left" if panel == "top" else "right"
    return "right" if panel == "top" else "left"
