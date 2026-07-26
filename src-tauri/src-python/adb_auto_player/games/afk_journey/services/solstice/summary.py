"""Reading the post-match summary screen.

This screen is the whole reason Mode A exists: it shows both comps, the winner and
per-hero stats, with no time pressure at all - it waits for input. Everything here is
pure, so it can be tested against saved frames with no device.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from adb_auto_player.models import ConfidenceValue
from adb_auto_player.ocr._backend import OCRBackend

from .config import SolsticeConfig
from .icons import IconLibrary
from .vision import extract_cell, identify_cell

CELL_TYPE = "summary_hero"
SCREEN_SLUG = "solstice_summary"

# The Defeat/Victory banner and the two player names share this vertical band.
_HEADER_BAND = (200, 320)
_HEADER_SPLIT_X = 540
# Minimum orange-vs-blue separation between the two header halves before the colour is
# trusted. Observed separations were far above this on every frame checked.
_WINNER_COLOUR_MIN_DELTA = 30.0
_HEADER_LEFT = (60, 470)
_HEADER_RIGHT = (610, 1020)
# Colour probes, deliberately narrower than the OCR windows above. The player AVATARS sit
# at the far left and far right of the banner and are full-colour portraits: sampling from
# x40-160 reads orange regardless of who won. The sash boundary sits near x540. These
# windows avoid both, and the sash is a solid fill so a modest patch is plenty.
# The roster panel tabs. Orange = that panel's trio won, blue = they lost.
_TOP_TAB = (355, 400)
_BOTTOM_TAB = (1000, 1050)
_TAB_X = (40, 200)
_PROBE_BAND = (205, 245)
_PROBE_LEFT = (200, 500)
_PROBE_RIGHT = (580, 880)

# The banner text itself ('Defeat' / 'Victory') is a low-contrast, gradient-backed
# script font. RapidOCR only detects it given a tall, full-width crop - a crop sized
# like _HEADER_BAND (which is plenty for the opaque player names) returns nothing at
# all, and splitting it into left/right halves (as _read_players does) returns nothing
# either, no matter how wide each half is. Verified on both fixtures: a 0:620 full-width
# pass returns it as ONE merged block - 'DefeatVictory' on summary_01, or merged with a
# player name ('VictoryCaffu') on summary_02 when only one word survives OCR.
_WINNER_BAND_Y1 = 620

# Stat columns, measured on summary_01.png. Rows share the hero cell's vertical centre.
# Columns are 190px wide, not the width of the widest number ('10,500K' is ~121px) -
# RapidOCR's detector silently returns nothing on this small text when the crop is much
# wider than that (a 280px-wide sword column returned zero blocks on every row; 190px
# was the widest that stayed reliable). _STAT_HALF_HEIGHT is 34, not the number glyph's
# own height, for the same reason: a lone '0' is small enough that a tighter 26px crop
# missed it on every row that had one, while every non-zero number still read fine at 26.
_STAT_COLUMNS = {
    "sword": (181, 371),
    "heart": (485, 675),
    "shield": (788, 978),
}
_STAT_HALF_HEIGHT = 34

_NUMBER = re.compile(r"^([\d,]+(?:\.\d+)?)\s*([KkMm]?)$")


@dataclass(frozen=True)
class HeroStats:
    """The three summary columns, named for their ICONS not their meaning.

    The columns are headed by a sword, a heart and a shield. Damage dealt and healing are
    the obvious readings of the first two; the third is genuinely ambiguous (damage taken?
    blocked? shielding applied?) and has NOT been confirmed. Naming these after the icons
    avoids baking a guess into the schema.
    """

    sword: int | None
    heart: int | None
    shield: int | None


@dataclass(frozen=True)
class SummaryHero:
    side: str
    slot: int
    slug: str | None
    art_ref: str | None
    score: float
    margin: float
    stats: HeroStats


@dataclass(frozen=True)
class SummaryRead:
    winner: str | None  # 'left' | 'right' | None when the header could not be read
    left_player: str | None
    right_player: str | None
    heroes: list[SummaryHero]


def parse_stat_number(text: str) -> int | None:
    """'699K' -> 699000, '10,500K' -> 10500000, '28,290' -> 28290, junk -> None."""
    match = _NUMBER.match(text.strip())
    if match is None:
        return None
    digits, suffix = match.groups()
    value = float(digits.replace(",", ""))
    if suffix.upper() == "K":
        value *= 1_000
    elif suffix.upper() == "M":
        value *= 1_000_000
    return int(value)


def _winner_by_panel_tint(frame: np.ndarray) -> str | None:
    """Which of the two hero panels won, from the panel tab's tint.

    Orange means that panel's three heroes WON, blue means they lost. This is the
    strongest signal available and the simplest: it ignores left and right entirely, which
    is all we actually need - the question is which trio beat which trio.

    It is also the cleanest region on the screen. The tab is a small solid fill with no
    player name, no avatar and no watermark anywhere near it, unlike the banner.

    The panel labels themselves ("Ally" / "Enemy") are NEVER read: in spectate they mean
    whichever side you bet on and they flip between matches. Only the tint is used.

    Returns None when neither tab is clearly tinted, so the caller can fall back.
    """
    top = np.median(
        frame[_TOP_TAB[0] : _TOP_TAB[1], _TAB_X[0] : _TAB_X[1]].reshape(-1, 3), axis=0
    )
    bottom = np.median(
        frame[_BOTTOM_TAB[0] : _BOTTOM_TAB[1], _TAB_X[0] : _TAB_X[1]].reshape(-1, 3),
        axis=0,
    )
    top_orange = float(top[2] - top[0])
    bottom_orange = float(bottom[2] - bottom[0])
    # A frame smaller than the probe regions slices to empty, and np.median of an empty
    # array is nan. Every comparison with nan is False, so without this guard the function
    # would fall through and return a side for a frame it never actually looked at.
    if math.isnan(top_orange) or math.isnan(bottom_orange):
        return None
    if abs(top_orange - bottom_orange) < _WINNER_COLOUR_MIN_DELTA:
        return None
    # The top panel is the first three heroes, which the cell registry labels "left".
    return "left" if top_orange > bottom_orange else "right"


def _winner_by_colour(frame: np.ndarray) -> str | None:
    """Which header half is orange. The winning side is tinted orange, the loser blue.

    Returns None when neither half is clearly tinted, so the caller can fall back to OCR
    rather than guessing from a weak difference.
    """
    # TOP strip only. Player names are vertically CENTRED in the banner, so a long name
    # bleeds sideways through the middle rows but never reaches the top edge. Combined
    # with the x windows below this keeps the probe clear of names, avatars and the sash
    # boundary at once.
    band = frame[_PROBE_BAND[0] : _PROBE_BAND[1]]
    # MEDIAN, not mean. The "Victory" / "Defeat" watermark is a lighter shade painted over
    # the sash, and it sits in the middle of each half - exactly where these probes are. A
    # mean is dragged by those lighter pixels; the median ignores them because they are a
    # minority of the window, and the sash itself is a solid fill.
    left = np.median(
        band[:, _PROBE_LEFT[0] : _PROBE_LEFT[1]].reshape(-1, 3), axis=0
    )
    right = np.median(
        band[:, _PROBE_RIGHT[0] : _PROBE_RIGHT[1]].reshape(-1, 3), axis=0
    )
    # index 2 is red, index 0 is blue in BGR. Positive means orange-tinted.
    left_orange = float(left[2] - left[0])
    right_orange = float(right[2] - right[0])
    if math.isnan(left_orange) or math.isnan(right_orange):
        return None
    if abs(left_orange - right_orange) < _WINNER_COLOUR_MIN_DELTA:
        return None
    return "left" if left_orange > right_orange else "right"


def _read_winner(frame: np.ndarray, ocr: OCRBackend) -> str | None:
    """Which side the Defeat/Victory banner declares the winner.

    Reads the whole band in one pass (see _WINNER_BAND_Y1 - splitting it loses the
    text entirely) and recovers the side either from word order in a merged block
    ('DefeatVictory' - reading order is left-to-right, so whichever word comes first
    is on that side) or, when only one word survived OCR, from that block's horizontal
    position relative to the halfway line.
    """
    # Colour FIRST. "Victory" and "Defeat" are faint watermark-style text and OCR misses
    # them entirely on some frames - on a 2026-07-26 capture the header band returned only
    # the player names, and the OCR path then produced the WRONG side. The winning half is
    # tinted orange and the losing half blue, which is a strong signal and was correct on
    # all four frames with independently known winners.
    winner = _winner_by_panel_tint(frame)
    if winner is not None:
        return winner

    winner = _winner_by_colour(frame)
    if winner is not None:
        return winner

    # Letter matching on the watermark was evaluated and REJECTED. "Victory" and "Defeat"
    # share only e and t, so partial text should disambiguate them - but across four
    # frames OCR never recovered "Victory" at all, only "feat"/"Defea". The one readable
    # word is also the one a player name can imitate: names sit in the same band, a long
    # one bleeds inward, and real examples cut both ways - "Tamau" contains a Defeat
    # letter while sitting on the winning side, and "Oipiq" contains Victory letters
    # while sitting on the losing side. It would add failure modes without adding
    # reliability, so the OCR fallback below looks for whole words only.

    blocks = ocr.detect_text_blocks(frame[0:_WINNER_BAND_Y1, :], ConfidenceValue(0.4))
    for block in blocks:
        text = block.text.lower()
        has_victory = "victory" in text
        has_defeat = "defeat" in text
        if has_victory and has_defeat:
            return "left" if text.index("victory") < text.index("defeat") else "right"
        if has_victory:
            return "left" if block.box.center.x < _HEADER_SPLIT_X else "right"
        if has_defeat:
            return "right" if block.box.center.x < _HEADER_SPLIT_X else "left"
    return None


def _read_players(frame: np.ndarray, ocr: OCRBackend) -> tuple[str | None, str | None]:
    y0, y1 = _HEADER_BAND
    names: list[str | None] = []
    for x0, x1 in (_HEADER_LEFT, _HEADER_RIGHT):
        blocks = ocr.detect_text_blocks(frame[y0:y1, x0:x1], ConfidenceValue(0.4))
        candidates = [
            b.text.strip()
            for b in blocks
            if b.text.strip().lower() not in {"defeat", "victory", ""}
        ]
        names.append(candidates[0] if candidates else None)
    return names[0], names[1]


def _read_stats(frame: np.ndarray, centre_y: int, ocr: OCRBackend) -> HeroStats:
    values: dict[str, int | None] = {}
    for name, (x0, x1) in _STAT_COLUMNS.items():
        crop = frame[centre_y - _STAT_HALF_HEIGHT : centre_y + _STAT_HALF_HEIGHT, x0:x1]
        blocks = ocr.detect_text_blocks(crop, ConfidenceValue(0.4))
        parsed = [parse_stat_number(b.text) for b in blocks]
        found = [p for p in parsed if p is not None]
        values[name] = found[0] if found else None
    return HeroStats(values["sword"], values["heart"], values["shield"])


def read_summary(
    frame: np.ndarray,
    cfg: SolsticeConfig,
    library: IconLibrary,
    ocr: OCRBackend,
) -> SummaryRead:
    """Parse one summary frame. Pure: no device, no taps, no persistence."""
    winner = _read_winner(frame, ocr)
    left_player, right_player = _read_players(frame, ocr)

    heroes: list[SummaryHero] = []
    for cell in sorted(
        cfg.cells(CELL_TYPE), key=lambda c: (c.side or "", c.slot or 0)
    ):
        result = identify_cell(extract_cell(frame, cell), CELL_TYPE, library, cfg)
        centre_y = (cell.y0 + cell.y1) // 2
        heroes.append(
            SummaryHero(
                side=cell.side or "",
                slot=cell.slot or 0,
                slug=result.slug,
                art_ref=result.art_ref,
                score=result.score,
                margin=result.margin,
                stats=_read_stats(frame, centre_y, ocr),
            )
        )
    return SummaryRead(winner, left_player, right_player, heroes)
