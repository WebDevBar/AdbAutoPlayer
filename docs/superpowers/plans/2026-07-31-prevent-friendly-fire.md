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
- **Everything works in BGR**, because that is what `get_screenshot()` returns
  (`_screenshot_mixin.py`: "Returns: np.ndarray: BGR screenshot"). Templates are loaded
  with `cv2.imread`, which is also BGR, and test fixtures are loaded with `cv2.imread`
  and **never converted**. An earlier draft specified RGB and converted in the tests
  only - the suite would have passed while production applied the predicate to swapped
  channels, which is total detection failure invisible to the tests.
- Colour predicate on 8-bit **BGR** (`frame[:, :, 0]` is blue, `[:, :, 2]` is red):
  - Friend: `g > 120 and r < 110 and g - r > 60 and g - b > 40`
  - Guild: `g > 130 and b > 130 and r < 110 and abs(g - b) < 45 and g - r > 60`
- Template confidence floor is **0.9, not 0.8**. Measured: the X template scores
  **0.8027** against the no-control region of frame 04, so a 0.8 floor misclassifies an
  unrelated screen as "exhausted". The real X scores 1.00 and Refresh 0.4252, so 0.9
  separates with margin.
- Component rules: 8-connectivity, discard `area < 400`, then flag when `area >= 2000 and width >= 100 and height <= 80 and width / height >= 2.0`.
- OCR: per-box equality on casefolded, whitespace-collapsed text against `friend` / `guild member`; ignore boxes below confidence `0.6`.
- Give-up requires **both** signals on every flagged card. One signal may skip a card; one signal may never spend an attempt.

---

## File Structure

| File | Responsibility |
|---|---|
| `services/friendly_fire/__init__.py` | Public surface: `evaluate`, `Action`, `Decision`, `Mode` |
| `services/friendly_fire/evaluate.py` | The orchestrator: colour + OCR + control -> one `Decision` |
| `services/friendly_fire/control.py` | Classifying the Refresh/X control and finding the give-up tick |
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

CONFIDENCE_FLOOR = 0.9  # 0.8 collides: the X scores 0.8027 on a no-control screen
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
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None, path
    return frame  # BGR, exactly as get_screenshot() delivers it - do NOT convert


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
    # BGR, because that is what the device gives us. Naming the channels rather
    # than indexing by convention is what stops the swap bug coming back.
    blue = frame[:, :, 0].astype(np.int16)
    green = frame[:, :, 1].astype(np.int16)
    red = frame[:, :, 2].astype(np.int16)
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
        frame: BGR frame, 1080x1920, straight from `get_screenshot()`.

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


def test_low_confidence_boxes_are_ignored():
    """A shaky read must not flag a card and drive the refresh/forfeit ladder."""
    from adb_auto_player.games.afk_journey.services.friendly_fire.detect import (
        card_has_badge_text,
    )
    from adb_auto_player.models import ConfidenceValue

    class _B:
        def __init__(self, text, conf):
            self.text = text
            self.confidence = ConfidenceValue(conf)

    assert card_has_badge_text([_B("Friend", 0.9)])
    assert not card_has_badge_text([_B("Friend", 0.4)])


def test_blocks_are_per_card_so_no_coordinate_mapping_is_needed():
    """An earlier draft compared crop-local x against full-screen card ranges."""
    from adb_auto_player.games.afk_journey.services.friendly_fire.detect import (
        card_has_badge_text,
    )
    from adb_auto_player.models import ConfidenceValue

    class _B:
        def __init__(self, text):
            self.text = text
            self.confidence = ConfidenceValue(0.9)

    assert not card_has_badge_text([_B("Bobo"), _B("Top 71")])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_ocr_match.py -q -p no:cacheprovider`
Expected: FAIL, `ImportError: cannot import name 'is_badge_text'`

- [ ] **Step 3: Append to `detect.py`**

```python
_BADGE_TEXTS = frozenset({"friend", "guild member"})
_OCR_FLOOR = 0.6


def is_badge_text(text: str) -> bool:
    """Whether one OCR box reads exactly as a badge label.

    Equality on a normalised single box, never a substring of the whole card. The
    OCR rectangle deliberately contains the opponent's NAME row, and names are
    arbitrary: a substring rule flags a player called "Friendzone".
    """
    return " ".join(text.split()).casefold() in _BADGE_TEXTS


def card_has_badge_text(blocks: list) -> bool:
    """Whether ONE card's OCR blocks contain a badge label.

    Takes the blocks for a single card, so no coordinate mapping is needed: the
    caller already cropped to that card. An earlier draft compared crop-local x
    against full-screen card ranges, which silently misassigned every card but the
    first.

    Args:
        blocks: `OCRResult` items from that card's crop only.

    Returns:
        True if any block is a badge label at or above the confidence floor.
    """
    return any(
        block.confidence.value >= _OCR_FLOOR and is_badge_text(block.text)
        for block in blocks
    )
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


def test_an_excluded_card_is_never_taken_again():
    """A card rejected by the confirming read must stay rejected, even if a later
    frame reads clear - otherwise the loop can attack it on the next pass."""
    d = decide((0, 1), set(), set(), "refresh", excluded=frozenset({0}))
    assert d.action is Action.TAKE and d.card == 1


