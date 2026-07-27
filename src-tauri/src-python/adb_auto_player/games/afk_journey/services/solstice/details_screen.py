"""Is this frame a post-match details screen, and is it the right event's?

Pure, stateless, and free of any recording policy - Mode A wants this check
without Mode B's deduplication, and a predicate that could reach the device
would be unusable in a mode that must never touch it.

Up to three signals, because one template is a single point of failure: a game
update that restyles it would silently stop collection.

  1. the Replay control, bottom-right
  2. an "Ally" or "Enemy" roster tab
  3. OPTIONAL - the header title, when the caller names one

Signal 3 is what separates "a details screen" from "THIS event's details
screen". Signals 1 and 2 are shared by any 3v3 post-battle screen the game may
have, so a mode that watches passively while the user plays anything at all can
otherwise record an Arena or Dream Realm match as Solstice Clash data and push
it into the shared pool. Callers that already know where they are - Mode A
navigated to the event itself - pass nothing and skip the check.
"""

import re
from pathlib import Path
from typing import NamedTuple

import numpy as np

from adb_auto_player.image_manipulation import IO
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.ocr import OCRBackend
from adb_auto_player.template_matching import TemplateMatcher

REPLAY_TEMPLATE = Path("event/solstice_clash/details_replay.png")
# Measured 1.000 on every details screen and <= 0.643 on fifteen others, so the
# default 90% sits comfortably inside the gap.
REPLAY_THRESHOLD = ConfidenceValue("90%")

# The roster tab strip: both tabs, below the player-name header and left of the
# stat columns, so no other text is in frame. Absolute pixels on 1080x1920,
# matching how summary.py already addresses its OCR regions.
TAB_STRIP = (0, 350, 220, 1730)  # x0, y0, x1, y1
TAB_LABELS = frozenset({"ally", "enemy"})

# The header band holding the event title. Measured on real frames: the title
# sits at y=100 and the player names at y=252, so this band takes the former and
# excludes the latter. Wider bands (0-220, 20-200, 40-180) all split the title
# into two blocks mid-word; 60-160 reads it as one. The join below survives a
# split anyway, but a clean read is worth having.
HEADER_BAND = (0, 60, 1080, 160)  # x0, y0, x1, y1

SOLSTICE_CLASH_TITLE = "Solstice Clash"


class DetailsSignals(NamedTuple):
    """Which detection signals fired.

    Reported separately so a caller can notice them DISAGREEING. They are
    combined with AND, which means a game update that restyles the Replay button
    stops collection silently - and so does one that breaks the label read. The
    redundancy this set is supposed to provide only exists if something watches
    for one firing without the others.

    `header` is None when the caller named no title, which is not the same as
    False - "not checked" must never read as "checked and wrong".
    """

    template: bool
    labels: bool
    header: bool | None = None

    @property
    def confirmed(self) -> bool:
        return self.template and self.labels and self.header is not False


def load_replay_template(template_dir: Path) -> np.ndarray:
    """Resolve the template path once, at mode start.

    IO.load_image caches globally, so this is not a performance measure - it is
    what keeps the predicate free of any path or directory knowledge, which is
    what makes it testable without a Game.
    """
    return IO.load_image(template_dir / REPLAY_TEMPLATE)


def _normalise(text: str) -> str:
    """Casefold and collapse whitespace. Nothing fuzzier than that."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _read_header_title(
    frame: np.ndarray, ocr: OCRBackend, expected: str
) -> bool:
    """Does the header band read as `expected`?

    Matched against each block AND against every block joined, because OCR
    splits a title mid-word depending on where the crop lands - 'Solstice Clash'
    came back as ['Solstice', 'e Clash'] from a band 20 pixels taller. Both forms
    are compared EXACTLY after normalising; no substring test, which would let a
    longer title containing this one pass.
    """
    x0, y0, x1, y1 = HEADER_BAND
    blocks = [b.text for b in ocr.detect_text_blocks(frame[y0:y1, x0:x1])]
    if not blocks:
        return False

    want = _normalise(expected)
    if any(_normalise(b) == want for b in blocks):
        return True
    return _normalise(" ".join(blocks)) == want


def details_signals(
    frame: np.ndarray,
    replay_template: np.ndarray,
    ocr: OCRBackend,
    header_title: str | None = None,
) -> DetailsSignals:
    """Evaluate every signal independently, without short-circuiting.

    is_details_screen could stop at a failed template match, but then a caller
    could never tell "not the details screen" apart from "the template broke".
    """
    template = (
        TemplateMatcher.find_template_match(
            base_image=frame,
            template_image=replay_template,
            threshold=REPLAY_THRESHOLD,
        )
        is not None
    )

    x0, y0, x1, y1 = TAB_STRIP
    blocks = ocr.detect_text_blocks(frame[y0:y1, x0:x1])
    # EXACT match on a whole block. A substring test accepts "Really" and
    # "Rally"; "All In" sits on the betting screen two characters away.
    #
    # Either label is enough - it is OR, not AND, and position is irrelevant.
    # Which tab reads "Ally" depends on who is watching, so the labels carry no
    # information about sides and nothing may read them for that purpose.
    labels = any(b.text.strip().casefold() in TAB_LABELS for b in blocks)

    header = (
        None
        if header_title is None
        else _read_header_title(frame, ocr, header_title)
    )

    return DetailsSignals(template=template, labels=labels, header=header)


def is_details_screen(
    frame: np.ndarray,
    replay_template: np.ndarray,
    ocr: OCRBackend,
    header_title: str | None = None,
) -> bool:
    """Every signal must agree.

    Pass `header_title` to also require that title in the header band - use it
    whenever the caller does not already know which screen it is looking at.
    Omit it to accept any details screen. See DetailsSignals for why the signals
    are also reported individually.
    """
    return details_signals(
        frame, replay_template, ocr, header_title
    ).confirmed
