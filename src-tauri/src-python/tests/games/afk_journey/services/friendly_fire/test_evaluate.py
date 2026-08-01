"""The orchestrator. A fake OCR backend keeps this device-free and deterministic."""

from pathlib import Path

import cv2
import pytest
from adb_auto_player.games.afk_journey.services.friendly_fire.evaluate import (
    confirms_take,
    evaluate,
    screen_changed,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode
from adb_auto_player.games.afk_journey.services.friendly_fire.select import Action
from adb_auto_player.games.afk_journey.settings import OpponentPosition


DATA = Path(__file__).parent / "data"
# The REAL template directory, not a stub. A stub would have passed while the
# packaged build was looking in the wrong place entirely.
TEMPLATES = Path(__file__).parents[5] / "adb_auto_player/games/afk_journey/templates"


def _frame(name):
    return cv2.imread(str(next(DATA.glob(f"{name}*.png"))), cv2.IMREAD_COLOR)


class _NoText:
    """An OCR backend that finds nothing, isolating the colour arm."""

    def detect_text_blocks(self, image, min_confidence=None):
        return []


class _Raises:
    """An OCR backend that blows up - failure must not abort the mode."""

    def detect_text_blocks(self, image, min_confidence=None):
        raise RuntimeError("ocr exploded")


@pytest.fixture(autouse=True)
def _isolated_collection(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    return tmp_path


def test_a_flagged_middle_card_is_skipped_for_card_1():
    d = evaluate(_frame("01"), Mode.ARENA, OpponentPosition.Left, _NoText(), TEMPLATES)
    assert d.action is Action.TAKE and d.card == 0


def test_a_flagged_first_card_falls_through_to_card_2():
    """Frame 03 has the badge on the LEFT card."""
    d = evaluate(_frame("03"), Mode.ARENA, OpponentPosition.Left, _NoText(), TEMPLATES)
    assert d.action is Action.TAKE and d.card == 1


def test_a_clean_board_takes_the_first_card():
    d = evaluate(_frame("05"), Mode.SUPREME_ARENA, OpponentPosition.Left, _NoText(), TEMPLATES)
    assert d.action is Action.TAKE and d.card == 0


def test_the_configured_position_is_respected():
    """Frame 08 flags the MIDDLE card, so Middle must fall through to card 1."""
    d = evaluate(_frame("08"), Mode.SUPREME_ARENA, OpponentPosition.Middle, _NoText(), TEMPLATES)
    assert d.action is Action.TAKE and d.card == 0


def test_ocr_failure_does_not_abort_the_evaluation():
    d = evaluate(_frame("01"), Mode.ARENA, OpponentPosition.Left, _Raises(), TEMPLATES)
    assert d.action is Action.TAKE


def test_every_evaluation_archives_its_frame(_isolated_collection):
    evaluate(_frame("01"), Mode.ARENA, OpponentPosition.Left, _NoText(), TEMPLATES)
    assert list(_isolated_collection.glob("*.png"))


def test_an_excluded_card_is_honoured_by_the_orchestrator():
    d = evaluate(
        _frame("05"),
        Mode.SUPREME_ARENA,
        OpponentPosition.Left,
        _NoText(),
        TEMPLATES,
        excluded=frozenset({0}),
    )
    assert d.action is Action.TAKE and d.card == 1


def test_the_confirming_read_agrees_on_a_clear_card():
    assert confirms_take(
        _frame("05"), Mode.SUPREME_ARENA, OpponentPosition.Left, _NoText(), 0
    )


def test_the_confirming_read_rejects_a_flagged_card():
    """Frame 01 flags the middle card, so confirming card 2 must fail."""
    assert not confirms_take(
        _frame("01"), Mode.ARENA, OpponentPosition.Left, _NoText(), 1
    )


def test_screen_changed_detects_a_redraw_and_a_stall():
    assert not screen_changed(_frame("01"), _frame("01"))
    assert screen_changed(_frame("01"), _frame("05"))
