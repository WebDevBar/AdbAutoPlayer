"""Reading the post-match summary.

Ground truth: summary_01 is Faust (blue, Defeat) vs Ni Nai (red, Victory) with
atalanta/igor/indris versus baelran/pippa/solise. Confirmed twice - by image matching
and independently by long-press OCR of all six names.
"""

import cv2
import pytest

from adb_auto_player.games.afk_journey.services.solstice.summary import (
    parse_stat_number,
    read_summary,
)

BLUE_TRUTH = ["atalanta", "igor", "indris"]
RED_TRUTH = ["baelran", "pippa", "solise"]


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

    blue = [h.slug for h in read.heroes if h.side == "blue"]
    red = [h.slug for h in read.heroes if h.side == "red"]
    assert blue == BLUE_TRUTH
    assert red == RED_TRUTH


def test_every_identification_clears_the_accept_rule(cfg, library, ocr_backend, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    read = read_summary(frame, cfg, library, ocr_backend)
    for hero in read.heroes:
        assert hero.score >= 0.70, f"{hero.slug} scored {hero.score}"
        assert hero.margin >= 0.10, f"{hero.slug} margin {hero.margin}"


def test_winner_comes_from_the_header_not_the_panel_labels(cfg, library, ocr_backend, frames):
    """summary_02's result banner independently said BLUE LOSES."""
    frame = cv2.imread(str(frames["summary_02"]))
    read = read_summary(frame, cfg, library, ocr_backend)
    assert read.winner == "red"


def test_stats_are_read_for_every_hero(cfg, library, ocr_backend, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    read = read_summary(frame, cfg, library, ocr_backend)
    first = next(h for h in read.heroes if h.side == "blue" and h.slot == 1)
    assert first.stats.sword == 699_000
    assert first.stats.shield == 2_924_000
