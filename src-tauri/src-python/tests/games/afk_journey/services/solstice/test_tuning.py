"""Crop/scale tuning against a CONFIRMED identity.

Measured headroom on the three weakest summary cards, tuning crop alone:
  solise  0.781 -> 0.866 (margin 0.244) at hw=22 top=18 bot=26
  baelran 0.798 -> 0.844 (margin 0.323) at hw=24 top=14 bot=26
  indris  0.876 -> 0.905 (margin 0.189) at hw=22 top=14 bot=32
"""

import shutil

import cv2
import pytest

from adb_auto_player.games.afk_journey.services.solstice.tuning import tune_cell

SOLISE_CENTRE = (90, 1307)


@pytest.fixture
def tmp_db(tmp_path, db_path):
    target = tmp_path / "heroes.sqlite"
    shutil.copy(db_path, target)
    return target


@pytest.mark.slow
def test_tuning_improves_the_weakest_card(cfg, library, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    result = tune_cell(gray, SOLISE_CENTRE, "solise", library, cfg)

    assert result is not None
    assert result.score >= 0.80, f"expected >=0.80, got {result.score}"
    assert result.margin >= 0.10


def test_tuning_returns_none_when_the_truth_never_wins(cfg, library, frames):
    """If the named hero is not what is on screen, tuning must refuse rather than
    force the wrong answer to score better."""
    frame = cv2.imread(str(frames["summary_01"]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    assert tune_cell(gray, SOLISE_CENTRE, "thoran", library, cfg) is None


@pytest.mark.slow
def test_learn_if_improved_stores_a_better_transform(cfg, library, frames, tmp_db):
    """The real wiring: confirmed identity in, stored+retrievable transform out."""
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow, MatchStore,
    )
    from adb_auto_player.games.afk_journey.services.solstice.tuning import learn_if_improved

    gray = cv2.cvtColor(cv2.imread(str(frames["summary_01"])), cv2.COLOR_BGR2GRAY)
    store = MatchStore(tmp_db)
    audit_id = store.record_audit(AuditRow(
        screen_slug="solstice_summary", side="red", slot=3,
        image_slug="solise", image_art_ref="Solise",
        image_score=0.781, image_margin=0.201, ocr_slug="solise", frame_path=None,
    ))

    stored = learn_if_improved(
        store=store, cfg=cfg, library=library, gray=gray, centre=(90, 1307),
        screen_slug="solstice_summary", image_slug="solise",
        confirmed_slug="solise", art_ref="Solise",
        current_score=0.781, current_margin=0.201, audit_id=audit_id,
    )

    assert stored is True
    got = store.transform_for("solstice_summary", "solise")
    assert got is not None
    assert got["margin"] > 0.201, "must only store an IMPROVEMENT"


def test_learn_if_improved_refuses_unconfirmed(cfg, library, frames, tmp_db):
    """No confirmation means nothing is stored, however good the tuning looks."""
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import learn_if_improved

    gray = cv2.cvtColor(cv2.imread(str(frames["summary_01"])), cv2.COLOR_BGR2GRAY)
    store = MatchStore(tmp_db)

    stored = learn_if_improved(
        store=store, cfg=cfg, library=library, gray=gray, centre=(90, 1307),
        screen_slug="solstice_summary", image_slug="solise",
        confirmed_slug=None, art_ref="Solise",
        current_score=0.781, current_margin=0.201, audit_id=None,
    )

    assert stored is False
    assert store.transform_for("solstice_summary", "solise") is None


def test_train_from_frame_confirms_by_side_set(cfg, library, frames, tmp_db):
    """An empty confirmed set produces no rows claiming agreement and no set-consistency."""
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import train_from_frame

    frame = cv2.imread(str(frames["spectate_prematch"]))
    store = MatchStore(tmp_db)

    result = train_from_frame(
        store=store, cfg=cfg, library=library, frame=frame,
        screen_slug="spectate_prematch", cell_type="prematch_pick",
        # Deliberately empty: nothing can be set-consistent, and no row may ever claim
        # ocr_slug agreement - this screen has no per-cell truth to confirm against.
        confirmed_by_side={"blue": set(), "red": set()},
        frame_path="/tmp/x.png", match_id=None,
    )
    assert result.written == 6
    assert result.deduced == 0
    assert result.set_consistent == 0
    agreed, total = store.audit_agreement_rate("spectate_prematch")
    assert total == 6
    assert agreed == 0, "cross-screen rows must never claim agreement"


def test_train_from_frame_never_confirms_even_when_set_consistent(cfg, library, frames, tmp_db):
    """Set membership plus uniqueness is a plausibility MEASUREMENT, never confirmation.

    Building the confirmed set from what the frame itself reads maximises set-consistency
    (every read is trivially in its own side's set and unique), which is exactly the case
    that must NOT be allowed to write agreed=1 - there is still no per-cell truth here, only
    a set the image matcher itself populated.
    """
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import train_from_frame
    from adb_auto_player.games.afk_journey.services.solstice.vision import (
        extract_cell, identify_cell,
    )

    frame = cv2.imread(str(frames["spectate_prematch"]))

    by_side: dict[str, set[str]] = {"blue": set(), "red": set()}
    for cell in cfg.cells("prematch_pick"):
        result = identify_cell(extract_cell(frame, cell), "prematch_pick", library, cfg)
        if result.slug:
            by_side[cell.side or ""].add(result.slug)
    assert any(by_side.values()), "fixture read nothing - check the seeded cell geometry"

    store = MatchStore(tmp_db)
    result = train_from_frame(
        store=store, cfg=cfg, library=library, frame=frame,
        screen_slug="spectate_prematch", cell_type="prematch_pick",
        confirmed_by_side=by_side, frame_path="/tmp/x.png", match_id=None,
    )

    assert result.set_consistent > 0, "a hero in its own side's set must be set-consistent"
    assert result.deduced == 0, "every read was in-set, so nothing is an elimination target"
    agreed, total = store.audit_agreement_rate("spectate_prematch")
    assert total == 6
    assert agreed == 0, "set-consistency must never be reported as OCR agreement"


def test_train_from_frame_does_not_confirm_a_swap(cfg, tmp_db):
    """The exact failure mode this design must refuse: two same-side heroes swapped
    between cells. Both reads remain in the confirmed set and both remain unique on
    their side, so set-consistency alone cannot tell this apart from a correct read -
    it must never be treated as confirmation, and no transform may be learned from it.
    """
    from unittest.mock import patch

    import numpy as np

    from adb_auto_player.games.afk_journey.services.solstice.config import Cell
    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import train_from_frame
    from adb_auto_player.games.afk_journey.services.solstice.vision import Identification

    cell_a = Cell(
        name="prematch_blue_1", cell_type="prematch_pick",
        x0=0, y0=0, x1=90, y1=120, side="blue", slot=1,
    )
    cell_b = Cell(
        name="prematch_blue_2", cell_type="prematch_pick",
        x0=90, y0=0, x1=180, y1=120, side="blue", slot=2,
    )
    # Cell A actually shows heroA but the matcher (mis)reads heroB, and vice versa - a
    # swap. Both slugs are in the confirmed set and each is unique on the "blue" side.
    ordered_reads = [
        Identification(
            slug="heroB", art_ref="HeroB", score=0.9, margin=0.3, status="identified",
        ),
        Identification(
            slug="heroA", art_ref="HeroA", score=0.9, margin=0.3, status="identified",
        ),
    ]

    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    store = MatchStore(tmp_db)

    with (
        patch(
            "adb_auto_player.games.afk_journey.services.solstice.vision.extract_cell",
            return_value=np.zeros((10, 10), dtype=np.uint8),
        ),
        patch(
            "adb_auto_player.games.afk_journey.services.solstice.vision.identify_cell",
            side_effect=ordered_reads,
        ),
        patch.object(type(cfg), "cells", return_value=[cell_a, cell_b]),
    ):
        result = train_from_frame(
            store=store, cfg=cfg, library=None, frame=frame,
            screen_slug="spectate_prematch", cell_type="prematch_pick",
            confirmed_by_side={"blue": {"heroA", "heroB"}, "red": set()},
            frame_path="/tmp/x.png", match_id=None,
        )

    assert result.written == 2
    # Both reads are set-consistent (each slug is in the set and unique on its side) -
    # that is exactly the trap: set-consistency cannot distinguish this swap from a
    # correct read.
    assert result.set_consistent == 2
    # Both reads were in the confirmed set, so there is no odd cell to pin by
    # elimination - the swap is invisible to this frame, by design.
    assert result.deduced == 0
    agreed, total = store.audit_agreement_rate("spectate_prematch")
    assert total == 2
    assert agreed == 0, "a swap must never be recorded as agreement"
    assert store.transform_for("spectate_prematch", "heroA") is None
    assert store.transform_for("spectate_prematch", "heroB") is None


def test_train_from_frame_deduces_the_odd_cell_by_elimination(cfg, tmp_db):
    """The ONE case with genuine per-cell truth: three confirmed heroes, two cells read
    two of them correctly, and the third cell reads something outside the set. The
    hero that no other cell accounted for must be that odd cell's true content - there
    is nowhere else for it to be. This must still record agreed=0 (the deduced hero
    differs from what was actually read), never a confirmation to learn from.
    """
    from unittest.mock import patch

    import numpy as np

    from adb_auto_player.games.afk_journey.services.solstice.config import Cell
    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import train_from_frame
    from adb_auto_player.games.afk_journey.services.solstice.vision import Identification

    cell_1 = Cell(
        name="prematch_blue_1", cell_type="prematch_pick",
        x0=0, y0=0, x1=90, y1=120, side="blue", slot=1,
    )
    cell_2 = Cell(
        name="prematch_blue_2", cell_type="prematch_pick",
        x0=90, y0=0, x1=180, y1=120, side="blue", slot=2,
    )
    cell_3 = Cell(
        name="prematch_blue_3", cell_type="prematch_pick",
        x0=180, y0=0, x1=270, y1=120, side="blue", slot=3,
    )
    # heroA and heroB are read correctly; cell_3 misreads heroC as something outside the
    # confirmed set entirely ("junk"). heroC is unaccounted for anywhere else, so cell_3
    # must actually be heroC.
    ordered_reads = [
        Identification(slug="heroA", art_ref="HeroA", score=0.9, margin=0.3, status="identified"),
        Identification(slug="heroB", art_ref="HeroB", score=0.9, margin=0.3, status="identified"),
        Identification(slug="junk", art_ref="Junk", score=0.75, margin=0.05, status="identified"),
    ]

    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)
    store = MatchStore(tmp_db)

    with (
        patch(
            "adb_auto_player.games.afk_journey.services.solstice.vision.extract_cell",
            return_value=np.zeros((10, 10), dtype=np.uint8),
        ),
        patch(
            "adb_auto_player.games.afk_journey.services.solstice.vision.identify_cell",
            side_effect=ordered_reads,
        ),
        patch.object(type(cfg), "cells", return_value=[cell_1, cell_2, cell_3]),
    ):
        result = train_from_frame(
            store=store, cfg=cfg, library=None, frame=frame,
            screen_slug="spectate_prematch", cell_type="prematch_pick",
            confirmed_by_side={"blue": {"heroA", "heroB", "heroC"}, "red": set()},
            frame_path="/tmp/x.png", match_id=None,
        )

    assert result.written == 3
    assert result.deduced == 1, "exactly one cell (slot 3) should be pinned by elimination"
    agreed, total = store.audit_agreement_rate("spectate_prematch")
    assert total == 3
    assert agreed == 0, "a deduction is always a disagreement by construction"
    # No transform may ever be learned from a deduction - learn_if_improved is never
    # called from train_from_frame at all.
    assert store.transform_for("spectate_prematch", "heroC") is None


def test_only_ocr_confirmed_slots_seed_the_confirmation_set():
    """An image-only summary read must NEVER become cross-screen ground truth.

    HeroSlot.hero_slug is `confirmed or hero.slug`, so it is populated even when the
    long-press failed. Calls the PRODUCTION helper - an inline reimplementation of the
    rule would pass even if the mixin built the set wrongly, which is the only thing
    worth testing here.
    """
    from adb_auto_player.games.afk_journey.services.solstice.store import HeroSlot
    from adb_auto_player.games.afk_journey.services.solstice.tuning import confirmed_sides

    confirmed = confirmed_sides([
        HeroSlot(side="blue", slot=1, hero_slug="atalanta", art_ref="Atalanta",
                 status="identified", identified_by="longpress_ocr"),
        HeroSlot(side="blue", slot=2, hero_slug="igor", art_ref="Igor",
                 status="identified", identified_by="image"),
        HeroSlot(side="red", slot=1, hero_slug=None, art_ref=None,
                 status="unknown", identified_by=None),
    ])

    assert confirmed["blue"] == {"atalanta"}, "image-only reads must be excluded"
    assert confirmed["red"] == set()
