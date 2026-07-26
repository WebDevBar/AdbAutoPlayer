"""Identification tests against the two labelled fixture frames.

Ground truth was confirmed by the user, not inferred. The score floors asserted here are
MEASURED baselines - if one fails, something regressed; do not lower the threshold.
"""

from pathlib import Path

import cv2
import pytest
from adb_auto_player.games.afk_journey.services.solstice import vision
from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig
from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary

ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")
pytestmark = pytest.mark.skipif(
    not ICON_DIR.exists(), reason="game icons not extracted (see extract_game_icons.py)"
)

# data/draft_selecting.png - the stuck match. Banned slots are excluded: their
# portrait is covered by a ban glyph. Rowan, Lily May and Reinier were already
# PICKED in this frame, so their grid cells render SKINNED and must still resolve
# to the hero.
GRID_TRUTH = {
    1: "indris",
    2: "viperian",
    3: "dionel",
    4: "laios",
    5: "reinier",
    7: "smokey_meerky",
    8: "odie",
    9: "pang",
    10: "rowan",
    11: "sinbad",
    12: "nerion",
    13: "parisa",
    14: "thador",
    15: "lily_may",
    16: "granny_dahnie",
    17: "lyca",
    18: "cryonaia",
    19: "sonja",
}
BANNED_SLOTS = {6, 20}

# data/prematch_locked.png - slots 1-3 blue, 4-6 red. Igor and Galahad are skinned.
LOCKED_TRUTH = {
    1: "berial",
    2: "eironn",
    3: "igor",
    4: "nara",
    5: "galahad",
    6: "temesia",
}


@pytest.fixture(scope="module")
def library(db_path):
    return IconLibrary.build(SolsticeConfig.load(db_path), ICON_DIR)


def test_extract_cell_returns_the_declared_size(db_path, frames, read_frame):
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    cell = cfg.cells("draft_card")[0]
    out = vision.extract_cell(frame, cell)
    assert out.shape == (cell.height, cell.width) == (120, 110)


def test_extract_cell_rejects_a_wrongly_sized_frame(db_path, frames, read_frame):
    """A rescaled screenshot must fail loudly, not silently crop the wrong region."""
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    scaled = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2))
    with pytest.raises(ValueError, match="1080x1920"):
        vision.extract_cell(scaled, cfg.cells("draft_card")[-1])


def test_identifies_every_unbanned_draft_cell(
    db_path, frames, library, read_frame, slot_of
):
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    wrong, low = [], []
    for cell in cfg.cells("draft_card"):
        if slot_of(cell) in BANNED_SLOTS:
            continue
        res = vision.identify_cell(
            vision.extract_cell(frame, cell), "draft_card", library, cfg
        )
        expected = GRID_TRUTH[slot_of(cell)]
        if res.slug != expected:
            wrong.append((slot_of(cell), expected, res.slug, round(res.score, 3)))
        if res.score < 0.90:
            low.append((slot_of(cell), expected, round(res.score, 3)))
    assert not wrong, f"misidentified: {wrong}"
    assert not low, f"below the measured 0.9055 floor: {low}"


def test_identifies_every_locked_pick(db_path, frames, library, read_frame, slot_of):
    """The 54/54 locked-pick baseline, encoded rather than merely cited."""
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["prematch_locked"])
    wrong, low = [], []
    for cell in cfg.cells("locked_pick"):
        res = vision.identify_cell(
            vision.extract_cell(frame, cell), "locked_pick", library, cfg
        )
        expected = LOCKED_TRUTH[slot_of(cell)]
        if res.slug != expected:
            wrong.append((slot_of(cell), expected, res.slug, round(res.score, 3)))
        if res.score < 0.90:
            low.append((slot_of(cell), expected, round(res.score, 3)))
    assert not wrong, f"misidentified: {wrong}"
    assert not low, f"below the measured 0.9249 floor: {low}"


def test_skinned_cells_still_resolve_to_the_hero(
    db_path, frames, library, read_frame, slot_of
):
    """A picked hero re-renders SKINNED in the grid; it must still map to the hero."""
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    by_slot = {slot_of(c): c for c in cfg.cells("draft_card")}
    for slot, slug in ((10, "rowan"), (15, "lily_may")):
        res = vision.identify_cell(
            vision.extract_cell(frame, by_slot[slot]), "draft_card", library, cfg
        )
        assert res.slug == slug
        assert res.art_ref is not None and res.art_ref.endswith("_s1"), (
            f"expected a skin for slot {slot}, got {res.art_ref}"
        )


def test_unknown_when_the_portrait_is_covered(
    db_path, frames, library, read_frame, slot_of
):
    """A banned cell must come back unknown, never guessed."""
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    banned = next(c for c in cfg.cells("draft_card") if slot_of(c) == 6)
    res = vision.identify_cell(
        vision.extract_cell(frame, banned), "draft_card", library, cfg
    )
    assert res.status == "unknown"
    assert res.slug is None


def test_identification_carries_runner_up_provenance(
    db_path, frames, library, read_frame, slot_of
):
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    cell = next(c for c in cfg.cells("draft_card") if slot_of(c) == 19)  # Sonja
    res = vision.identify_cell(
        vision.extract_cell(frame, cell), "draft_card", library, cfg
    )
    assert res.runner_up_slug is not None
    assert res.runner_up_score is not None
    assert res.margin == pytest.approx(res.score - res.runner_up_score, abs=1e-6)


def test_pool_constraint_does_not_change_the_answer(
    db_path, frames, library, read_frame, slot_of
):
    """Narrowing to the pool must keep the answer and not shrink the margin."""
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    cell = next(c for c in cfg.cells("draft_card") if slot_of(c) == 19)  # Sonja
    gray = vision.extract_cell(frame, cell)
    full = vision.identify_cell(gray, "draft_card", library, cfg)
    narrowed = vision.identify_cell(
        gray, "draft_card", library, cfg, candidates=set(GRID_TRUTH.values())
    )
    assert narrowed.slug == full.slug == "sonja"
    assert narrowed.margin >= full.margin
