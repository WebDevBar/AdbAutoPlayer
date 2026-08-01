"""Reading a draft frame's two trios."""

import numpy as np

from adb_auto_player.games.afk_journey.services.solstice import frameside as mod
from adb_auto_player.games.afk_journey.services.solstice.frameside import (
    Verdict,
    classify,
    read_frame_sides,
)


class _Cell:
    def __init__(self, side: str, slot: int) -> None:
        self.name = f"{side}{slot}"
        self.cell_type = "draft_pick"
        self.side = side
        self.slot = slot
        self.x0, self.y0, self.x1, self.y1 = 0, 0, 4, 4


class _Cfg:
    def cells(self, cell_type: str) -> list:
        assert cell_type == "draft_pick"
        return [
            _Cell("left", 1),
            _Cell("right", 2),
            _Cell("right", 3),
            _Cell("left", 4),
            _Cell("left", 5),
            _Cell("right", 6),
        ]

    def scale_chain(self, cell_type: str) -> list[float]:
        return [1.0]


_BY_NAME = {
    "left1": "lucca",
    "right2": "talene",
    "right3": "lilymay",
    "left4": "perseus",
    "left5": "gerda",
    "right6": "koko",
}


def test_groups_by_cell_side_not_slot(monkeypatch):
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    def fake_identify(cell_gray, cell_type, library, cfg, candidates=None):
        raise AssertionError("patched per-cell below")

    calls = []

    def fake_extract(frame_in, cell):
        calls.append(cell.name)
        return np.zeros((4, 4), dtype=np.uint8)

    monkeypatch.setattr(mod, "extract_cell", fake_extract)
    monkeypatch.setattr(
        mod,
        "identify_cell",
        lambda gray, ct, lib, cfg, candidates=None: type(
            "I", (), {"slug": _BY_NAME[calls[-1]]}
        )(),
    )

    blue, red = read_frame_sides(frame, _Cfg(), object())
    assert blue == frozenset({"lucca", "perseus", "gerda"})
    assert red == frozenset({"talene", "lilymay", "koko"})


def test_unidentified_cell_is_omitted(monkeypatch):
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(mod, "extract_cell", lambda f, c: np.zeros((4, 4), np.uint8))
    monkeypatch.setattr(
        mod,
        "identify_cell",
        lambda gray, ct, lib, cfg, candidates=None: type("I", (), {"slug": None})(),
    )

    blue, red = read_frame_sides(frame, _Cfg(), object())
    assert blue == frozenset()
    assert red == frozenset()


# --- classifying a stored row against its frame ----------------------------

_B = frozenset({"lucca", "perseus", "gerda"})
_R = frozenset({"talene", "lilymay", "koko"})


def test_agree_when_frame_blue_matches_row_left():
    assert classify(_B, _R, _B, _R) is Verdict.AGREE


def test_mirrored_when_frame_blue_matches_row_right():
    assert classify(_B, _R, _R, _B) is Verdict.MIRRORED


def test_a_five_pick_draft_frame_is_the_NORMAL_case():
    """Red is one short on every draft frame - the game leaves before pick 6 locks."""
    short_red = frozenset({"talene", "lilymay"})
    assert classify(_B, short_red, _B, _R) is Verdict.AGREE
    assert classify(_B, short_red, _R, _B) is Verdict.MIRRORED


def test_unreadable_when_blue_is_incomplete():
    """Blue takes slots 1, 4 and 5, so it is complete before the draft ends.

    A short blue read means the READER failed, which is not evidence about the row.
    """
    assert classify(frozenset({"lucca"}), _R, _B, _R) is Verdict.UNREADABLE


def test_unreadable_when_red_is_more_than_one_short():
    assert classify(_B, frozenset({"talene"}), _B, _R) is Verdict.UNREADABLE


def test_incomplete_when_the_row_has_fewer_than_six():
    assert classify(_B, _R, frozenset({"lucca"}), _R) is Verdict.INCOMPLETE


def test_partial_when_both_are_complete_but_neither_orientation_matches():
    other = frozenset({"lucca", "perseus", "koko"})
    assert classify(_B, _R, other, _R) is Verdict.PARTIAL


def test_verdict_has_no_no_row_member():
    """A frame with no stored row is dropped by the audit, never classified."""
    assert "NO_ROW" not in Verdict.__members__
    assert [member.value for member in Verdict] == [
        "agree",
        "mirrored",
        "partial",
        "unreadable",
        "incomplete",
    ]
