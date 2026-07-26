"""Reading the post-match summary.

Ground truth: summary_01 is Faust (left, Defeat) vs Ni Nai (right, Victory) with
atalanta/igor/indris versus baelran/pippa/solise. Confirmed twice - by image matching
and independently by long-press OCR of all six names.
"""

import cv2
import numpy as np
import pytest

from adb_auto_player.games.afk_journey.services.solstice import summary
from adb_auto_player.games.afk_journey.services.solstice.summary import (
    parse_stat_number,
    read_summary,
)
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.models.geometry import Box, Point
from adb_auto_player.models.ocr import OCRResult

LEFT_TRUTH = ["atalanta", "igor", "indris"]
RIGHT_TRUTH = ["baelran", "pippa", "solise"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("699K", 699_000),
        ("10,500K", 10_500_000),
        ("0", 0),
        ("28,290", 28_290),
        ("2924K", 2_924_000),
        ("", None),
        ("Ally", None),
    ],
)
def test_parse_stat_number(text, expected):
    assert parse_stat_number(text) == expected


def test_identifies_all_six_heroes(cfg, library, ocr_backend, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    read = read_summary(frame, cfg, library, ocr_backend)

    left = [h.slug for h in read.heroes if h.side == "left"]
    right = [h.slug for h in read.heroes if h.side == "right"]
    assert left == LEFT_TRUTH
    assert right == RIGHT_TRUTH


def test_every_identification_clears_the_accept_rule(cfg, library, ocr_backend, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    read = read_summary(frame, cfg, library, ocr_backend)
    for hero in read.heroes:
        assert hero.score >= 0.70, f"{hero.slug} scored {hero.score}"
        assert hero.margin >= 0.10, f"{hero.slug} margin {hero.margin}"


def test_winner_comes_from_the_header_not_the_panel_labels(cfg, library, ocr_backend, frames):
    """summary_02's result banner independently said LEFT LOSES."""
    frame = cv2.imread(str(frames["summary_02"]))
    read = read_summary(frame, cfg, library, ocr_backend)
    assert read.winner == "right"


def test_stats_are_read_for_every_hero(cfg, library, ocr_backend, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    read = read_summary(frame, cfg, library, ocr_backend)
    first = next(h for h in read.heroes if h.side == "left" and h.slot == 1)
    assert first.stats.sword == 699_000
    assert first.stats.shield == 2_924_000


class _StubOCR:
    """Returns a fixed list of blocks regardless of the image it is given.

    _read_winner takes exactly one full-width crop and passes it to
    detect_text_blocks once - a stub that ignores the image entirely is a faithful
    substitute for RapidOCR here, and lets each case pin down exactly one code path
    (merged-block word order, or single-word box position) without depending on
    RapidOCR's actual detection behaviour or a second fixture frame.
    """

    def __init__(self, blocks: list[OCRResult]) -> None:
        self._blocks = blocks

    def detect_text_blocks(self, image, min_confidence=ConfidenceValue(0.0)):
        return self._blocks

    def extract_text(self, image) -> str:
        return " ".join(b.text for b in self._blocks)


def _block(text: str, centre_x: int) -> OCRResult:
    """An OCRResult whose box centre.x is exactly `centre_x` (y is unused here)."""
    return OCRResult(
        text=text,
        confidence=ConfidenceValue(1.0),
        box=Box(Point(centre_x - 5, 260), 10, 10),
    )


def test_read_winner_merged_block_victory_first_is_left():
    """'VictoryDefeat' - Victory read first (left) means the left side won.

    Neither fixture exercises this order (both are 'DefeatVictory' / right-wins), so
    this is the only place a left win is checked at all.
    """
    ocr = _StubOCR([_block("VictoryDefeat", centre_x=537)])
    dummy_frame = np.zeros((640, 1080, 3), dtype=np.uint8)
    assert summary._read_winner(dummy_frame, ocr) == "left"


def test_read_winner_merged_block_defeat_first_is_right():
    """'DefeatVictory' - Defeat read first (left) means victory is on the right."""
    dummy_frame = np.zeros((640, 1080, 3), dtype=np.uint8)
    ocr = _StubOCR([_block("DefeatVictory", centre_x=537)])
    assert summary._read_winner(dummy_frame, ocr) == "right"


def test_read_winner_single_word_falls_back_to_box_position():
    """Only 'Victory' survived OCR (merged with a player name, as on summary_02).

    Box centre on the LEFT of the split line here - the opposite side from the
    merged-block case above - so this proves the position fallback actually flips
    the answer rather than the test just happening to agree with the string-order
    branch.
    """
    dummy_frame = np.zeros((640, 1080, 3), dtype=np.uint8)
    ocr = _StubOCR([_block("VictoryCaffu", centre_x=200)])
    assert summary._read_winner(dummy_frame, ocr) == "left"
