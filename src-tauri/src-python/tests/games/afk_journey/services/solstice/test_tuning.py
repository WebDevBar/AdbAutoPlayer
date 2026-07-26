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
    """A hero in that side's confirmed set is confirmed; anything else is not."""
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import train_from_frame

    frame = cv2.imread(str(frames["spectate_prematch"]))
    store = MatchStore(tmp_db)

    written = train_from_frame(
        store=store, cfg=cfg, library=library, frame=frame,
        screen_slug="spectate_prematch", cell_type="prematch_pick",
        # Deliberately empty: nothing can be confirmed, so every row must record
        # ocr_slug=None rather than inventing agreement.
        confirmed_by_side={"blue": set(), "red": set()},
        frame_path="/tmp/x.png", match_id=None,
    )
    assert written == 6
    agreed, total = store.audit_agreement_rate("spectate_prematch")
    assert total == 6
    assert agreed == 0, "nothing can be confirmed against an empty set"


def test_train_from_frame_confirms_when_the_hero_is_in_the_set(cfg, library, frames, tmp_db):
    """The POSITIVE case: a uniquely-read hero present in that side's set is confirmed.

    The confirmed set is built from what the frame actually reads, because this test is
    about the RULE (set membership plus uniqueness produces agreement), not about which
    heroes happen to be in the fixture. Asserting only the negative case would pass even
    if train_from_frame never confirmed anything at all.
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
    train_from_frame(
        store=store, cfg=cfg, library=library, frame=frame,
        screen_slug="spectate_prematch", cell_type="prematch_pick",
        confirmed_by_side=by_side, frame_path="/tmp/x.png", match_id=None,
    )

    agreed, total = store.audit_agreement_rate("spectate_prematch")
    assert total == 6
    assert agreed > 0, "a hero in its own side's set must produce an agreeing row"


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