def test_an_excluded_card_can_never_justify_forfeiting():
    """Exclusion is one-signal evidence by definition, so it must not satisfy the
    both-signals precondition for spending an attempt."""
    d = decide((0, 1), {1}, {1}, "give_up", excluded=frozenset({0}))
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
    excluded: frozenset[int] = frozenset(),
) -> Decision:
    """Choose an action.

    Args:
        order: cards to consider, most preferred first.
        flagged_colour: cards flagged by the colour arm.
        flagged_ocr: cards flagged by the OCR arm.
        control: "refresh", "give_up" or "unknown".
        excluded: cards a CONFIRMING read has already rejected. These stay rejected
            for the rest of the attempt - without that memory the loop re-evaluates
            from scratch and can take a card the second read called friendly.

    Returns:
        The action to take, with a reason for the log.
    """
    flagged = flagged_colour | flagged_ocr | excluded
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
    # An excluded card was rejected by a DISAGREEMENT, which is one-signal evidence
    # by definition, so it can never satisfy the both-signals precondition either.
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
- Create: `templates/arena/refresh_glyph.png`, `arena/give_up_glyph.png`, `arena/give_up_confirm.png`, `supreme_arena/refresh_glyph.png`
- Create: `.../services/friendly_fire/control.py`
- Test: `tests/.../friendly_fire/test_control.py`

**Interfaces:**
- Produces: `classify_control(frame: np.ndarray, mode: Mode) -> str` returning `"refresh" | "give_up" | "unknown"`; `find_give_up_tick(frame: np.ndarray) -> Point | None`

- [ ] **Step 1: Cut the templates from the fixtures**

```bash
cd ~/Dev/webdevbar/adbautoplayer
.venv/bin/python - <<'CUT'
from PIL import Image
D = "src-tauri/src-python/tests/games/afk_journey/services/friendly_fire/data/"
T = "src-tauri/src-python/adb_auto_player/games/afk_journey/templates/"
for src, box, dst in [
    ("01-friend-badge-middle-card-20260731.png", (930,1745,1010,1825), "arena/refresh_glyph.png"),
    ("03-refresh-exhausted-x-button-friend-on-left-20260731.png", (930,1745,1010,1825), "arena/give_up_glyph.png"),
    ("05-supreme-arena-select-opponent-no-badges-20260731.png", (905,1735,985,1815), "supreme_arena/refresh_glyph.png"),
    ("04-give-up-confirmation-dialog-20260731.png", (786,1163,947,1321), "arena/give_up_confirm.png"),
]:
    Image.open(D+src).convert("RGB").crop(box).save(T+dst)
    print("cut", dst)
# PNG on disk is colour-order agnostic: PIL writes it, cv2.imread reads it back as BGR,
# and every consumer here uses cv2.imread. No conversion anywhere in the pipeline.
CUT
```

- [ ] **Step 2: Write the failing test**

```python
"""Classifying the bottom-right control, and finding the give-up tick.

This decides whether we tap a control that forfeits a daily attempt, so the
fixtures matter more here than anywhere else in the suite.
"""

from pathlib import Path

import cv2
import numpy as np
from adb_auto_player.games.afk_journey.services.friendly_fire.control import (
    classify_control,
    find_give_up_tick,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    CONTROL_REGION,
    GIVE_UP_CANCEL_CENTRE,
    GLYPH_TEMPLATE_BOX,
    Mode,
)

DATA = Path(__file__).parent / "data"


def _frame(name):
    path = next(DATA.glob(f"{name}*.png"))
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert frame is not None, path
    return frame  # BGR, exactly as get_screenshot() delivers it - do NOT convert


def test_every_template_fits_inside_its_search_region():
    """Round 11: a 170px template into a 169px region cannot be matched at all."""
    for mode in Mode:
        rx0, ry0, rx1, ry1 = CONTROL_REGION[mode]
        tx0, ty0, tx1, ty1 = GLYPH_TEMPLATE_BOX[mode]
        assert tx1 - tx0 < rx1 - rx0
        assert ty1 - ty0 < ry1 - ry0


def test_arena_refresh_and_x_are_told_apart():
    assert classify_control(_frame("01"), Mode.ARENA) == "refresh"
    assert classify_control(_frame("03"), Mode.ARENA) == "give_up"


def test_supreme_arena_is_classified_with_its_OWN_template():
    """Round 10: the Arena refresh glyph scores 0.36 here. Deleting the Supreme
    Arena template and pointing at Arena's must break this test, because the
    symptom in production is a mode that quits instead of refreshing."""
    assert classify_control(_frame("05"), Mode.SUPREME_ARENA) == "refresh"
    assert classify_control(_frame("06"), Mode.SUPREME_ARENA) == "give_up"


def test_a_screen_with_no_control_is_unknown_not_guessed():
    """The dialog frame has no bottom-right control. Unknown must never tap."""
    assert classify_control(_frame("04"), Mode.ARENA) == "unknown"


def test_the_give_up_tick_is_found_and_is_the_tap_target():
    point = find_give_up_tick(_frame("04"))
    assert point is not None
    assert abs(point.x - 866) <= 12
    assert abs(point.y - 1241) <= 12


def test_the_tick_is_not_the_cancel_button():
    """Recorded geometry exists so we can assert we are not tapping cancel."""
    point = find_give_up_tick(_frame("04"))
    assert abs(point.x - GIVE_UP_CANCEL_CENTRE.x) > 100


def test_no_tick_on_a_screen_without_the_dialog():
    for name in ("01", "05"):
        assert find_give_up_tick(_frame(name)) is None
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/test_control.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: ...friendly_fire.control`

