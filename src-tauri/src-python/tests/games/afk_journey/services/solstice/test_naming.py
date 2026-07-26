"""Resolving a hero slug from OCR text.

Ground truth for the whole mode comes through this function, so a wrong answer is worse
than no answer. Ambiguity therefore returns None rather than guessing.
"""

import cv2
import pytest

from adb_auto_player.games.afk_journey.services.solstice.naming import resolve_hero_name


def test_exact_name_resolves(cfg):
    assert resolve_hero_name(["Atalanta"], cfg) == "atalanta"


def test_ocr_damage_is_tolerated(cfg):
    """A dropped or substituted character must not throw away a usable read."""
    assert resolve_hero_name(["Ata1anta"], cfg) == "atalanta"


def test_unrelated_text_resolves_to_nothing(cfg):
    assert resolve_hero_name(["490K", "Lightbearer", "Marksman"], cfg) is None


def test_empty_input_resolves_to_nothing(cfg):
    assert resolve_hero_name([], cfg) is None


def test_two_distinct_hero_names_resolve_to_nothing(cfg):
    """Two different real heroes named in the same read is genuine ambiguity.

    This is the guard the whole module exists for: len(hits) != 1 must return None even
    when every individual match is a clean, exact hit. Picking a wrong single hero here
    would be recorded as ground truth, so a tie between two real heroes must not resolve
    to either one of them.
    """
    assert resolve_hero_name(["Atalanta", "Leymar"], cfg) is None


def test_reads_the_name_from_a_real_longpress_frame(cfg, ocr_backend, frames):
    from adb_auto_player.models import ConfidenceValue

    frame = cv2.imread(str(frames["longpress_ally1"]))
    blocks = ocr_backend.detect_text_blocks(frame, ConfidenceValue(0.5))
    assert resolve_hero_name([b.text for b in blocks], cfg) == "atalanta"
