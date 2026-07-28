"""Which Solstice Clash screen is on display - pure, no device, no GUI.

Replaces the single `draft_anchor` template, which was the "Current Theme:" label: a
small text crop that any overlay or animation can obscure for a frame. Read as
"the draft is over" on one bad frame, it ended the watch about ten seconds early and
capped every live run at four of six picks.

Two independent signals per screen, the same shape `details_screen.py` uses:

- **Which screen** is decided by the VS art, because it is the one thing that differs.
  During the draft it is gold on a dark rotated square; once the picks lock it is white
  on a flame burst. Measured over 154 captured frames: 35 matched the draft VS, 20
  matched the locked VS, and NOTHING matched both.
- **That we are on a Solstice betting screen at all** is corroborated by the All In
  tiles. They cannot tell the two screens apart - they sit at IDENTICAL coordinates on
  both - so they are never used for that. They exist to make a false positive need two
  independent things to go wrong at once.

The crops deliberately exclude the near-white panel behind the tiles: animated confetti
drifts across it, and every white pixel included is a pixel that changes between frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from adb_auto_player.image_manipulation import IO
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.template_matching import TemplateMatcher

_DIR = Path("event") / "solstice_clash"
VS_DRAFT = _DIR / "vs_draft.png"
VS_LOCKED = _DIR / "vs_locked.png"
ALLIN_LEFT = _DIR / "allin_left.png"
ALLIN_RIGHT = _DIR / "allin_right.png"

# Measured, not guessed. On the captured frames the correct VS scores 0.979-1.000 and
# the wrong one never exceeds 0.483, so anywhere in between separates them; 0.90 leaves
# room for compression noise without approaching the miss band.
VS_THRESHOLD = ConfidenceValue(0.90)
# The tiles are unobstructed and score 1.000 when present, but one can be covered by the
# chat widget or an emoji, so either alone is enough.
ALLIN_THRESHOLD = ConfidenceValue(0.90)


@dataclass(frozen=True)
class Templates:
    """Loaded once at mode start, so the predicate knows nothing about paths."""

    vs_draft: np.ndarray
    vs_locked: np.ndarray
    allin_left: np.ndarray
    allin_right: np.ndarray


def load_templates(template_dir: Path) -> Templates:
    """Resolve every screen template. `IO.load_image` caches globally."""
    return Templates(
        vs_draft=IO.load_image(template_dir / VS_DRAFT),
        vs_locked=IO.load_image(template_dir / VS_LOCKED),
        allin_left=IO.load_image(template_dir / ALLIN_LEFT),
        allin_right=IO.load_image(template_dir / ALLIN_RIGHT),
    )


def _matches(frame: np.ndarray, template: np.ndarray, threshold) -> bool:
    return (
        TemplateMatcher.find_template_match(
            base_image=frame, template_image=template, threshold=threshold
        )
        is not None
    )


def on_betting_screen(frame: np.ndarray, templates: Templates) -> bool:
    """Either All In tile. OR, not AND: one can be covered by the chat widget."""
    return _matches(frame, templates.allin_left, ALLIN_THRESHOLD) or _matches(
        frame, templates.allin_right, ALLIN_THRESHOLD
    )


def is_draft_screen(frame: np.ndarray, templates: Templates) -> bool:
    """Picks are still landing and betting is open."""
    return _matches(frame, templates.vs_draft, VS_THRESHOLD) and on_betting_screen(
        frame, templates
    )


def is_locked_screen(frame: np.ndarray, templates: Templates) -> bool:
    """The draft is over and the six picks are shown at rest."""
    return _matches(frame, templates.vs_locked, VS_THRESHOLD) and on_betting_screen(
        frame, templates
    )
