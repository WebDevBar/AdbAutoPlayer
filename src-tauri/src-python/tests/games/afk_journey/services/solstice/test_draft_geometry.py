"""Which geometry belongs to which screen, measured on real frames.

Written after Mode C's first live run logged nothing. Two mistakes were in play: the
draft was gated on `classify_screen`, whose fixed-position anchor matches the draft you
play yourself rather than the one you spectate, and the six pick cells were read with
both registered geometries on the assumption that one of them had to be wrong.

Neither assumption survived contact with the fixtures. The geometries belong to
different screens, and reading the wrong one on a screen returns confident nonsense
rather than nothing - which is worse, because nonsense reaches the model.
"""

from pathlib import Path

import cv2
import pytest
from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig
from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary
from adb_auto_player.games.afk_journey.services.solstice.paths import (
    solstice_db_path,
    solstice_icon_dir,
)
from adb_auto_player.games.afk_journey.services.solstice.vision import (
    classify_screen,
    extract_cell,
    identify_cell,
)

DATA = Path(__file__).parent / "data"
ANCHORS = (
    Path(__file__).resolve().parents[5]
    / "adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors"
)
DRAFT_TEMPLATE = ANCHORS.parent / "draft_anchor.png"


@pytest.fixture(scope="module")
def library():
    cfg = SolsticeConfig.load(solstice_db_path())
    return cfg, IconLibrary.build(cfg, solstice_icon_dir())


def _identified(frame, cell_type, cfg, lib) -> int:
    return sum(
        identify_cell(extract_cell(frame, cell), cell_type, lib, cfg).status
        == "identified"
        for cell in cfg.cells(cell_type)
    )


def test_the_spectate_draft_is_not_what_classify_screen_detects():
    """The regression that made Mode C log nothing on its first live run."""
    frame = cv2.imread(str(DATA / "spectate_draft.png"))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    template = cv2.imread(str(DRAFT_TEMPLATE), cv2.IMREAD_GRAYSCALE)

    assert classify_screen(frame, ANCHORS) == "unknown", (
        "if this ever returns 'draft', re-check which detector Mode C should gate on"
    )
    best = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED).max()
    assert best > 0.95, f"draft_anchor template scored only {best:.3f}"


def test_the_players_own_draft_is_the_one_the_anchor_matches():
    """The mirror image, so the two screens can never be conflated again."""
    assert classify_screen(cv2.imread(str(DATA / "draft_selecting.png")), ANCHORS) == (
        "draft"
    )


def test_the_spectate_draft_reads_with_the_spectate_geometry(library):
    cfg, lib = library
    frame = cv2.imread(str(DATA / "spectate_draft.png"))
    assert _identified(frame, "draft_pick", cfg, lib) > _identified(
        frame, "draft_locked_pick", cfg, lib
    )


def test_the_spectate_prematch_reads_with_its_own_geometry(library):
    """6/6 at 0.99 - this screen is the reliable one, and it is what the locked line
    is read from."""
    cfg, lib = library
    frame = cv2.imread(str(DATA / "spectate_prematch.png"))
    assert _identified(frame, "prematch_pick", cfg, lib) == 6
    assert _identified(frame, "locked_pick", cfg, lib) < 6
