"""Identification tests against the two labelled fixture frames.

Ground truth was confirmed by the user, not inferred. The score floors asserted here are
MEASURED baselines - if one fails, something regressed; do not lower the threshold.
"""

import shutil
from pathlib import Path

import cv2
import numpy as np
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

# data/prematch_locked.png - slots 1-3 left, 4-6 right. Igor and Galahad are skinned.
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


def test_classifies_the_three_fixture_screens(frames, anchor_dir, read_frame):
    assert vision.classify_screen(
        read_frame(frames["draft_selecting"]), anchor_dir
    ) == ("draft")
    assert vision.classify_screen(
        read_frame(frames["prematch_locked"]), anchor_dir
    ) == ("prematch_locked")


def test_spectate_is_not_mistaken_for_draft(frames, anchor_dir, read_frame):
    """Spectate must never classify as draft.

    The two do NOT share geometry, and reading draft cells off a spectate frame
    would silently produce nonsense.
    """
    assert vision.classify_screen(read_frame(frames["spectate"]), anchor_dir) != "draft"


def test_each_anchor_fires_only_on_its_own_screen(frames, anchor_dir, read_frame):
    for name in ("draft_selecting", "spectate"):
        got = vision.classify_screen(read_frame(frames[name]), anchor_dir)
        assert got != "prematch_locked", f"{name} misclassified as prematch_locked"


def test_unknown_for_an_unrelated_image(anchor_dir):
    """A frame with neither anchor must be unknown, not defaulted to a real screen."""
    blank = np.zeros((1920, 1080, 3), dtype=np.uint8)
    assert vision.classify_screen(blank, anchor_dir) == "unknown"


def test_ban_detection_finds_exactly_the_two_banned_cells(
    db_path, frames, anchor_dir, read_frame, slot_of
):
    """Three glyph variants are required.

    The red/blue pair alone missed one hero's overlay (0.279 / 0.241), letting a banned
    card through as a phantom hero.
    """
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    glyphs = vision.load_ban_glyphs(anchor_dir)
    assert len(glyphs) >= 3, f"expected >= 3 glyph variants, found {len(glyphs)}"
    banned = {
        slot_of(c)
        for c in cfg.cells("draft_card")
        if vision.is_banned(vision.extract_cell(frame, c), glyphs)
    }
    assert banned == BANNED_SLOTS, f"expected {BANNED_SLOTS}, got {banned}"


def test_identify_pool_reads_the_whole_grid(
    db_path, frames, anchor_dir, library, read_frame
):
    cfg = SolsticeConfig.load(db_path)
    pool = vision.identify_pool(
        read_frame(frames["draft_selecting"]), cfg, library, anchor_dir
    )
    assert pool.banned_slots == BANNED_SLOTS
    assert pool.slugs == set(GRID_TRUTH.values())
    assert all(pool.per_slot[s].status == "identified" for s in GRID_TRUTH)
    assert set(pool.per_slot) & BANNED_SLOTS == set(), "banned slots must not be read"


def test_identify_with_pool_reports_which_tier_answered(
    db_path, frames, library, read_frame, slot_of
):
    """A pool hit and a full-library fallback must be distinguishable afterwards."""
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    cell = next(c for c in cfg.cells("draft_card") if slot_of(c) == 19)  # Sonja
    gray = vision.extract_cell(frame, cell)

    hit = vision.identify_with_pool(gray, "draft_card", library, cfg, {"sonja", "lyca"})
    assert hit.slug == "sonja"
    assert hit.candidate_scope == "pool"
    assert hit.pool_miss == 0

    # a pool that cannot contain the answer must fall back AND say so
    miss = vision.identify_with_pool(
        gray, "draft_card", library, cfg, {"berial", "tilaya"}
    )
    assert miss.slug == "sonja"
    assert miss.candidate_scope == "full_library"
    assert miss.pool_miss == 1


def test_missing_anchor_fails_loudly(tmp_path, frames, read_frame):
    """A broken install must raise, not classify everything as 'unknown'.

    Silently returning 'unknown' would leave a device loop spinning forever with no
    indication of the real problem.
    """
    empty = tmp_path / "no_anchors"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="anchor missing"):
        vision.classify_screen(read_frame(frames["draft_selecting"]), empty)


def test_missing_ban_glyphs_fails_loudly(tmp_path):
    """No glyphs would mean nothing is ever detected as banned - phantom heroes."""
    empty = tmp_path / "no_glyphs"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="ban glyph"):
        vision.load_ban_glyphs(empty)


def test_too_few_ban_glyphs_fails_loudly(tmp_path, anchor_dir):
    """Three variants are required; the red/blue pair alone missed a real ban."""
    partial = tmp_path / "two_glyphs"
    partial.mkdir()
    for name in ("ban_glyph_red.png", "ban_glyph_blue.png"):
        shutil.copy(anchor_dir / name, partial / name)
    with pytest.raises(FileNotFoundError, match="at least 3"):
        vision.load_ban_glyphs(partial)


def test_empty_pool_is_rejected(db_path, frames, library, read_frame, slot_of):
    """An empty pool means the pool READ failed - it is not a constraint.

    Accepting it would silently mark every pick as a pool miss and hide the failure.
    """
    cfg = SolsticeConfig.load(db_path)
    frame = read_frame(frames["draft_selecting"])
    cell = next(c for c in cfg.cells("draft_card") if slot_of(c) == 19)
    gray = vision.extract_cell(frame, cell)
    with pytest.raises(ValueError, match="empty pool"):
        vision.identify_with_pool(gray, "draft_card", library, cfg, set())
    # None still means "no pool available", which is a legitimate full-library search
    res = vision.identify_with_pool(gray, "draft_card", library, cfg, None)
    assert res.slug == "sonja"
    assert res.pool_miss == 0
