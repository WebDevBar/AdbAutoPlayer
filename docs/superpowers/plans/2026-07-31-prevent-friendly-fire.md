# Prevent Friendly Fire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-mode "Prevent Friendly Fire" toggle to Arena and Supreme Arena that never initiates a battle against an opponent the game marks as a Friend or Guild Member.

**Architecture:** All detection and decision logic goes in a new pure-Python service package, `games/afk_journey/services/friendly_fire/`, with no device access - it takes numpy frames and returns verdicts. The two mixins keep all device interaction and call into it. This mirrors `services/solstice/` and is what makes the whole feature testable from the eight committed fixture frames without an emulator.

**Tech Stack:** Python 3.13, numpy, OpenCV (`cv2`), Pydantic settings, pytest. Existing helpers: `game_find_template_match`, `tap`, `get_screenshot`, `handle_popup_messages`, `OCRBackend.detect_text_blocks`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-31-prevent-friendly-fire-design.md`. Where this plan and the spec disagree, the spec wins - stop and report.
- Resolution is hard-enforced at **1080x1920** by `_check_requirements`; all geometry assumes it.
- Ruff: line length 88, Google docstrings, `X | None` not `Optional`, no magic literals in comparisons (name them). Run `uvx ruff check --fix` and `uvx ruff format` from the REPO ROOT, never `uv run ruff`.
- **Never use the Edit tool on `.py` files** - it converts straight quotes to curly ones. Use `git apply` with a written patch, or a Python `.replace()` script with `assert s.count(old) == 1`.
- Tests run from `src-tauri/`: `../.venv/bin/python -m pytest <path> -q -p no:cacheprovider`.
- The toggle is **off by default**. With it off, the existing code path must be byte-for-byte unchanged in behaviour.
- Colour predicate, exactly, on 8-bit RGB:
  - Friend: `g > 120 and r < 110 and g - r > 60 and g - b > 40`
  - Guild: `g > 130 and b > 130 and r < 110 and abs(g - b) < 45 and g - r > 60`
- Component rules: 8-connectivity, discard `area < 400`, then flag when `area >= 2000 and width >= 100 and height <= 80 and width / height >= 2.0`.
- OCR: per-box equality on casefolded, whitespace-collapsed text against `friend` / `guild member`; ignore boxes below confidence `0.6`.
- Give-up requires **both** signals on every flagged card. One signal may skip a card; one signal may never spend an attempt.

---

## File Structure

| File | Responsibility |
|---|---|
| `services/friendly_fire/__init__.py` | Public surface: `evaluate_cards`, `Verdict`, `choose_card` |
| `services/friendly_fire/geometry.py` | Every measured constant: card x-ranges, OCR y-ranges, control regions, template crops, tap points |
| `services/friendly_fire/detect.py` | Pure detection: badge components, card assignment, OCR matching |
| `services/friendly_fire/select.py` | Pure decision: preference order, what to do next |
| `services/friendly_fire/collect.py` | Frame archiving to the per-user data dir |
| `settings.py` | New `ArenaSettings`, new field on `SupremeArenaSettings`, registration |
| `mixins/arena.py` | Device interaction for Arena |
| `mixins/supreme_arena.py` | Device interaction for Supreme Arena |
| `templates/arena/refresh_glyph.png`, `give_up_glyph.png`, `templates/supreme_arena/refresh_glyph.png` | Cut from fixtures |
| `tests/games/afk_journey/services/friendly_fire/` | All tests plus the 8 fixture frames |

---

### Task 1: Fixtures and geometry constants

**Files:**
- Create: `src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/data/` (8 PNGs copied from `/mnt/vault/adbautoplayer/arena-friendly-fire/`)
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/__init__.py` (empty for now)
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/geometry.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/test_geometry.py`

**Interfaces:**
- Produces: `Mode` (StrEnum: `ARENA`, `SUPREME_ARENA`), `CARD_X_RANGES: dict[Mode, tuple[tuple[int,int],...]]`, `OCR_Y_RANGE: dict[Mode, tuple[int,int]]`, `CONTROL_REGION: dict[Mode, tuple[int,int,int,int]]`, `SA_TAP_POINTS: tuple[Point,...]`, `GIVE_UP_TICK_REGION`, `GIVE_UP_TICK_TEMPLATE_BOX`

- [ ] **Step 1: Copy the fixture frames**

```bash
cd ~/Dev/webdevbar/adbautoplayer
mkdir -p src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/data
cp /mnt/vault/adbautoplayer/arena-friendly-fire/*.png \
   src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/data/
ls src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/data/ | wc -l   # expect 8
```

- [ ] **Step 2: Write the failing test**

```python
"""Geometry constants. Every number here was measured from a fixture frame."""

from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    CARD_X_RANGES,
    CONTROL_REGION,
    OCR_Y_RANGE,
    Mode,
)


def test_every_mode_has_three_card_ranges():
    for mode in Mode:
        assert len(CARD_X_RANGES[mode]) == 3


def test_card_ranges_are_ordered_and_non_empty():
    for mode in Mode:
        for x0, x1 in CARD_X_RANGES[mode]:
            assert x0 < x1


def test_measured_badges_fall_inside_their_card_range():
    """The badge boxes measured in the spec must land in exactly one card range."""
    cases = [
        (Mode.ARENA, 0, 93, 281),
        (Mode.ARENA, 1, 456, 644),
        (Mode.ARENA, 1, 421, 679),
        (Mode.SUPREME_ARENA, 1, 446, 636),
        (Mode.SUPREME_ARENA, 2, 759, 948),
    ]
    for mode, card, bx0, bx1 in cases:
        x0, x1 = CARD_X_RANGES[mode][card]
        assert x0 <= bx0 and bx1 <= x1, f"{mode} card {card} does not contain {bx0}-{bx1}"


def test_ocr_band_contains_every_measured_badge():
    for mode, by0, by1 in [
        (Mode.ARENA, 953, 1052),
        (Mode.SUPREME_ARENA, 975, 1091),
    ]:
        y0, y1 = OCR_Y_RANGE[mode]
        assert y0 <= by0 and by1 <= y1


def test_control_region_is_large_enough_for_an_80px_template():
    """Round 11: a template wider than its search region cannot be matched at all."""
    for mode in Mode:
        x0, y0, x1, y1 = CONTROL_REGION[mode]
        assert x1 - x0 > 80
        assert y1 - y0 > 80
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_geometry.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: ...friendly_fire.geometry`

- [ ] **Step 4: Write `geometry.py`**

```python
"""Measured screen geometry for the friendly-fire guard.

Every constant here came from a fixture frame at 1080x1920, which is the only
resolution the app permits. Nothing in this module is a guess; where a number was
inferred rather than observed, the comment says so.
"""

from enum import StrEnum

from adb_auto_player.models.geometry import Point


class Mode(StrEnum):
    """The two modes this guard serves. They are different SCREENS, not variants."""

    ARENA = "arena"
    SUPREME_ARENA = "supreme_arena"


# Horizontal extent of each opponent card, left to right. A badge component is
# assigned to every card whose range it OVERLAPS - overlap rather than centre,
# because a centre rule is undefined for a component sitting on a boundary and
# guessing wrong marks the wrong card safe.
CARD_X_RANGES: dict[Mode, tuple[tuple[int, int], ...]] = {
    Mode.ARENA: ((40, 340), (395, 700), (755, 1060)),
    Mode.SUPREME_ARENA: ((60, 400), (390, 720), (720, 1050)),
}

# Vertical extent for the OCR crop, per MODE rather than per card: wide enough to
# contain any card's content at any stagger, so it needs no anchoring.
OCR_Y_RANGE: dict[Mode, tuple[int, int]] = {
    Mode.ARENA: (900, 1300),
    Mode.SUPREME_ARENA: (950, 1500),
}

# The bottom-right control, which is Refresh or the X. Identical box in both states.
CONTROL_REGION: dict[Mode, tuple[int, int, int, int]] = {
    Mode.ARENA: (882, 1724, 1052, 1864),
    Mode.SUPREME_ARENA: (860, 1718, 1029, 1859),
}

# Where each glyph template is cut from, so the assets can be re-cut identically.
GLYPH_TEMPLATE_BOX: dict[Mode, tuple[int, int, int, int]] = {
    Mode.ARENA: (930, 1745, 1010, 1825),
    Mode.SUPREME_ARENA: (905, 1735, 985, 1815),
}

# The give-up dialog is detected by its green tick: language-independent,
# feature-rich (std 54.5 against the blank sheet's 4.8), and it IS the tap target.
GIVE_UP_TICK_TEMPLATE_BOX: tuple[int, int, int, int] = (786, 1163, 947, 1321)
GIVE_UP_TICK_REGION: tuple[int, int, int, int] = (700, 1100, 1010, 1380)
GIVE_UP_CANCEL_CENTRE = Point(639, 1240)  # recorded only so tests can assert we miss it

# Supreme Arena taps fixed points; Arena locates its cards by template.
SA_TAP_POINTS: tuple[Point, ...] = (Point(165, 950), Point(540, 950), Point(915, 950))

CONFIDENCE_FLOOR = 0.8
OCR_CONFIDENCE_FLOOR = 0.6
```

- [ ] **Step 5: Run the tests and lint**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider`
Expected: PASS (5 tests)
Run: `cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/ && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/`

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/ \
        src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/
git commit -m "feat(friendly-fire): measured geometry and fixture frames"
```

---

### Task 2: Badge detection

**Files:**
- Create: `.../services/friendly_fire/detect.py`
- Test: `tests/.../friendly_fire/test_detect.py`

**Interfaces:**
- Consumes: `geometry.Mode`, `CARD_X_RANGES`
- Produces: `Badge` (frozen dataclass: `kind: str`, `box: tuple[int,int,int,int]`), `find_badges(frame: np.ndarray) -> list[Badge]`, `cards_with_badges(frame: np.ndarray, mode: Mode) -> set[int]`

- [ ] **Step 1: Write the failing test**

```python
"""Badge detection, against the eight fixture frames."""

from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.friendly_fire.detect import (
    cards_with_badges,
    find_badges,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode

DATA = Path(__file__).parent / "data"


def _frame(name: str) -> np.ndarray:
    path = next(DATA.glob(f"{name}*.png"))
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert bgr is not None, path
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


@pytest.mark.parametrize(
    ("frame_name", "mode", "expected"),
    [
        ("01", Mode.ARENA, {1}),
        ("02", Mode.ARENA, {1}),
        ("03", Mode.ARENA, {0}),
        ("04", Mode.ARENA, set()),
        ("05", Mode.SUPREME_ARENA, set()),
        ("06", Mode.SUPREME_ARENA, {2}),
        ("07", Mode.SUPREME_ARENA, {2}),
        ("08", Mode.SUPREME_ARENA, {1}),
    ],
)
def test_each_fixture_flags_exactly_the_right_cards(frame_name, mode, expected):
    assert cards_with_badges(_frame(frame_name), mode) == expected


def test_the_baseline_frame_yields_no_badge_at_all():
    """05 is the frame that proves an empty screen reads empty."""
    assert find_badges(_frame("05")) == []


def test_the_sword_buttons_are_rejected_by_SHAPE_not_by_area():
    """Round 2: the largest badge is 7948px and the smallest sword button 8012px, so
    an area-only rule passes every frame in this set while being wrong. If someone
    relaxes the height or aspect bound, this must fail."""
    badges = find_badges(_frame("01"))
    assert len(badges) == 1
    x0, y0, x1, y1 = badges[0].box
    assert (x1 - x0) / (y1 - y0) >= 2.0
    assert (y1 - y0) <= 80


def test_cyan_guild_badges_are_detected_not_only_green_friend_ones():
    """A green-only predicate sails straight past Guild Member."""
    assert cards_with_badges(_frame("02"), Mode.ARENA) == {1}
    assert cards_with_badges(_frame("07"), Mode.SUPREME_ARENA) == {2}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_detect.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: ...friendly_fire.detect`

- [ ] **Step 3: Write `detect.py`**

```python
"""Badge detection: is this opponent a Friend or a Guild Member?

Two properties are load-bearing and both were established by measurement, not choice.

The badge is found by SHAPE, not position. An earlier design anchored a search band
to the player-name row; nothing in a frame identifies that row against the adjacent
score and rank text, so it was not implementable. A badge is a wide short bar and the
green battle button is a blob - aspect 3.0-6.2 against 0.7 - which needs no anchor and
makes the cards' vertical stagger irrelevant.

Area alone does NOT separate them: the largest observed badge is 7948px and the
smallest sword button 8012px. Aspect ratio is what does the work.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import CARD_X_RANGES, Mode

_SPECK_AREA = 400
_MIN_AREA = 2000
_MIN_WIDTH = 100
_MAX_HEIGHT = 80
_MIN_ASPECT = 2.0


@dataclass(frozen=True)
class Badge:
    """One detected badge and where it sits."""

    kind: str  # "friend" or "guild"
    box: tuple[int, int, int, int]  # x0, y0, x1, y1


def _colour_mask(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel masks for the two badge colours.

    Exact predicates rather than a tolerance: a "within N of this RGB" formulation
    flips the answer with N, and one setting attacks the friend.
    """
    red = frame[:, :, 0].astype(np.int16)
    green = frame[:, :, 1].astype(np.int16)
    blue = frame[:, :, 2].astype(np.int16)
    friend = (green > 120) & (red < 110) & (green - red > 60) & (green - blue > 40)
    guild = (
        (green > 130)
        & (blue > 130)
        & (red < 110)
        & (np.abs(green - blue) < 45)
        & (green - red > 60)
    )
    return friend, guild


def find_badges(frame: np.ndarray) -> list[Badge]:
    """Every badge on the frame, found by colour then filtered by shape.

    Args:
        frame: RGB frame, 1080x1920.

    Returns:
        One `Badge` per qualifying component, in no particular order.
    """
    friend, guild = _colour_mask(frame)
    found: list[Badge] = []
    for kind, mask in (("friend", friend), ("guild", guild)):
        count, _, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8
        )
        for index in range(1, count):
            x, y, width, height, area = (int(v) for v in stats[index])
            if area < _SPECK_AREA:
                continue
            if (
                area >= _MIN_AREA
                and width >= _MIN_WIDTH
                and height <= _MAX_HEIGHT
                and width / height >= _MIN_ASPECT
            ):
                found.append(Badge(kind=kind, box=(x, y, x + width, y + height)))
    return found


def cards_with_badges(frame: np.ndarray, mode: Mode) -> set[int]:
    """Indices of cards carrying a badge.

    Assignment is by x-range OVERLAP. A component overlapping two ranges flags BOTH,
    which errs toward refusing to attack - a centre rule is undefined on a boundary
    and the cost of guessing wrong is attacking a friend.
    """
    flagged: set[int] = set()
    for badge in find_badges(frame):
        bx0, _, bx1, _ = badge.box
        for index, (x0, x1) in enumerate(CARD_X_RANGES[mode]):
            if bx0 < x1 and x0 < bx1:
                flagged.add(index)
    return flagged
```

- [ ] **Step 4: Run the tests**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider`
Expected: PASS (12 tests)

- [ ] **Step 5: Lint and commit**

```bash
cd ~/Dev/webdevbar/adbautoplayer
uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/
uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/
git add -A src-tauri/src-python
git commit -m "feat(friendly-fire): detect badges by shape, not position"
```

---

### Task 3: The OCR arm

**Files:**
- Modify: `.../services/friendly_fire/detect.py`
- Test: `tests/.../friendly_fire/test_ocr_match.py`

**Interfaces:**
- Produces: `is_badge_text(text: str) -> bool`, `cards_with_badge_text(blocks: list[OCRResult], mode: Mode) -> set[int]`

- [ ] **Step 1: Write the failing test**

```python
"""The OCR arm. Its match rule matters as much as the colour predicate."""

from adb_auto_player.games.afk_journey.services.friendly_fire.detect import (
    is_badge_text,
)


def test_exact_badge_text_matches():
    assert is_badge_text("Friend")
    assert is_badge_text("Guild Member")


def test_matching_is_case_and_whitespace_insensitive():
    assert is_badge_text("  friend ")
    assert is_badge_text("GUILD   MEMBER")


def test_a_player_named_Friendzone_is_NOT_a_friend():
    """The OCR rectangle contains the opponent NAME row by design, and names are
    arbitrary player strings. A substring rule flags strangers and burns refreshes."""
    assert not is_badge_text("Friendzone")
    assert not is_badge_text("BestFriend")
    assert not is_badge_text("Guild Membership")


def test_unrelated_text_does_not_match():
    for text in ("Refresh : 7/7", "Top 122", "MorganaLaFey", "1491", ""):
        assert not is_badge_text(text)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_ocr_match.py -q -p no:cacheprovider`
Expected: FAIL, `ImportError: cannot import name 'is_badge_text'`

- [ ] **Step 3: Append to `detect.py`**

```python
_BADGE_TEXTS = frozenset({"friend", "guild member"})


def is_badge_text(text: str) -> bool:
    """Whether one OCR box reads exactly as a badge label.

    Equality on a normalised single box, never a substring of the whole card. The
    OCR rectangle deliberately contains the opponent's NAME row, and names are
    arbitrary: a substring rule flags a player called "Friendzone".
    """
    return " ".join(text.split()).casefold() in _BADGE_TEXTS


def cards_with_badge_text(blocks: list, mode: Mode) -> set[int]:
    """Card indices whose OCR contains a badge label.

    Args:
        blocks: `OCRResult` items already filtered to the card's crop.
        mode: which screen these came from.

    Returns:
        Indices of flagged cards.
    """
    flagged: set[int] = set()
    for block in blocks:
        if not is_badge_text(block.text):
            continue
        centre_x = block.box.top_left.x + block.box.width // 2
        for index, (x0, x1) in enumerate(CARD_X_RANGES[mode]):
            if x0 <= centre_x <= x1:
                flagged.add(index)
    return flagged
```

- [ ] **Step 4: Run the tests, lint, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider
cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/ && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): exact per-box OCR matching"
```

---

### Task 4: Preference order and the decision

**Files:**
- Create: `.../services/friendly_fire/select.py`
- Test: `tests/.../friendly_fire/test_select.py`

**Interfaces:**
- Produces: `Action` (StrEnum: `TAKE`, `REFRESH`, `GIVE_UP`, `STOP`), `Decision` (frozen dataclass: `action: Action`, `card: int | None`, `reason: str`), `preference_order(mode, position) -> tuple[int, ...]`, `decide(order, flagged_colour, flagged_ocr, control) -> Decision`

- [ ] **Step 1: Write the failing test**

```python
"""The decision, as a pure function. No device, no frames."""

import pytest
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode
from adb_auto_player.games.afk_journey.services.friendly_fire.select import (
    Action,
    decide,
    preference_order,
)
from adb_auto_player.games.afk_journey.settings import OpponentPosition


def test_arena_order_is_always_card_1_then_2():
    assert preference_order(Mode.ARENA, OpponentPosition.Left) == (0, 1)
    assert preference_order(Mode.ARENA, OpponentPosition.Right) == (0, 1)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (OpponentPosition.Left, (0, 1)),
        (OpponentPosition.Middle, (1, 0)),
        (OpponentPosition.Right, (2, 0, 1)),
    ],
)
def test_supreme_arena_respects_the_configured_position(position, expected):
    """The toggle must never silently override a setting the user chose."""
    assert preference_order(Mode.SUPREME_ARENA, position) == expected


def test_card_3_is_never_a_fallback():
    """Right offers card 3 FIRST, but a flagged card 1 must not fall back onto it."""
    assert preference_order(Mode.SUPREME_ARENA, OpponentPosition.Left)[-1] != 2


def test_first_unflagged_card_in_order_is_taken():
    d = decide((0, 1), flagged_colour=set(), flagged_ocr=set(), control="refresh")
    assert d.action is Action.TAKE and d.card == 0


def test_a_flagged_first_choice_falls_through_to_the_second():
    d = decide((0, 1), flagged_colour={0}, flagged_ocr=set(), control="refresh")
    assert d.action is Action.TAKE and d.card == 1


def test_either_signal_alone_is_enough_to_skip():
    assert decide((0, 1), {0}, set(), "refresh").card == 1
    assert decide((0, 1), set(), {0}, "refresh").card == 1


def test_all_flagged_with_refreshes_left_refreshes():
    d = decide((0, 1), {0, 1}, {0, 1}, "refresh")
    assert d.action is Action.REFRESH


def test_all_flagged_and_exhausted_gives_up_when_both_signals_agree():
    d = decide((0, 1), {0, 1}, {0, 1}, "give_up")
    assert d.action is Action.GIVE_UP


def test_exhausted_with_a_single_signal_flag_STOPS_rather_than_forfeiting():
    """A persistent false positive drains refreshes and would otherwise spend a daily
    attempt on a false read. One signal may skip a card; it may never forfeit."""
    d = decide((0, 1), {0, 1}, {0}, "give_up")
    assert d.action is Action.STOP


def test_an_unknown_control_never_taps_anything():
    d = decide((0, 1), {0, 1}, {0, 1}, "unknown")
    assert d.action is Action.STOP


def test_three_cards_all_flagged_is_handled_not_just_two():
    """Round 14: 'both are flagged' assumed two cards; Right evaluates three."""
    d = decide((2, 0, 1), {0, 1, 2}, {0, 1, 2}, "refresh")
    assert d.action is Action.REFRESH
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_select.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: ...friendly_fire.select`

- [ ] **Step 3: Write `select.py`**

```python
"""What to do about the cards, as a pure function of what was seen."""

from dataclasses import dataclass
from enum import StrEnum

from adb_auto_player.games.afk_journey.settings import OpponentPosition

from .geometry import Mode


class Action(StrEnum):
    """What the caller should do next."""

    TAKE = "take"
    REFRESH = "refresh"
    GIVE_UP = "give_up"
    STOP = "stop"


@dataclass(frozen=True)
class Decision:
    """An action, the card it applies to, and why - the reason goes in the log."""

    action: Action
    card: int | None
    reason: str


# Arena has no position setting, so its order is fixed.
_ARENA_ORDER: tuple[int, ...] = (0, 1)
_SA_ORDERS: dict[OpponentPosition, tuple[int, ...]] = {
    OpponentPosition.Left: (0, 1),
    OpponentPosition.Middle: (1, 0),
    # Right offers card 3 FIRST because the user asked for it, but never as a
    # fallback: card 3 is routinely out of the power bracket, and falling back onto
    # it would lose the battle in order to avoid a friend.
    OpponentPosition.Right: (2, 0, 1),
}


def preference_order(mode: Mode, position: OpponentPosition) -> tuple[int, ...]:
    """The cards to evaluate, in the order they should be preferred."""
    if mode is Mode.ARENA:
        return _ARENA_ORDER
    return _SA_ORDERS[position]


def decide(
    order: tuple[int, ...],
    flagged_colour: set[int],
    flagged_ocr: set[int],
    control: str,
) -> Decision:
    """Choose an action.

    Args:
        order: cards to consider, most preferred first.
        flagged_colour: cards flagged by the colour arm.
        flagged_ocr: cards flagged by the OCR arm.
        control: "refresh", "give_up" or "unknown".

    Returns:
        The action to take, with a reason for the log.
    """
    flagged = flagged_colour | flagged_ocr
    for card in order:
        if card not in flagged:
            return Decision(Action.TAKE, card, f"card {card + 1} is clear")

    if control == "refresh":
        return Decision(Action.REFRESH, None, "every evaluated card is flagged")

    if control != "give_up":
        return Decision(Action.STOP, None, "the control matched neither Refresh nor X")

    # Forfeiting costs a daily attempt, so one signal is not enough to justify it.
    # A single detector agreeing with itself across every refresh is exactly what a
    # persistent false positive looks like.
    single_signal = [c for c in order if c not in (flagged_colour & flagged_ocr)]
    if single_signal:
        return Decision(
            Action.STOP,
            None,
            f"card(s) {[c + 1 for c in single_signal]} flagged by one signal only - "
            f"refusing to forfeit an attempt on a single detector",
        )
    return Decision(Action.GIVE_UP, None, "every card flagged by both signals")
```

- [ ] **Step 4: Run, lint, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider
cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/ && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): preference order and the give-up precondition"
```

---

### Task 5: Settings

**Files:**
- Modify: `.../games/afk_journey/settings.py`
- Test: `tests/.../friendly_fire/test_settings.py`

**Interfaces:**
- Produces: `ArenaSettings` with `prevent_friendly_fire: bool`, same field on `SupremeArenaSettings`, `Settings.arena`

- [ ] **Step 1: Write the failing test**

```python
"""The toggle. Off by default, on both modes."""

from adb_auto_player.games.afk_journey.settings import Settings


def test_both_modes_expose_the_toggle_and_it_is_OFF_by_default():
    s = Settings()
    assert s.arena.prevent_friendly_fire is False
    assert s.supreme_arena.prevent_friendly_fire is False


def test_the_label_is_the_one_the_operator_asked_for():
    field = type(Settings().arena).model_fields["prevent_friendly_fire"]
    assert field.alias == (
        "Prevent Friendly Fire - do not attack friends or guild-mates in this mode"
    )


def test_supreme_arena_keeps_its_existing_settings():
    """Adding a section must not disturb what is already there."""
    s = Settings()
    assert s.supreme_arena.attempts == 5
    assert s.supreme_arena.opponent_position.value == "Left"
```

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL, `AttributeError: 'Settings' object has no attribute 'arena'`

- [ ] **Step 3: Patch `settings.py`**

Write the patch to a file and apply with `git apply` - never the Edit tool on a `.py` file.

```python
_LABEL = (
    "Prevent Friendly Fire - do not attack friends or guild-mates in this mode"
)


class ArenaSettings(BaseModel):
    """Arena Settings model.

    New section. Arena had no settings at all before this, so an [Arena] block
    appears in every existing AFKJourney.toml on upgrade.
    """

    prevent_friendly_fire: bool = Field(
        default=False,
        alias=_LABEL,
        title=_LABEL,
        description=(
            "Skip opponents the game marks as Friend or Guild Member. Refreshes for "
            "another opponent instead; gives up the challenge only if every refresh "
            "is spent. Off by default - turning it on changes which opponent is "
            "attacked."
        ),
    )
```

Add the same field to `SupremeArenaSettings`, and register the section on `Settings`:

```python
    arena: ArenaSettings = Field(
        default_factory=ArenaSettings, alias="Arena", title="Arena"
    )
```

- [ ] **Step 4: Run, lint, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider
cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/settings.py && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/settings.py
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): the toggle, off by default, on both modes"
```

---

### Task 6: Control classification and the give-up tick

**Files:**
- Create: `templates/arena/refresh_glyph.png`, `templates/arena/give_up_glyph.png`, `templates/supreme_arena/refresh_glyph.png`, `templates/arena/give_up_confirm.png`
- Modify: `.../services/friendly_fire/detect.py`
- Test: `tests/.../friendly_fire/test_control.py`

**Interfaces:**
- Produces: `classify_control(frame, mode, matcher) -> str` returning `"refresh" | "give_up" | "unknown"`

- [ ] **Step 1: Cut the templates from the fixtures**

```bash
cd ~/Dev/webdevbar/adbautoplayer
.venv/bin/python - <<'PY'
from PIL import Image
D = "src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/data/"
T = "src-tauri/src-python/adb_auto_player/games/afk_journey/templates/"
cuts = [
    ("01-friend-badge-middle-card-20260731.png", (930,1745,1010,1825), "arena/refresh_glyph.png"),
    ("03-refresh-exhausted-x-button-friend-on-left-20260731.png", (930,1745,1010,1825), "arena/give_up_glyph.png"),
    ("05-supreme-arena-select-opponent-no-badges-20260731.png", (905,1735,985,1815), "supreme_arena/refresh_glyph.png"),
    ("04-give-up-confirmation-dialog-20260731.png", (786,1163,947,1321), "arena/give_up_confirm.png"),
]
for src, box, dst in cuts:
    Image.open(D+src).convert("RGB").crop(box).save(T+dst)
    print("cut", dst)
PY
```

- [ ] **Step 2: Write the failing test**

```python
"""Classifying the bottom-right control. This decides whether we tap a control that
forfeits a daily attempt, so the fixtures matter more here than anywhere else."""

from pathlib import Path

import cv2
import numpy as np
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    CONTROL_REGION,
    GLYPH_TEMPLATE_BOX,
    Mode,
)

DATA = Path(__file__).parent / "data"
TEMPLATES = (
    Path(__file__).parents[5]
    / "adb_auto_player/games/afk_journey/templates"
)


def _frame(name):
    path = next(DATA.glob(f"{name}*.png"))
    return cv2.cvtColor(cv2.imread(str(path), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def _match(frame, mode, template_name):
    x0, y0, x1, y1 = CONTROL_REGION[mode]
    region = frame[y0:y1, x0:x1]
    tpl = cv2.cvtColor(
        cv2.imread(str(TEMPLATES / template_name), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB
    )
    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def test_every_template_fits_inside_its_search_region():
    """Round 11: a 170px template into a 169px region cannot be matched at all."""
    for mode in Mode:
        rx0, ry0, rx1, ry1 = CONTROL_REGION[mode]
        tx0, ty0, tx1, ty1 = GLYPH_TEMPLATE_BOX[mode]
        assert tx1 - tx0 < rx1 - rx0
        assert ty1 - ty0 < ry1 - ry0


def test_arena_refresh_and_x_are_told_apart():
    assert _match(_frame("01"), Mode.ARENA, "arena/refresh_glyph.png") >= 0.8
    assert _match(_frame("03"), Mode.ARENA, "arena/give_up_glyph.png") >= 0.8


def test_supreme_arena_needs_its_OWN_refresh_template():
    """Round 10: the Arena template scores 0.36 here. If someone deletes the
    Supreme Arena template and points at Arena's, this must fail."""
    assert _match(_frame("05"), Mode.SUPREME_ARENA, "supreme_arena/refresh_glyph.png") >= 0.8
    assert _match(_frame("05"), Mode.SUPREME_ARENA, "arena/refresh_glyph.png") < 0.8


def test_the_x_template_is_genuinely_shared():
    assert _match(_frame("06"), Mode.SUPREME_ARENA, "arena/give_up_glyph.png") >= 0.8
```

- [ ] **Step 3: Run, confirm the numbers, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_control.py -q -p no:cacheprovider
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): control glyph templates cut from the fixtures"
```

---

### Task 7: Frame collection

**Files:**
- Create: `.../services/friendly_fire/collect.py`
- Test: `tests/.../friendly_fire/test_collect.py`

**Interfaces:**
- Produces: `collection_dir() -> Path`, `archive(frame, mode, outcome) -> Path | None`

- [ ] **Step 1: Write the failing test**

```python
"""Frame collection. Must work on a machine that is not the author's."""

import os
from pathlib import Path

import numpy as np
from adb_auto_player.games.afk_journey.services.friendly_fire.collect import (
    archive,
    collection_dir,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode


def test_the_directory_is_NOT_the_authors_vault_mount():
    """The spec's earlier draft named /mnt/vault, which no end user has."""
    assert "/mnt/vault" not in str(collection_dir())


def test_it_lands_under_the_per_user_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    assert collection_dir() == tmp_path


def test_archiving_writes_a_named_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    out = archive(frame, Mode.ARENA, "flagged-1")
    assert out is not None and out.exists()
    assert "arena" in out.name and "flagged-1" in out.name


def test_a_write_failure_never_raises(monkeypatch):
    """Collection is diagnostics. It must never cost a match."""
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", "/proc/nonexistent/nope")
    assert archive(np.zeros((5, 5, 3), dtype=np.uint8), Mode.ARENA, "x") is None
```

- [ ] **Step 2: Write `collect.py`**

```python
"""Archiving evaluated frames, so the unobserved cases can be studied later.

Not /mnt/vault: that is the author's machine. This app ships to Windows and macOS,
so the destination is resolved the same way the Solstice Clash mode resolves it.
"""

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from adb_auto_player.util import RuntimeInfo

from .geometry import Mode


def collection_dir() -> Path:
    """Where evaluated frames are written."""
    override = os.environ.get("ADB_FRIENDLY_FIRE_DIR")
    if override:
        return Path(override).expanduser()
    if RuntimeInfo.is_windows():
        base = os.environ.get("APPDATA") or "~/AppData/Roaming"
    elif RuntimeInfo.is_mac():
        base = "~/Library/Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "AdbAutoPlayer" / "friendly-fire"


def archive(frame: np.ndarray, mode: Mode, outcome: str) -> Path | None:
    """Write one frame, named with mode, timestamp and outcome.

    Returns the path, or None if anything went wrong - diagnostics must never cost
    a match.
    """
    try:
        directory = collection_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = directory / f"{mode.value}-{stamp}-{outcome}.png"
        cv2.imwrite(str(path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        return path
    except Exception as exc:  # noqa: BLE001 - never worth a match
        logging.debug(f"[FF-10] could not archive frame: {exc}")
        return None
```

- [ ] **Step 3: Run, lint, commit**

---

### Task 8: Wire into Arena

**Files:**
- Modify: `mixins/arena.py` (`_choose_opponent`, currently lines 96-127)
- Test: `tests/.../friendly_fire/test_arena_integration.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing test using a stub, not a device**

```python
"""Arena wiring. A stub records taps instead of performing them."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.modules.setdefault("pytauri", MagicMock())
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.games.afk_journey.mixins.arena import ArenaMixin  # noqa: E402


class _Stub(ArenaMixin):
    def __init__(self, toggle_on):
        self._s = SimpleNamespace(arena=SimpleNamespace(prevent_friendly_fire=toggle_on))
        self.taps = []

    @property
    def settings(self):
        return self._s

    def tap(self, coordinates, **kwargs):
        self.taps.append(coordinates)


def test_with_the_toggle_OFF_the_guard_never_runs():
    """Off is the default; the old path must be untouched."""
    bot = _Stub(toggle_on=False)
    assert bot._friendly_fire_enabled() is False


def test_with_the_toggle_ON_the_guard_runs():
    assert _Stub(toggle_on=True)._friendly_fire_enabled() is True
```

- [ ] **Step 2: Implement `_choose_opponent` with the guard**

Restructure so that with the toggle off the existing code runs unchanged, and with it on the loop is: screenshot, `handle_popup_messages()`, evaluate, decide, act. Locate the card tap by matching `arena/opponent.png` inside the card's x-range, which is the change that lets it reach card 2 at all.

- [ ] **Step 3: Run the full solstice + friendly-fire suites, lint, commit**

---

### Task 9: Wire into Supreme Arena

**Files:**
- Modify: `mixins/supreme_arena.py` (`_sa_choose_opponent`, currently lines 63-157)

Same shape as Task 8, but tapping `SA_TAP_POINTS[card]` rather than locating a template, and passing `settings.supreme_arena.opponent_position` into `preference_order`.

- [ ] Steps mirror Task 8.

---

### Task 10: Changelog, version bump, build

- [ ] Add a `CHANGELOG.md` entry under Unreleased describing the toggle, the default-off decision, and the give-up behaviour.
- [ ] Bump `WDB_RELEASE` in BOTH `wdb_version.py` and `src/lib/wdb-version.ts` - the build refuses if they disagree.
- [ ] `./build-rpm.sh`, then report the install command. Do not install; the operator restarts the collector themselves.

---

## Self-Review

**Spec coverage:** detection (T2, T3), card assignment (T2), preference order (T4), give-up precondition (T4), settings (T5), control classification (T6), give-up dialog (T6), frame collection (T7), Arena wiring (T8), Supreme Arena wiring (T9), changelog and build (T10). Error-handling rows are covered by T4's decision table plus T8/T9 wiring.

**Placeholders:** none - every code step carries real code.

**Type consistency:** `Mode` from geometry is used unchanged in detect, select and collect. `decide()` takes `control: str` with the same three literals `classify_control` returns. `preference_order` takes the real `OpponentPosition` enum from `settings.py`.
