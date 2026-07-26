"""The icon library decodes the GAME's own hero art, not wiki uploads.

Measured on 2026-07-26, game assets beat the wiki library on every surface:
locked_pick blind test 52/54 -> 54/54 correct, median score 0.797 -> 0.973.
Two heroes only work with game art: the wiki's Reinier is a different picture
entirely (0.631 -> 0.975) and Eironn resolves via his skin (0.648 -> 0.953).

Icons are 430MB and NOT committed, so these tests skip cleanly without them.
"""

import sqlite3
from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig
from adb_auto_player.games.afk_journey.services.solstice.icons import (
    IconLibrary,
    decode_ast,
)

ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")
pytestmark = pytest.mark.skipif(
    not ICON_DIR.exists(), reason="game icons not extracted (see extract_game_icons.py)"
)

SONJA = "spui_herohead_66.png"  # external_id 66


def test_decodes_a_known_icon_to_the_documented_size():
    img = decode_ast(ICON_DIR / "hero" / SONJA)
    assert img.shape == (248, 180, 4)
    assert img.dtype == np.uint8


def test_decode_is_not_upside_down():
    """Unity's texture origin is bottom-left, so the raw decode arrives flipped.

    In a correctly oriented portrait the body and shoulders fill the BOTTOM of the frame
    while there is transparent space above the head, so the bottom half carries far more
    opaque pixels. Measured on Sonja: bottom 172.4 vs top 50.5. Drop the flip in
    decode_ast() and this assertion inverts.
    """
    img = decode_ast(ICON_DIR / "hero" / SONJA)
    alpha = img[:, :, 3]
    assert alpha[124:].mean() > alpha[:124].mean() * 2


def test_header_drives_dimensions_not_a_hardcoded_size():
    """Icons are NOT all 180x248.

    Assuming so squashed a 184x248 skin and cost ~0.15 of match score. The AST header
    carries width/height; the decoder must use them.
    """
    sizes = set()
    for name in sorted(p.name for p in (ICON_DIR / "heroskin").glob("*.png"))[:40]:
        img = decode_ast(ICON_DIR / "heroskin" / name)
        sizes.add((img.shape[1], img.shape[0]))
    assert len(sizes) > 1, f"expected varied icon sizes, got {sizes}"


def test_library_covers_every_usable_roster_hero(db_path):
    cfg = SolsticeConfig.load(db_path)
    lib = IconLibrary.build(cfg, ICON_DIR)
    slugs = {e.slug for e in lib.entries()}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    usable = {
        r[0]
        for r in con.execute(
            "SELECT h.slug FROM solstice_roster r JOIN hero h ON h.name = r.name "
            "WHERE r.status='usable'"
        )
    }
    con.close()
    assert usable - slugs == set(), f"missing from library: {sorted(usable - slugs)}"


def test_library_excludes_npcs(db_path):
    """IDs >= 1000 are NPCs, mobs and bosses - they never appear in a draft."""
    cfg = SolsticeConfig.load(db_path)
    lib = IconLibrary.build(cfg, ICON_DIR)
    heroes = cfg.heroes()
    for entry in lib.entries():
        assert entry.slug in heroes, f"library entry for unknown slug: {entry.slug}"
        ext = heroes[entry.slug].external_id
        assert ext is not None and ext < 1000, f"NPC id {ext} in library"


def test_library_includes_skins_mapped_to_their_hero(db_path):
    """A skin resolves to its HERO. Identification never needs to name the skin."""
    cfg = SolsticeConfig.load(db_path)
    lib = IconLibrary.build(cfg, ICON_DIR)
    skins = [e for e in lib.entries() if e.art_kind == "skin"]
    assert skins, "no skin entries in the library"
    assert any(e.slug == "eironn" and "15_s1" in e.art_ref for e in skins)
    assert all(e.slug in cfg.heroes() for e in skins)


def test_gamma_brightens_the_decoded_art(db_path):
    """Gamma correction brightens the decoded art.

    Decoded RGB renders darker than the game draws it; library_config.gamma
    (exponent 1/1.8) raised the match median from 0.9550 to 0.9718 on labelled cells.
    """
    cfg = SolsticeConfig.load(db_path)
    raw = decode_ast(ICON_DIR / "hero" / SONJA)
    lib = IconLibrary.build(cfg, ICON_DIR)
    entry = next(e for e in lib.entries() if e.art_ref == "spui_herohead_66")
    opaque = raw[:, :, 3] > 200
    raw_gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
    assert entry.gray[opaque].mean() > raw_gray[opaque].mean()


def test_for_slugs_narrows_the_candidate_set(db_path):
    """The pool constraint: 121 candidates down to <= 20 for a given match."""
    cfg = SolsticeConfig.load(db_path)
    lib = IconLibrary.build(cfg, ICON_DIR)
    subset = lib.for_slugs({"sonja", "eironn"})
    assert subset, "expected entries for those heroes"
    assert {e.slug for e in subset} == {"sonja", "eironn"}
    assert len(subset) < len(lib.entries())