- [ ] **Step 4: Write `control.py`**

```python
"""The bottom-right control, and the give-up dialog.

One of these two states forfeits a daily attempt, so nothing here guesses: the
control is positively matched as Refresh or X, and anything else is "unknown",
which the caller must treat as a reason to stop rather than to tap.
"""

import logging
from pathlib import Path

import cv2
import numpy as np

from adb_auto_player.models.geometry import Point

from .geometry import (
    CONFIDENCE_FLOOR,
    CONTROL_REGION,
    GIVE_UP_TICK_REGION,
    Mode,
)

_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

# The refresh glyph is NOT shared between modes: Arena's arrow is anticlockwise and
# thin, Supreme Arena's clockwise and thick, and cross-matching scores 0.36 against a
# 0.8 floor. The X IS shared - it cross-matches at 1.00 - so it has no per-mode variant.
_REFRESH_TEMPLATE: dict[Mode, str] = {
    Mode.ARENA: "arena/refresh_glyph.png",
    Mode.SUPREME_ARENA: "supreme_arena/refresh_glyph.png",
}
_GIVE_UP_TEMPLATE = "arena/give_up_glyph.png"
_TICK_TEMPLATE = "arena/give_up_confirm.png"


def _load(name: str) -> np.ndarray:
    """Load a template in BGR, matching what the device and cv2.imread both give."""
    template = cv2.imread(str(_TEMPLATES / name), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(_TEMPLATES / name)
    return template


def _best(region: np.ndarray, template: np.ndarray) -> tuple[float, Point]:
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    centre = Point(
        location[0] + template.shape[1] // 2, location[1] + template.shape[0] // 2
    )
    return float(score), centre


def classify_control(frame: np.ndarray, mode: Mode) -> str:
    """Whether the bottom-right control is Refresh, the X, or unrecognised.

    Both-match and neither-match are BOTH "unknown". The two glyphs are visually
    unalike, so a double match means the read is wrong - and resolving it by higher
    confidence would spend a daily attempt on a coin toss.
    """
    x0, y0, x1, y1 = CONTROL_REGION[mode]
    region = frame[y0:y1, x0:x1]
    try:
        refresh, _ = _best(region, _load(_REFRESH_TEMPLATE[mode]))
        give_up, _ = _best(region, _load(_GIVE_UP_TEMPLATE))
    except (FileNotFoundError, cv2.error) as exc:
        logging.warning(f"[FF-20] could not classify the control: {exc}")
        return "unknown"

    is_refresh = refresh >= CONFIDENCE_FLOOR
    is_give_up = give_up >= CONFIDENCE_FLOOR
    if is_refresh == is_give_up:
        return "unknown"
    return "refresh" if is_refresh else "give_up"


def find_give_up_tick(frame: np.ndarray) -> Point | None:
    """The green confirm tick of the "Give up this challenge?" dialog.

    Detected by the tick rather than the dialog sheet: the sheet is blank (pixel
    std 4.8 against the tick's 54.5) so it carries no information, and the crop
    that looked language-independent actually contained the sentence. The tick is
    an icon, it is feature-rich, and it IS the tap target - so detection and action
    cannot disagree.

    Returns:
        The matched centre to tap, or None if the dialog is not up.
    """
    x0, y0, x1, y1 = GIVE_UP_TICK_REGION
    region = frame[y0:y1, x0:x1]
    try:
        score, centre = _best(region, _load(_TICK_TEMPLATE))
    except (FileNotFoundError, cv2.error) as exc:
        logging.warning(f"[FF-21] could not look for the give-up tick: {exc}")
        return None
    if score < CONFIDENCE_FLOOR:
        return None
    return Point(x0 + centre.x, y0 + centre.y)
```

- [ ] **Step 5: Run, lint, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider
cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/ && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/services/friendly_fire/
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): classify the control and locate the give-up tick"
```

---

### Task 7: Frame collection

Unchanged from the previous numbering - see the `collect.py` task below, which now
follows control classification.

**Files:**
- Create: `.../services/friendly_fire/collect.py`
- Test: `tests/.../friendly_fire/test_collect.py`

**Interfaces:**
- Produces: `collection_dir() -> Path`, `archive(frame, mode, outcome) -> Path | None`

- [ ] **Step 1: Write the failing test**

```python
"""Frame collection. Must work on a machine that is not the author's."""

import numpy as np
from adb_auto_player.games.afk_journey.services.friendly_fire.collect import (
    archive,
    collection_dir,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode


def test_the_directory_is_NOT_the_authors_vault_mount(monkeypatch):
    """The spec's earlier draft named /mnt/vault, which no end user has."""
    monkeypatch.delenv("ADB_FRIENDLY_FIRE_DIR", raising=False)
    assert "/mnt/vault" not in str(collection_dir())


def test_it_honours_an_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    assert collection_dir() == tmp_path


def test_archiving_writes_a_named_frame(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    out = archive(np.zeros((20, 20, 3), dtype=np.uint8), Mode.ARENA, "flagged-1")
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
so the destination is resolved the way the Solstice Clash mode resolves it.
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
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        path = directory / f"{mode.value}-{stamp}-{outcome}.png"
        cv2.imwrite(str(path), frame)  # already BGR
        return path
    except Exception as exc:  # noqa: BLE001 - never worth a match
        logging.debug(f"[FF-10] could not archive frame: {exc}")
        return None
```

- [ ] **Step 3: Run, lint, commit**

---

### Task 8: The orchestrator - the package's public surface

This is the task the mixins actually call. It is the only place colour, OCR, the
control and the decision meet.

**Files:**
- Create: `.../services/friendly_fire/evaluate.py`
- Modify: `.../services/friendly_fire/__init__.py`
- Test: `tests/.../friendly_fire/test_evaluate.py`

**Interfaces:**
- Consumes: `detect.cards_with_badges`, `detect.card_has_badge_text`, `control.classify_control`, `select.decide`, `select.preference_order`, `collect.archive`
- Produces: `CardReport` (frozen dataclass: `index: int`, `colour: bool`, `ocr: bool`), `evaluate(frame, mode, position, ocr_backend) -> Decision`

- [ ] **Step 1: Write the failing test**

```python
"""The orchestrator. A fake OCR backend keeps this device-free and deterministic."""

from pathlib import Path

import cv2
import numpy as np
from adb_auto_player.games.afk_journey.services.friendly_fire.evaluate import evaluate
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import Mode
from adb_auto_player.games.afk_journey.services.friendly_fire.select import Action
from adb_auto_player.games.afk_journey.settings import OpponentPosition

DATA = Path(__file__).parent / "data"


def _frame(name):
    path = next(DATA.glob(f"{name}*.png"))
    return cv2.imread(str(path), cv2.IMREAD_COLOR)  # BGR, as the device delivers


class _NoText:
    """An OCR backend that finds nothing, isolating the colour arm."""

    def detect_text_blocks(self, image, min_confidence=None):
        return []


def test_a_flagged_middle_card_is_skipped_for_card_1(monkeypatch, tmp_path):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    d = evaluate(_frame("01"), Mode.ARENA, OpponentPosition.Left, _NoText())
    assert d.action is Action.TAKE and d.card == 0


def test_a_flagged_first_card_falls_through_to_card_2(monkeypatch, tmp_path):
    """Frame 03 has the badge on the LEFT card and refreshes exhausted."""
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    d = evaluate(_frame("03"), Mode.ARENA, OpponentPosition.Left, _NoText())
    assert d.action is Action.TAKE and d.card == 1


def test_a_clean_board_takes_the_first_card(monkeypatch, tmp_path):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    d = evaluate(_frame("05"), Mode.SUPREME_ARENA, OpponentPosition.Left, _NoText())
    assert d.action is Action.TAKE and d.card == 0


def test_the_configured_position_is_respected(monkeypatch, tmp_path):
    """Frame 08 flags the MIDDLE card, so Middle must fall through to card 1."""
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    d = evaluate(_frame("08"), Mode.SUPREME_ARENA, OpponentPosition.Middle, _NoText())
    assert d.action is Action.TAKE and d.card == 0


def test_every_evaluation_archives_its_frame(monkeypatch, tmp_path):
    monkeypatch.setenv("ADB_FRIENDLY_FIRE_DIR", str(tmp_path))
    evaluate(_frame("01"), Mode.ARENA, OpponentPosition.Left, _NoText())
    assert list(tmp_path.glob("*.png"))
```

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL, `ModuleNotFoundError: ...friendly_fire.evaluate`

- [ ] **Step 3: Write `evaluate.py`**

```python
"""Where the two signals, the control and the decision meet."""

import logging
from dataclasses import dataclass

import numpy as np

from adb_auto_player.models import ConfidenceValue
from adb_auto_player.games.afk_journey.settings import OpponentPosition

from .collect import archive
from .control import classify_control
from .detect import card_has_badge_text, cards_with_badges
from .geometry import CARD_X_RANGES, OCR_CONFIDENCE_FLOOR, OCR_Y_RANGE, Mode
from .select import Decision, decide, preference_order


@dataclass(frozen=True)
class CardReport:
    """What each signal said about one card."""

    index: int
    colour: bool
    ocr: bool


def _ocr_flags(frame: np.ndarray, mode: Mode, cards, ocr_backend) -> set[int]:
    """Which of `cards` carry badge text.

    Each card is OCR'd from its OWN crop, so the results need no coordinate
    mapping back to screen space - which is where an earlier draft went wrong.
    """
    flagged: set[int] = set()
    y0, y1 = OCR_Y_RANGE[mode]
    for index in cards:
        x0, x1 = CARD_X_RANGES[mode][index]
        try:
            blocks = ocr_backend.detect_text_blocks(
                frame[y0:y1, x0:x1], ConfidenceValue(OCR_CONFIDENCE_FLOOR)
            )
        except Exception as exc:  # noqa: BLE001 - OCR failure must not abort the mode
            logging.warning(f"[FF-22] OCR failed on card {index + 1}: {exc}")
            continue
        if card_has_badge_text(blocks):
            flagged.add(index)
    return flagged


def evaluate(
    frame: np.ndarray,
    mode: Mode,
    position: OpponentPosition,
    ocr_backend,
    excluded: frozenset[int] = frozenset(),
) -> Decision:
    """Read one select-opponent frame and decide what to do about it.

    Args:
        frame: BGR screenshot at 1080x1920, as `get_screenshot()` returns it.
        mode: which screen this is.
        position: the user's configured Opponent Position (ignored for Arena).
        ocr_backend: anything exposing `detect_text_blocks`.
        excluded: cards already rejected by a confirming read this attempt.

    Returns:
        The action to take, with a reason for the log.
    """
    order = preference_order(mode, position)
    colour = cards_with_badges(frame, mode) & set(order)
    ocr = _ocr_flags(frame, mode, order, ocr_backend)
    control = classify_control(frame, mode)
    decision = decide(order, colour, ocr, control, excluded)

    disagreement = colour ^ ocr
    outcome = "-".join(
        [
            decision.action.value,
            f"colour{sorted(c + 1 for c in colour)}",
            f"ocr{sorted(c + 1 for c in ocr)}",
            control,
        ]
    ).replace(" ", "")
    archive(frame, mode, outcome + ("-DISAGREE" if disagreement else ""))

    logging.info(
        f"[FF-01] {mode.value}: colour flagged {sorted(c + 1 for c in colour)}, "
        f"OCR flagged {sorted(c + 1 for c in ocr)}, control={control} "
        f"-> {decision.action.value} ({decision.reason})"
    )
    return decision


def confirms_take(
    frame: np.ndarray,
    mode: Mode,
    position: OpponentPosition,
    ocr_backend,
    card: int,
) -> bool:
    """Second read: is `card` STILL clear on a fresh frame?

    The mode has no timer, so a confirming read is free and the cost of being wrong
    is not. A card seen clear once and flagged once is treated as flagged - the
    optimistic read would defeat the whole feature.

    Args:
        frame: a SECOND screenshot, taken after the one that produced the decision.
        mode: which screen.
        position: the configured preference.
        ocr_backend: anything exposing `detect_text_blocks`.
        card: the card the first read chose.

    Returns:
        True only if both reads agree the card is clear.
    """
    order = preference_order(mode, position)
    colour = cards_with_badges(frame, mode) & set(order)
    ocr = _ocr_flags(frame, mode, [card], ocr_backend)
    if card in (colour | ocr):
        logging.warning(
            f"[FF-02] second read disagrees on card {card + 1} - treating as flagged"
        )
        archive(frame, mode, f"DISAGREE-card{card + 1}")
        return False
    return True


def screen_changed(before: np.ndarray, after: np.ndarray) -> bool:
    """Whether a refresh actually redrew the cards.

    A stalled refresh and an exhausted one look identical from a single frame, and
    acting on the guess taps a control that forfeits an attempt.
    """
    if before.shape != after.shape:
        return True
    return bool(np.mean(np.abs(before.astype(np.int16) - after.astype(np.int16))) > 1.0)
```

And export it:

```python
"""Prevent Friendly Fire: never attack a Friend or a Guild Member."""

from .evaluate import evaluate
from .geometry import Mode
from .select import Action, Decision

__all__ = ["Action", "Decision", "Mode", "evaluate"]
```

- [ ] **Step 4: Run, lint, commit**

---

### Task 9: Wire into Arena

**Files:**
- Modify: `mixins/arena.py`
- Test: `tests/.../friendly_fire/test_arena_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
"""Arena wiring, with a stub standing in for the device."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2

sys.modules.setdefault("pytauri", MagicMock())
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.games.afk_journey.mixins.arena import ArenaMixin  # noqa: E402
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (  # noqa: E402
    CARD_X_RANGES,
    Mode,
)

DATA = Path(__file__).parent / "data"


class _Stub(ArenaMixin):
    def __init__(self, on):
        self._s = SimpleNamespace(arena=SimpleNamespace(prevent_friendly_fire=on))

    @property
    def settings(self):
        return self._s


def test_the_guard_is_off_by_default_and_reports_so():
    assert _Stub(False)._friendly_fire_enabled() is False


def test_the_guard_reports_on_when_enabled():
    assert _Stub(True)._friendly_fire_enabled() is True


def test_every_card_is_locatable_by_the_existing_template():
    """Arena's current code searches only the left 40%, which is exactly why it
    cannot reach card 2. Per card x-range, all three match at >= 0.99."""
    frame = cv2.imread(str(next(DATA.glob("01*.png"))), cv2.IMREAD_COLOR)
    template = cv2.imread(
        str(
            Path(__file__).parents[5]
            / "adb_auto_player/games/afk_journey/templates/arena/opponent.png"
        ),
        cv2.IMREAD_COLOR,
    )
    # Measured: 0.99927, 0.99416, 0.98908. The floor is 0.98 because card 3 sits at
    # 0.98908 - an earlier draft asserted 0.99 and could never have passed.
    for index, (x0, x1) in enumerate(CARD_X_RANGES[Mode.ARENA]):
        result = cv2.matchTemplate(frame[:, x0:x1], template, cv2.TM_CCOEFF_NORMED)
        assert result.max() >= 0.98, f"card {index + 1} not located"
```

- [ ] **Step 2: Patch `arena.py`** (write the patch to a file, apply with `git apply` - never the Edit tool on a `.py` file)

Module-level constants, beside the existing ones:

```python
# The guard re-reads after every refresh. Bounded so a screen that never changes
# cannot loop forever; the real exit is the control turning into the X.
_MAX_FRIENDLY_FIRE_ROUNDS = 12
# Centre of the bottom-right control, per mode, from CONTROL_REGION.
_ARENA_CONTROL_AT = Point(967, 1794)
```

Imports:

```python
from adb_auto_player.games.afk_journey.services.friendly_fire import (
    Action,
    Mode as FFMode,
    evaluate as ff_evaluate,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.control import (
    find_give_up_tick,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.evaluate import (
    confirms_take,
    screen_changed,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    CARD_X_RANGES,
)
from adb_auto_player.games.afk_journey.settings import OpponentPosition
from adb_auto_player.ocr import RapidOCRBackend
```

Methods:

```python
    def _friendly_fire_enabled(self) -> bool:
        """Whether the guard is on for this mode."""
        arena = getattr(self.settings, "arena", None)
        return bool(getattr(arena, "prevent_friendly_fire", False))

    def _ff_halt(self) -> bool:
        """Stop the whole RUN, not just this selection, and return False.

        Every failure exit goes through here. `run_arena` has TWO loops, so a bare
        `return False` only breaks the first - the second then claims a free attempt
        and fights on, which is how an unsafe give-up or a failed refresh would have
        been followed by more battles.
        """
        self._ff_stop_run = True
        return False

    def _ff_ocr(self) -> RapidOCRBackend:
        """One OCR backend per run, built lazily.

        There is no `ocr_backend` attribute on the base class - every mode that
        needs OCR constructs its own, as `solstice_clash.py:2112` does.
        """
        backend = getattr(self, "_ff_ocr_cache", None)
        if backend is None:
            backend = RapidOCRBackend()
            self._ff_ocr_cache = backend
        return backend

    def _tap_arena_card(self, index: int) -> bool:
        """Tap opponent card `index`, located within its own x-range."""
        x0, x1 = CARD_X_RANGES[FFMode.ARENA][index]
        crop = CropRegions(left=x0 / 1080, right=(1080 - x1) / 1080)
        match = self.game_find_template_match(
            template="arena/opponent.png", crop_regions=crop
        )
        if match is None:
            logging.error(f"[FF-30] could not locate card {index + 1}")
            self._ff_stop_run = True
            return False
        self.tap(match)
        return True

    def _ff_give_up(self) -> bool:
        """Forfeit the challenge. Two positive matches before anything is tapped.

        The X is already the control - that is what a GIVE_UP decision means - so
        tap it FIRST to raise the dialog, then match the tick and tap that. An
        earlier draft looked for the tick before opening the dialog, so it could
        never find one and never forfeited.
        """
        self.tap(_ARENA_CONTROL_AT)
        self.sleep_navigation()
        tick = find_give_up_tick(self.get_screenshot())
        if tick is None:
            logging.error("[FF-33] give-up dialog did not appear - stopping")
            return self._ff_halt()
        self.tap(tick)
        self.sleep_navigation()
        self._ff_stop_run = True
        logging.info("[FF-34] gave up the challenge - every opponent was a friend")
        return False

    def _choose_opponent_guarded(self) -> bool:
        """Pick an opponent that is neither a Friend nor a Guild Member.

        Waits for the selection screen before reading it. The unguarded path gets
        that readiness guarantee from its `wait_for_template`, which this branch
        replaces - without an explicit wait here, the first evaluation can land on
        a half-drawn frame, read it as clear, and then fail to locate the card.
        """
        try:
            self.wait_for_template(
                template="arena/opponent.png",
                timeout=self.min_timeout,
                timeout_message="Arena opponent screen did not appear.",
            )
        except GameTimeoutError as fail:
            logging.error(f"[FF-35] {fail}")
            return self._ff_halt()

        retried_unknown = False
        excluded: set[int] = set()
        for _ in range(_MAX_FRIENDLY_FIRE_ROUNDS):
            self.handle_popup_messages()
            decision = ff_evaluate(
                self.get_screenshot(),
                FFMode.ARENA,
                OpponentPosition.Left,  # Arena has no position setting
                self._ff_ocr(),
                frozenset(excluded),
            )
            if decision.action is Action.TAKE:
                # Confirming read before committing to a battle. Free - no timer -
                # and it is what catches a transient false negative.
                self.sleep_navigation()
                if confirms_take(
                    self.get_screenshot(),
                    FFMode.ARENA,
                    OpponentPosition.Left,
                    self._ff_ocr(),
                    decision.card,
                ):
                    return self._tap_arena_card(decision.card)
                # Remember the rejection for the rest of this attempt. Without it the
                # next iteration starts fresh and can take the very card the second
                # read called friendly.
                excluded.add(decision.card)
                continue
            if decision.action is Action.STOP:
                # An "unknown" control gets one re-read before the mode stops: a
                # single bad frame should not end the run.
                if "neither Refresh nor X" in decision.reason and not retried_unknown:
                    retried_unknown = True
                    self.sleep_navigation()
                    continue
                logging.warning(f"[FF-31] stopping: {decision.reason}")
                return self._ff_halt()
            if decision.action is Action.GIVE_UP:
                return self._ff_give_up()

            before = self.get_screenshot()
            self.tap(_ARENA_CONTROL_AT)
            self.sleep_navigation()
            if not screen_changed(before, self.get_screenshot()):
                logging.error(
                    "[FF-37] the screen did not change after a refresh - stopping "
                    "rather than assuming exhaustion, which would forfeit an attempt"
                )
                return self._ff_halt()
            # Re-establish readiness after the refresh redraws the cards.
            try:
                self.wait_for_template(
                    template="arena/opponent.png",
                    timeout=self.min_timeout,
                    timeout_message="Arena cards did not redraw after a refresh.",
                )
            except GameTimeoutError as fail:
                logging.error(f"[FF-36] {fail}")
                return self._ff_halt()
        logging.warning("[FF-32] no non-friendly opponent after the round cap")
        return self._ff_halt()
```

**Stopping the RUN, not just the selection.** `run_arena` has TWO loops: five attempts,
then two more that claim a free attempt. Returning `False` from `_choose_opponent` only
breaks the first, so a give-up would be followed by claiming a free attempt and fighting
on - the opposite of the spec's "after a give-up, the mode STOPS". Set a run-level flag and
honour it in both loops:

```python
    def run_arena(self) -> None:
        """Use Arena attempts."""
        self.start_up(device_streaming=False)
        self._ff_stop_run = False
        ...
        for _ in range(5):
            if self._ff_stop_run:
                break
            ...
        for _ in range(2):
            if self._ff_stop_run:
                break
            ...
```

**Where the branch goes.** NOT at the top of `_choose_opponent`: that method first
waits for and taps Challenge/Continue, and branching before it would evaluate the
Arena landing screen instead of the card-selection screen. Branch immediately after
the existing `self.tap(btn)` and `handle_popup_messages()`, replacing only the
`wait_for_template("arena/opponent.png", ...)` and its `self.tap(opponent)`:

```python
            self.handle_popup_messages()  # Clear any potential popups
            if self._friendly_fire_enabled():
                return self._choose_opponent_guarded()
            opponent = self.wait_for_template(  # unchanged original path
```

- [ ] **Step 3: Run, lint, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider
cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/ && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/arena.py
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): Arena wiring"
```

---

### Task 10: Wire into Supreme Arena

**Files:**
- Modify: `mixins/supreme_arena.py`
- Test: `tests/.../friendly_fire/test_supreme_arena_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
"""Supreme Arena wiring. Same shape as Arena, but it taps fixed points and it has a
position setting to respect."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.modules.setdefault("pytauri", MagicMock())
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.games.afk_journey.mixins.supreme_arena import (  # noqa: E402
    SupremeArenaMixin,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (  # noqa: E402
    SA_TAP_POINTS,
)
from adb_auto_player.games.afk_journey.settings import OpponentPosition  # noqa: E402


class _Stub(SupremeArenaMixin):
    def __init__(self, on, position=OpponentPosition.Left):
        self._s = SimpleNamespace(
            supreme_arena=SimpleNamespace(
                prevent_friendly_fire=on, opponent_position=position, attempts=5
            )
        )
        self.taps = []

    @property
    def settings(self):
        return self._s

    def tap(self, coordinates, **kwargs):
        self.taps.append(coordinates)


def test_the_guard_is_off_by_default():
    assert _Stub(False)._sa_friendly_fire_enabled() is False


def test_the_guard_reports_on_when_enabled():
    assert _Stub(True)._sa_friendly_fire_enabled() is True


def test_the_configured_position_is_read_not_ignored():
    """The toggle must never silently override a setting the user chose."""
    bot = _Stub(True, OpponentPosition.Right)
    assert bot._sa_position() is OpponentPosition.Right


def test_tapping_a_card_uses_the_existing_fixed_points():
    bot = _Stub(True)
    bot._tap_sa_card(1)
    assert bot.taps == [SA_TAP_POINTS[1]]
```

- [ ] **Step 2: Patch `supreme_arena.py`**

Same module-level constant and imports as Task 9, with the control centre for this
mode and no `CARD_X_RANGES` import:

```python
_MAX_FRIENDLY_FIRE_ROUNDS = 12
_SA_CONTROL_AT = Point(944, 1788)
```

```python
from adb_auto_player.games.afk_journey.services.friendly_fire import (
    Action,
    Mode as FFMode,
    evaluate as ff_evaluate,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.control import (
    find_give_up_tick,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.evaluate import (
    confirms_take,
    screen_changed,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (
    SA_TAP_POINTS,
)
from adb_auto_player.ocr import RapidOCRBackend
```

```python
    def _sa_friendly_fire_enabled(self) -> bool:
        """Whether the guard is on for Supreme Arena."""
        return bool(
            getattr(self.settings.supreme_arena, "prevent_friendly_fire", False)
        )

    def _sa_halt(self) -> bool:
        """Stop the whole run and return False. Every failure exit goes through here."""
        self._ff_stop_run = True
        return False

    def _sa_position(self) -> OpponentPosition:
        """The user's configured card preference, which the guard respects."""
        return self.settings.supreme_arena.opponent_position

    def _sa_ocr(self) -> RapidOCRBackend:
        """One OCR backend per run, built lazily - there is no base-class attribute."""
        backend = getattr(self, "_sa_ocr_cache", None)
        if backend is None:
            backend = RapidOCRBackend()
            self._sa_ocr_cache = backend
        return backend

    def _tap_sa_card(self, index: int) -> None:
        """Tap opponent card `index` at its existing fixed point."""
        self.tap(SA_TAP_POINTS[index])

    def _sa_give_up(self) -> bool:
        """Forfeit the challenge: tap the X, match the dialog tick, tap it."""
        self.tap(_SA_CONTROL_AT)
        self.sleep_navigation()
        tick = find_give_up_tick(self.get_screenshot())
        if tick is None:
            logging.error("[FF-43] give-up dialog did not appear - stopping")
            return self._sa_halt()
        self.tap(tick)
        self.sleep_navigation()
        self._ff_stop_run = True
        logging.info("[FF-44] gave up the challenge - every opponent was a friend")
        return False

    def _sa_choose_opponent_guarded(self) -> bool:
        """Pick an opponent that is neither a Friend nor a Guild Member."""
        retried_unknown = False
        excluded: set[int] = set()
        for _ in range(_MAX_FRIENDLY_FIRE_ROUNDS):
            self.handle_popup_messages()
            decision = ff_evaluate(
                self.get_screenshot(),
                FFMode.SUPREME_ARENA,
                self._sa_position(),
                self._sa_ocr(),
                frozenset(excluded),
            )
            if decision.action is Action.TAKE:
                self.sleep_navigation()
                if confirms_take(
                    self.get_screenshot(),
                    FFMode.SUPREME_ARENA,
                    self._sa_position(),
                    self._sa_ocr(),
                    decision.card,
                ):
                    self._tap_sa_card(decision.card)
                    return True
                excluded.add(decision.card)
                continue
            if decision.action is Action.STOP:
                if "neither Refresh nor X" in decision.reason and not retried_unknown:
                    retried_unknown = True
                    self.sleep_navigation()
                    continue
                logging.warning(f"[FF-41] stopping: {decision.reason}")
                return self._sa_halt()
            if decision.action is Action.GIVE_UP:
                return self._sa_give_up()

            before = self.get_screenshot()
            self.tap(_SA_CONTROL_AT)
            self.sleep_navigation()
            if not screen_changed(before, self.get_screenshot()):
                logging.error(
                    "[FF-47] the screen did not change after a refresh - stopping "
                    "rather than assuming exhaustion, which would forfeit an attempt"
                )
                return self._sa_halt()
        logging.warning("[FF-42] no non-friendly opponent after the round cap")
        return self._sa_halt()
```

**Stopping the run.** `run_supreme_arena` has one loop over `attempts`; set
`self._ff_stop_run = False` at the start of the run and `break` on it at the top of the
loop, exactly as Arena does.

**Where the branch goes.** In `_sa_choose_opponent`, after the Select Opponent screen
has been reached and the `no_attempts_popup` case has been handled - that is, replacing
only the `position = ...` / `opponent_x = ...` / `self.tap(Point(opponent_x, 950))`
block, and keeping the `challenge_detail.png` tap that follows it:

```python
            if self._sa_friendly_fire_enabled():
                if not self._sa_choose_opponent_guarded():
                    return False
            else:
                position = self.settings.supreme_arena.opponent_position  # unchanged
                ...
```

- [ ] **Step 3: Run, lint, commit**

```bash
cd src-tauri && ../.venv/bin/python -m pytest src-python/tests/games/afk_journey/services/friendly_fire/ -q -p no:cacheprovider
cd ~/Dev/webdevbar/adbautoplayer && uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/ && uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/supreme_arena.py
git add -A src-tauri/src-python && git commit -m "feat(friendly-fire): Supreme Arena wiring"
```

---

### Task 11: Changelog, version bump, build

- [ ] Add a `CHANGELOG.md` entry under Unreleased: the toggle, default off, the refresh-then-give-up ladder, and that one signal can skip a card but never forfeit an attempt.
- [ ] Bump `WDB_RELEASE` in BOTH `wdb_version.py` and `src/lib/wdb-version.ts` - the build refuses if they disagree.
- [ ] Run the whole solstice + friendly-fire + auto-bet suites and show the pass count.
- [ ] `./build-rpm.sh`, then report the install command. Do not install; the operator restarts the collector themselves.

---

## Reviewer findings judged INVALID

Recorded so they are not re-applied by a later reader.

**"`parents[5]` resolves to `tests`, use `parents[6]`"** (plan round 6). Counted directly:
from `tests/games/afk_journey/services/friendly_fire/test_x.py`, `parents[5]` is
`src-tauri/src-python`, and `parents[5]/adb_auto_player` exists while
`parents[6]/adb_auto_player` does not. Round 1 checked the same expression and agreed it was
correct; round 6 contradicted itself. The path stays as written.

## Self-Review

**Spec coverage:** detection (T2, T3), card assignment (T2), preference order (T4), give-up precondition (T4), settings (T5), control classification (T6), give-up dialog (T6), frame collection (T7), Arena wiring (T8), Supreme Arena wiring (T9), changelog and build (T10). Error-handling rows are covered by T4's decision table plus T8/T9 wiring.

**Placeholders:** none - every code step carries real code.

**Type consistency:** `Mode` from geometry is used unchanged in detect, select and collect. `decide()` takes `control: str` with the same three literals `classify_control` returns. `preference_order` takes the real `OpponentPosition` enum from `settings.py`.
