# Solstice Clash Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the vision and hero-library foundation for the AFK Journey "Solstice Clash" event, so later work can record matches and predict outcomes.

**Scope relative to the spec:** this plan implements **Phase 1a** - the parts of
`docs/superpowers/specs/2026-07-25-solstice-clash-phase1-design.md` that need no OCR and no device:
screen classification, portrait extraction, hero matching, library construction, and the SQLite +
manifest store. The spec's remaining Phase 1 items - **OCR field extraction** (player names,
ratings, ranks, theme, countdown, odds/pools, token balance, result banners), **the labelling
pass**, and **the accuracy-measurement report** - are deliberately left to a follow-up **Phase 1b**
plan, because they need fixture frames for the VS/result screens plus an OCR backend choice that
this plan does not make. Phase 1 is not complete until 1b ships.

**Architecture:** Three shared services under `games/afk_journey/services/solstice/` - `vision.py` (template-anchored screen classification + field extraction), `heroes.py` (library build, matching, labelling), `store.py` (SQLite + committed JSON manifest). A thin `SolsticeMixin` is added to the `AFKJourneyBase` inheritance chain later; Phase 1 delivers the services and their tests only.

**Tech Stack:** Python 3.13, OpenCV (`cv2`), NumPy, SQLite (stdlib `sqlite3`), pytest. Existing helpers: `TemplateMatcher` (`template_matching/template_matcher.py`), `SettingsLoader.get_app_config_dir()`.

## Global Constraints

- **Device resolution is 1080x1920.** All geometry constants assume it. `AFKJourneyBase.base_resolution` is already `Resolution.from_string("1080x1920")` (`games/afk_journey/base.py`).
- **Hero matching direction is fixed and must not be inverted:** `TemplateMatcher.find_template_match(base_image=<padded library entry>, template_image=<live probe>)`. The helper slides `template_image` inside `base_image` and raises `ValueError` if the template is larger (`template_matching/template_matcher.py:281`).
- **Never use `game_find_template_match` for hero matching** - it uses the opposite orientation (screenshot as base) and cannot return per-candidate scores.
- **Screen detection uses template anchors only.** Brightness/pixel-statistic heuristics are forbidden; they failed three times during design.
- **`hero.slug` is immutable once assigned.** Only `label` may change. Skins link via `is_skin_of`, never by renaming.
- **Accept a hero match only at score >= 0.90 AND margin >= 0.10 over the runner-up.** Otherwise record `unknown`; never guess.
- **All tests must run without a device**, using committed fixture PNGs.
- Test command (from repo root): `uv run --group dev pytest <path> -q`
- Lint/format: `uv run --group dev ruff check` and `ruff format` if configured; follow existing file style (`from __future__ import annotations`, type hints, Google-style docstrings).

---

## File Structure

| File | Responsibility |
|---|---|
| `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/__init__.py` | package marker, public exports |
| `.../solstice/geometry.py` | all pixel constants (grid, slots, crops) - single source of drift |
| `.../solstice/screens.py` | `SolsticeScreen` enum + anchor definitions |
| `.../solstice/vision.py` | screen classification + field extraction |
| `.../solstice/heroes.py` | portrait extraction, matching, library build |
| `.../solstice/store.py` | SQLite schema, manifest load/reconcile/write |
| `.../templates/event/solstice_clash/anchors/*.png` | committed screen anchors |
| `.../templates/event/solstice_clash/heroes/*.png` | committed hero templates |
| `.../templates/event/solstice_clash/hero_library.json` | committed manifest (slug/label/is_skin_of/templates) |
| `tests/games/afk_journey/services/solstice/` | tests + fixture frames |

---

### Task 1: Geometry constants + fixture frames

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/__init__.py`
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/geometry.py`
- Create: `src-tauri/src-python/tests/games/afk_journey/services/solstice/__init__.py`
- Create: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_geometry.py`
- Create fixtures: `src-tauri/src-python/tests/games/afk_journey/services/solstice/data/` (4 PNGs, copied in Step 1)

**Interfaces:**
- Consumes: nothing
- Produces: `BASE_RESOLUTION`, `POOL_ROWS`, `POOL_COLS`, `CARD_W`, `CARD_H`, `PORTRAIT_BOX`, `PORTRAIT_PAD`, `PICK_SLOTS`, `SLOT_BOX`, `card_rect(row, col)`, `portrait_rect(row, col)`, `slot_rect(name)` - all returning `tuple[int, int, int, int]` as `(x0, y0, x1, y1)`

- [ ] **Step 1: Copy fixture frames into the test data dir**

These four frames were captured during design and cover the screens Phase 1 must classify.

```bash
cd /mnt/docs/adbautoplayer
mkdir -p src-tauri/src-python/tests/games/afk_journey/services/solstice/data
cp /tmp/solstice/compete/192048_396.png  src-tauri/src-python/tests/games/afk_journey/services/solstice/data/banning.png
cp /tmp/solstice/skin_talene.png         src-tauri/src-python/tests/games/afk_journey/services/solstice/data/selecting.png
cp /tmp/solstice/next.png                src-tauri/src-python/tests/games/afk_journey/services/solstice/data/spectate_draft.png
cp /tmp/solstice/draw/d_1.png            src-tauri/src-python/tests/games/afk_journey/services/solstice/data/outworld_night.png
ls -la src-tauri/src-python/tests/games/afk_journey/services/solstice/data/
```

Expected: four PNG files, each 1080x1920.

If `/tmp/solstice` has been cleared, re-capture equivalents with
`adb -s 192.168.240.112:5555 exec-out screencap -p` (strip the leading Waydroid warning by seeking to the PNG magic bytes `\x89PNG\r\n\x1a\n`).

- [ ] **Step 2: Write the failing test**

Create `tests/games/afk_journey/services/solstice/test_geometry.py`:

```python
"""Geometry constants for Solstice Clash screens.

Pure arithmetic - no device, no image decoding required.
"""

from __future__ import annotations

from adb_auto_player.games.afk_journey.services.solstice import geometry as geo


def test_base_resolution_is_portrait_1080x1920():
    assert geo.BASE_RESOLUTION == (1080, 1920)


def test_pool_grid_is_four_by_five():
    assert len(geo.POOL_ROWS) == 4
    assert len(geo.POOL_COLS) == 5


def test_card_rect_top_left_matches_measured_values():
    assert geo.card_rect(0, 0) == (155, 665, 305, 855)


def test_card_rect_bottom_right_stays_on_screen():
    x0, y0, x1, y1 = geo.card_rect(3, 4)
    assert x1 <= geo.BASE_RESOLUTION[0]
    assert y1 <= geo.BASE_RESOLUTION[1]


def test_portrait_rect_is_inside_its_card():
    cx0, cy0, cx1, cy1 = geo.card_rect(1, 2)
    px0, py0, px1, py1 = geo.portrait_rect(1, 2)
    assert cx0 <= px0 < px1 <= cx1
    assert cy0 <= py0 < py1 <= cy1


def test_portrait_excludes_badge_row():
    """Pick badges and padlocks render in the top ~45px of a card."""
    _, cy0, _, _ = geo.card_rect(0, 0)
    _, py0, _, _ = geo.portrait_rect(0, 0)
    assert py0 - cy0 >= 45


def test_all_six_pick_slots_defined():
    assert set(geo.PICK_SLOTS) == {"blue1", "blue4", "blue5", "red2", "red3", "red6"}


def test_slot_rect_returns_expected_width():
    x0, _, x1, _ = geo.slot_rect("blue1")
    assert x1 - x0 == 120
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_geometry.py -q
```

Expected: FAIL - `ModuleNotFoundError: No module named '...services.solstice'`

- [ ] **Step 4: Write the implementation**

Create `services/solstice/__init__.py`:

```python
"""Solstice Clash event services: vision, hero library, storage."""
```

Create `services/solstice/geometry.py`:

```python
"""Pixel geometry for Solstice Clash screens at 1080x1920.

Single source of truth for every coordinate. If a game update shifts the UI,
this is the only module that changes.

All rectangles are returned as (x0, y0, x1, y1) with exclusive upper bounds,
matching numpy slicing conventions: img[y0:y1, x0:x1].
"""

from __future__ import annotations

BASE_RESOLUTION: tuple[int, int] = (1080, 1920)

# Hero pool grid, 4 rows x 5 columns (compete Banning and Selecting screens).
POOL_ROWS: tuple[int, ...] = (665, 900, 1135, 1370)
POOL_COLS: tuple[int, ...] = (155, 315, 475, 635, 795)
CARD_W: int = 150
CARD_H: int = 190

# Portrait sub-region within a card. Starts below the star row and any pick
# badge / padlock overlay (which occupy roughly the top 45px), and ends above
# the faction gem at the bottom.
PORTRAIT_BOX: tuple[int, int, int, int] = (20, 45, 130, 165)  # x0, y0, x1, y1

# Padding added when a portrait is stored as a library template, so a live
# probe can be searched *within* it and small misalignments are absorbed.
PORTRAIT_PAD: int = 12

# Draft pick slots. Keys are stable identifiers used throughout the codebase.
PICK_SLOTS: dict[str, int] = {
    "blue1": 48,
    "blue4": 190,
    "blue5": 333,
    "red2": 630,
    "red3": 772,
    "red6": 915,
}
SLOT_W: int = 120
SLOT_BOX: tuple[int, int] = (390, 555)  # y0, y1


def card_rect(row: int, col: int) -> tuple[int, int, int, int]:
    """Return the (x0, y0, x1, y1) rectangle of a pool card."""
    x0 = POOL_COLS[col]
    y0 = POOL_ROWS[row]
    return x0, y0, x0 + CARD_W, y0 + CARD_H


def portrait_rect(row: int, col: int) -> tuple[int, int, int, int]:
    """Return the portrait sub-rectangle of a pool card, in screen coords."""
    cx0, cy0, _, _ = card_rect(row, col)
    px0, py0, px1, py1 = PORTRAIT_BOX
    return cx0 + px0, cy0 + py0, cx0 + px1, cy0 + py1


def slot_rect(name: str) -> tuple[int, int, int, int]:
    """Return the (x0, y0, x1, y1) rectangle of a draft pick slot."""
    x0 = PICK_SLOTS[name]
    y0, y1 = SLOT_BOX
    return x0, y0, x0 + SLOT_W, y1
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_geometry.py -q
```

Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/ \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/
git commit -m "feat(solstice): geometry constants for Solstice Clash screens

Single source of truth for card grid, portrait crops and pick slots at
1080x1920, with fixture frames for device-free testing."
```

---

### Task 2: Portrait extraction + ban-overlay detection

**Files:**
- Create: `.../services/solstice/heroes.py`
- Create: `.../templates/event/solstice_clash/anchors/ban_glyph_red.png` (cut in Step 0)
- Create: `.../templates/event/solstice_clash/anchors/ban_glyph_blue.png` (cut in Step 0)
- Create: `tests/games/afk_journey/services/solstice/test_heroes_extract.py`

**Interfaces:**
- Consumes: `geometry.card_rect`, `geometry.portrait_rect`, `geometry.PORTRAIT_PAD`
- Produces:
  - `extract_portrait(img: np.ndarray, row: int, col: int) -> np.ndarray` (grayscale probe)
  - `extract_padded_portrait(img: np.ndarray, row: int, col: int) -> np.ndarray` (grayscale, padded - library entry)
  - `is_banned(img: np.ndarray, row: int, col: int, ban_glyphs: list[np.ndarray]) -> bool`
  - `load_ban_glyphs(anchor_dir: Path) -> list[np.ndarray]`
  - `BAN_MATCH_THRESHOLD: float = 0.60`
  - `is_blank(portrait: np.ndarray) -> bool`

- [ ] **Step 0: Cut the two ban-slash glyph templates**

The banned cards in `selecting.png` are at (3,0) red and (3,1) blue. **Both** variants are
required: they render differently in grayscale, and matching only the red glyph scores 0.18
against a blue ban. Write this snippet to `/tmp/cut_glyphs.py` and run it from the repo root:

```python
import cv2, os
ROWS = [665, 900, 1135, 1370]
COLS = [155, 315, 475, 635, 795]
SRC = 'src-tauri/src-python/tests/games/afk_journey/services/solstice/data/selecting.png'
OUT = ('src-tauri/src-python/adb_auto_player/games/afk_journey/templates/'
       'event/solstice_clash/anchors')
img = cv2.imread(SRC)
assert img is not None, SRC
os.makedirs(OUT, exist_ok=True)
for name, (r, c) in {'ban_glyph_red': (3, 0), 'ban_glyph_blue': (3, 1)}.items():
    x, y = COLS[c], ROWS[r]
    card = img[y:y + 190, x:x + 150]
    glyph = cv2.cvtColor(card[45:165, 20:130], cv2.COLOR_BGR2GRAY)[10:-10, 10:-10]
    cv2.imwrite(os.path.join(OUT, name + '.png'), glyph)
    print(name, glyph.shape[1], 'x', glyph.shape[0])
```

Run: `cd /mnt/docs/adbautoplayer && uv run --group dev python /tmp/cut_glyphs.py`

Expected: two 90x100 grayscale PNGs written.

- [ ] **Step 1: Write the failing test**

Create `tests/games/afk_journey/services/solstice/test_heroes_extract.py`:

```python
"""Portrait extraction and overlay detection from real captured frames."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.solstice import heroes

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def banning() -> np.ndarray:
    img = cv2.imread(str(DATA / "banning.png"))
    assert img is not None, "fixture banning.png missing"
    return img


@pytest.fixture(scope="module")
def selecting() -> np.ndarray:
    img = cv2.imread(str(DATA / "selecting.png"))
    assert img is not None, "fixture selecting.png missing"
    return img


@pytest.fixture(scope="module")
def ban_glyphs() -> list:
    anchors = (
        Path(__file__).parents[5]
        / "adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors"
    )
    glyphs = heroes.load_ban_glyphs(anchors)
    assert len(glyphs) == 2, f"expected red+blue ban glyphs under {anchors}"
    return glyphs


def test_probe_is_grayscale_and_correctly_sized(banning):
    p = heroes.extract_portrait(banning, 0, 0)
    assert p.ndim == 2
    assert p.shape == (120, 110)


def test_padded_entry_is_larger_than_probe(banning):
    probe = heroes.extract_portrait(banning, 0, 0)
    padded = heroes.extract_padded_portrait(banning, 0, 0)
    assert padded.shape[0] > probe.shape[0]
    assert padded.shape[1] > probe.shape[1]


def test_real_portraits_are_not_blank(banning):
    """Every one of the 20 banning-screen cards holds a real portrait."""
    blanks = [
        (r, c)
        for r in range(4)
        for c in range(5)
        if heroes.is_blank(heroes.extract_portrait(banning, r, c))
    ]
    assert blanks == []


def test_banned_cards_detected(selecting, ban_glyphs):
    """selecting.png has bans at row 3 col 0 (red) and row 3 col 1 (blue)."""
    assert heroes.is_banned(selecting, 3, 0, ban_glyphs) is True
    assert heroes.is_banned(selecting, 3, 1, ban_glyphs) is True


def test_no_false_positives_on_any_other_card(selecting, ban_glyphs):
    """Regression guard: a colour-cast heuristic flagged 13 of 20 here."""
    flagged = [
        (r, c)
        for r in range(4)
        for c in range(5)
        if heroes.is_banned(selecting, r, c, ban_glyphs)
    ]
    assert flagged == [(3, 0), (3, 1)]


def test_no_bans_flagged_on_the_banning_screen(banning, ban_glyphs):
    """banning.png predates any ban, so nothing may be flagged."""
    flagged = [
        (r, c)
        for r in range(4)
        for c in range(5)
        if heroes.is_banned(banning, r, c, ban_glyphs)
    ]
    assert flagged == []


def test_empty_glyph_list_reports_nothing_banned(selecting):
    assert heroes.is_banned(selecting, 3, 0, []) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_extract.py -q
```

Expected: FAIL - `cannot import name 'heroes'`

- [ ] **Step 3: Write the implementation**

Create `services/solstice/heroes.py`:

```python
"""Hero card extraction, matching and library management for Solstice Clash."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import geometry as geo

# A portrait whose grayscale standard deviation falls below this is empty
# UI space rather than hero art (measured: real portraits >= 22, blanks <= 13).
_BLANK_STD_THRESHOLD: float = 18.0

# A banned card is covered by a large circle-slash graphic. It is detected by
# template-matching that glyph, NOT by a colour cast: colour heuristics
# false-positive on red- and blue-haired heroes (10 of 20 cards on a real
# banning frame). Red and blue variants render differently in grayscale, so
# both templates are required.
BAN_MATCH_THRESHOLD: float = 0.60


def _to_gray(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def extract_portrait(img: np.ndarray, row: int, col: int) -> np.ndarray:
    """Return the grayscale portrait probe for a pool card."""
    x0, y0, x1, y1 = geo.portrait_rect(row, col)
    return _to_gray(img[y0:y1, x0:x1])


def extract_padded_portrait(img: np.ndarray, row: int, col: int) -> np.ndarray:
    """Return a padded grayscale portrait, for storage as a library template.

    The padding gives a live probe room to be searched within this image,
    absorbing small alignment differences between captures.
    """
    x0, y0, x1, y1 = geo.portrait_rect(row, col)
    p = geo.PORTRAIT_PAD
    h, w = img.shape[:2]
    return _to_gray(
        img[max(0, y0 - p) : min(h, y1 + p), max(0, x0 - p) : min(w, x1 + p)]
    )


def is_blank(portrait: np.ndarray) -> bool:
    """True when a portrait crop holds no hero art."""
    return float(portrait.std()) < _BLANK_STD_THRESHOLD


def is_banned(
    img: np.ndarray,
    row: int,
    col: int,
    ban_glyphs: list[np.ndarray],
) -> bool:
    """True when a pool card carries the ban circle-slash overlay.

    Measured separation on real frames: banned cards score 1.00, every other
    card <= 0.35, so the 0.60 threshold has a wide margin. An empty glyph list
    means bans cannot be detected, so nothing is reported as banned.
    """
    portrait = extract_portrait(img, row, col)
    for glyph in ban_glyphs:
        if glyph.shape[0] > portrait.shape[0] or glyph.shape[1] > portrait.shape[1]:
            continue
        score = float(cv2.matchTemplate(portrait, glyph, cv2.TM_CCOEFF_NORMED).max())
        if score >= BAN_MATCH_THRESHOLD:
            return True
    return False


def load_ban_glyphs(anchor_dir: "Path") -> list[np.ndarray]:
    """Load the red and blue ban-slash templates as grayscale."""
    glyphs = []
    for name in ("ban_glyph_red", "ban_glyph_blue"):
        img = cv2.imread(str(anchor_dir / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            glyphs.append(img)
    return glyphs
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_extract.py -q
```

Expected: PASS, 7 tests. If `test_no_false_positives_on_any_other_card` fails, the ban glyph templates are wrong or missing - re-cut them (Task 2 Step 0). Do **not** relax `BAN_MATCH_THRESHOLD`: measured separation is 1.00 for bans versus 0.35 for every other card, so a failure means bad templates, not a bad threshold.

- [ ] **Step 5: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/heroes.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_extract.py
git commit -m "feat(solstice): portrait extraction and ban-overlay detection"
```

---

### Task 3: Hero matcher (correct search direction)

**Files:**
- Modify: `.../services/solstice/heroes.py`
- Create: `tests/games/afk_journey/services/solstice/test_heroes_match.py`

**Interfaces:**
- Consumes: `extract_portrait`, `extract_padded_portrait` from Task 2
- Produces:
  - `HeroMatch` dataclass: `slug: str | None`, `score: float`, `runner_up: float`, `status: str` (`"ok"` | `"unknown"`)
  - `match_portrait(probe: np.ndarray, library: dict[str, np.ndarray]) -> HeroMatch`
  - Constants `ACCEPT_SCORE = 0.90`, `ACCEPT_MARGIN = 0.10`

- [ ] **Step 1: Write the failing test**

Create `tests/games/afk_journey/services/solstice/test_heroes_match.py`:

```python
"""Hero matching: direction, thresholds and the unknown path."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.solstice import heroes

DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def banning() -> np.ndarray:
    img = cv2.imread(str(DATA / "banning.png"))
    assert img is not None
    return img


def _library_from(img, coords) -> dict[str, np.ndarray]:
    return {
        f"hero_{i:03d}": heroes.extract_padded_portrait(img, r, c)
        for i, (r, c) in enumerate(coords, start=1)
    }


def test_identical_card_matches_itself_near_perfectly(banning):
    lib = _library_from(banning, [(0, 0)])
    probe = heroes.extract_portrait(banning, 0, 0)
    m = heroes.match_portrait(probe, lib)
    assert m.status == "ok"
    assert m.slug == "hero_001"
    assert m.score > 0.99


def test_different_cards_are_well_separated(banning):
    lib = _library_from(banning, [(0, 0), (1, 1), (2, 2), (3, 3)])
    probe = heroes.extract_portrait(banning, 2, 2)
    m = heroes.match_portrait(probe, lib)
    assert m.slug == "hero_003"
    assert m.score - m.runner_up > 0.30


def test_unknown_when_library_has_no_match(banning):
    lib = _library_from(banning, [(0, 0)])
    probe = heroes.extract_portrait(banning, 3, 4)
    m = heroes.match_portrait(probe, lib)
    assert m.status == "unknown"
    assert m.slug is None


def test_unknown_on_empty_library(banning):
    m = heroes.match_portrait(heroes.extract_portrait(banning, 0, 0), {})
    assert m.status == "unknown"
    assert m.slug is None
    assert m.score == 0.0


def test_probe_larger_than_entry_does_not_raise(banning):
    """Guards the direction: an oversized probe must be skipped, not crash."""
    lib = {"hero_001": np.zeros((10, 10), dtype=np.uint8)}
    m = heroes.match_portrait(heroes.extract_portrait(banning, 0, 0), lib)
    assert m.status == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_match.py -q
```

Expected: FAIL - `module has no attribute 'match_portrait'`

- [ ] **Step 3: Write the implementation**

Append to `services/solstice/heroes.py`:

```python
from dataclasses import dataclass

ACCEPT_SCORE: float = 0.90
ACCEPT_MARGIN: float = 0.10


@dataclass(frozen=True)
class HeroMatch:
    """Outcome of matching one portrait against the library."""

    slug: str | None
    score: float
    runner_up: float
    status: str  # "ok" | "unknown"


def match_portrait(
    probe: np.ndarray,
    library: dict[str, np.ndarray],
) -> HeroMatch:
    """Identify a portrait against the hero library.

    Direction matters: the padded library entry is the *base* image and the
    live probe is the sliding *template*, so small alignment differences are
    absorbed by the search. Inverting this yields ~0.77 instead of ~0.999 and
    raises ValueError whenever the template is larger than the base.

    Returns status "unknown" unless the best score clears ACCEPT_SCORE and
    beats the runner-up by at least ACCEPT_MARGIN.
    """
    best_slug: str | None = None
    best = 0.0
    second = 0.0

    for slug, entry in library.items():
        if entry.shape[0] < probe.shape[0] or entry.shape[1] < probe.shape[1]:
            # Probe cannot be searched inside a smaller entry - skip rather
            # than let cv2 raise.
            continue
        score = float(cv2.matchTemplate(entry, probe, cv2.TM_CCOEFF_NORMED).max())
        if score > best:
            second = best
            best, best_slug = score, slug
        elif score > second:
            second = score

    if best_slug is not None and best >= ACCEPT_SCORE and (best - second) >= ACCEPT_MARGIN:
        return HeroMatch(best_slug, best, second, "ok")
    return HeroMatch(None, best, second, "unknown")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_match.py -q
```

Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/heroes.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_match.py
git commit -m "feat(solstice): hero matcher with fixed search direction and unknown path

Padded library entry is the base image, live probe the sliding template.
Accepts only at score >= 0.90 with a >= 0.10 margin; never guesses."
```

---

### Task 4: Screen anchors + classifier

**Files:**
- Create: `.../services/solstice/screens.py`
- Create: `.../services/solstice/vision.py`
- Create: `.../templates/event/solstice_clash/anchors/` (4 PNGs, cut in Step 1)
- Create: `tests/games/afk_journey/services/solstice/test_vision.py`

**Interfaces:**
- Consumes: `geometry`
- Produces:
  - `SolsticeScreen` enum: `BANNING`, `SELECTING`, `SPECTATE_DRAFT`, `UNKNOWN`
  - `classify(img: np.ndarray, anchors: dict[SolsticeScreen, np.ndarray]) -> SolsticeScreen`
  - `load_anchors(anchor_dir: Path) -> dict[SolsticeScreen, np.ndarray]`

- [ ] **Step 1: Cut anchor templates from the fixtures**

Each anchor is a small, high-contrast, position-stable region unique to one screen.

```bash
cd /mnt/docs/adbautoplayer
ANCH=src-tauri/src-python/adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors
mkdir -p "$ANCH"
DATA=src-tauri/src-python/tests/games/afk_journey/services/solstice/data
python3 - <<'PY'
import cv2
DATA="src-tauri/src-python/tests/games/afk_journey/services/solstice/data"
ANCH="src-tauri/src-python/adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors"
cuts = [
    ("banning_label",   f"{DATA}/banning.png",         20,  80,  40, 300),
    ("selecting_label", f"{DATA}/selecting.png",       25,  85, 780,1060),
    ("theme_strip",     f"{DATA}/selecting.png",      590, 640, 300, 800),
    ("betting_bar",     f"{DATA}/spectate_draft.png",1300,1360,  60, 350),
]
for name, src, y0, y1, x0, x1 in cuts:
    img = cv2.imread(src)
    assert img is not None, src
    cv2.imwrite(f"{ANCH}/{name}.png", img[y0:y1, x0:x1])
    print(f"{name}: {x1-x0}x{y1-y0}")
PY
```

Expected: four PNGs written, dimensions printed.

- [ ] **Step 2: Write the failing test**

Create `tests/games/afk_journey/services/solstice/test_vision.py`:

```python
"""Screen classification via template anchors (never pixel statistics)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.solstice.screens import SolsticeScreen
from adb_auto_player.games.afk_journey.services.solstice import vision

DATA = Path(__file__).parent / "data"
ANCHORS = (
    Path(__file__).parents[5]
    / "adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors"
)


@pytest.fixture(scope="module")
def anchors() -> dict:
    a = vision.load_anchors(ANCHORS)
    assert a, f"no anchors found under {ANCHORS}"
    return a


def _img(name: str) -> np.ndarray:
    img = cv2.imread(str(DATA / name))
    assert img is not None, name
    return img


def test_banning_screen_classified(anchors):
    assert vision.classify(_img("banning.png"), anchors) is SolsticeScreen.BANNING


def test_selecting_screen_classified(anchors):
    assert vision.classify(_img("selecting.png"), anchors) is SolsticeScreen.SELECTING


def test_spectate_draft_classified(anchors):
    assert (
        vision.classify(_img("spectate_draft.png"), anchors)
        is SolsticeScreen.SPECTATE_DRAFT
    )


def test_outworld_is_not_mistaken_for_a_pool_screen(anchors):
    """The night-time outworld must never classify as a Solstice screen.

    Regression guard: brightness heuristics wrongly matched result and
    outworld frames during design.
    """
    assert vision.classify(_img("outworld_night.png"), anchors) is SolsticeScreen.UNKNOWN


def test_blank_image_is_unknown(anchors):
    blank = np.zeros((1920, 1080, 3), dtype=np.uint8)
    assert vision.classify(blank, anchors) is SolsticeScreen.UNKNOWN
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_vision.py -q
```

Expected: FAIL - `No module named '...solstice.screens'`

- [ ] **Step 4: Write the implementation**

Create `services/solstice/screens.py`:

```python
"""Solstice Clash screen identities and their anchor filenames."""

from __future__ import annotations

from enum import Enum


class SolsticeScreen(Enum):
    """A recognised Solstice Clash screen."""

    BANNING = "banning"
    SELECTING = "selecting"
    SPECTATE_DRAFT = "spectate_draft"
    UNKNOWN = "unknown"


# Anchor file (without .png) that uniquely identifies each screen.
# Order matters: the first match wins, so the most specific anchors come first.
ANCHOR_FILES: tuple[tuple[SolsticeScreen, str], ...] = (
    (SolsticeScreen.BANNING, "banning_label"),
    (SolsticeScreen.SELECTING, "selecting_label"),
    (SolsticeScreen.SPECTATE_DRAFT, "betting_bar"),
)
```

Create `services/solstice/vision.py`:

```python
"""Screen classification for Solstice Clash, using template anchors only.

Pixel-statistic heuristics are deliberately not used: during design they
misclassified result and outworld frames as draft screens three times.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .screens import ANCHOR_FILES, SolsticeScreen

# An anchor must match this well to identify a screen.
ANCHOR_THRESHOLD: float = 0.85


def load_anchors(anchor_dir: Path) -> dict[SolsticeScreen, np.ndarray]:
    """Load anchor templates as grayscale, keyed by the screen they identify."""
    anchors: dict[SolsticeScreen, np.ndarray] = {}
    for screen, filename in ANCHOR_FILES:
        path = anchor_dir / f"{filename}.png"
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            anchors[screen] = img
    return anchors


def classify(
    img: np.ndarray,
    anchors: dict[SolsticeScreen, np.ndarray],
) -> SolsticeScreen:
    """Identify a screenshot, returning UNKNOWN rather than guessing."""
    if img is None or img.ndim != 3:
        return SolsticeScreen.UNKNOWN
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    for screen, _ in ANCHOR_FILES:
        anchor = anchors.get(screen)
        if anchor is None:
            continue
        if anchor.shape[0] > gray.shape[0] or anchor.shape[1] > gray.shape[1]:
            continue
        score = float(cv2.matchTemplate(gray, anchor, cv2.TM_CCOEFF_NORMED).max())
        if score >= ANCHOR_THRESHOLD:
            return screen
    return SolsticeScreen.UNKNOWN
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_vision.py -q
```

Expected: PASS, 5 tests. If `test_outworld_is_not_mistaken_for_a_pool_screen` fails, an anchor is not specific enough - re-cut it tighter around unique text rather than raising the threshold.

- [ ] **Step 6: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/screens.py \
        src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/vision.py \
        src-tauri/src-python/adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors/ \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_vision.py
git commit -m "feat(solstice): anchor-based screen classifier

Template anchors only - includes a regression guard proving the night
outworld frame classifies as UNKNOWN rather than a pool screen."
```

---

### Task 5: SQLite store + committed manifest

**Files:**
- Create: `.../services/solstice/store.py`
- Create: `tests/games/afk_journey/services/solstice/test_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `SolsticeStore(db_path: Path)` with `.conn`
  - `SolsticeStore.create_schema() -> None`
  - `SolsticeStore.reconcile_library(manifest: dict) -> int` (returns rows upserted)
  - `SolsticeStore.get_hero(slug: str) -> sqlite3.Row | None`
  - `load_manifest(path: Path) -> dict`
  - `write_manifest(path: Path, manifest: dict) -> None`

Manifest shape (committed as `hero_library.json`):

```json
{
  "version": 1,
  "heroes": [
    {"slug": "hero_001", "label": null, "is_skin_of": null, "templates": ["hero_001.png"]}
  ]
}
```

- [ ] **Step 1: Write the failing test**

Create `tests/games/afk_journey/services/solstice/test_store.py`:

```python
"""SQLite schema and manifest reconciliation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from adb_auto_player.games.afk_journey.services.solstice.store import (
    SolsticeStore,
    load_manifest,
    write_manifest,
)


@pytest.fixture()
def store(tmp_path: Path) -> SolsticeStore:
    s = SolsticeStore(tmp_path / "solstice.db")
    s.create_schema()
    return s


def _manifest(*entries) -> dict:
    return {"version": 1, "heroes": list(entries)}


def test_schema_creates_expected_tables(store):
    names = {
        r[0]
        for r in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"match", "match_hero", "match_odds", "hero", "hero_template"} <= names


def test_hero_slug_is_unique(store):
    store.conn.execute("INSERT INTO hero(slug, label) VALUES ('hero_001', 'A')")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO hero(slug, label) VALUES ('hero_001', 'B')")


def test_reconcile_inserts_new_heroes(store):
    n = store.reconcile_library(
        _manifest({"slug": "hero_001", "label": "Tilaya", "is_skin_of": None,
                   "templates": ["hero_001.png"]})
    )
    assert n == 1
    assert store.get_hero("hero_001")["label"] == "Tilaya"


def test_reconcile_runs_every_startup_not_only_when_empty(store):
    """A stale local DB must not shadow an updated committed manifest."""
    store.reconcile_library(
        _manifest({"slug": "hero_001", "label": None, "is_skin_of": None,
                   "templates": ["hero_001.png"]})
    )
    store.reconcile_library(
        _manifest({"slug": "hero_001", "label": "Tilaya", "is_skin_of": None,
                   "templates": ["hero_001.png"]})
    )
    assert store.get_hero("hero_001")["label"] == "Tilaya"


def test_manifest_wins_on_label_conflict(store):
    store.conn.execute("INSERT INTO hero(slug, label) VALUES ('hero_002', 'LocalGuess')")
    store.reconcile_library(
        _manifest({"slug": "hero_002", "label": "Zorya", "is_skin_of": None,
                   "templates": []})
    )
    assert store.get_hero("hero_002")["label"] == "Zorya"


def test_local_only_slug_is_preserved(store):
    store.conn.execute("INSERT INTO hero(slug, label) VALUES ('hero_999', NULL)")
    store.reconcile_library(
        _manifest({"slug": "hero_001", "label": "A", "is_skin_of": None,
                   "templates": []})
    )
    assert store.get_hero("hero_999") is not None


def test_manifest_round_trips(tmp_path: Path):
    p = tmp_path / "hero_library.json"
    m = _manifest({"slug": "hero_001", "label": "Tilaya", "is_skin_of": None,
                   "templates": ["hero_001.png"]})
    write_manifest(p, m)
    assert load_manifest(p) == m


def test_load_manifest_missing_file_returns_empty(tmp_path: Path):
    assert load_manifest(tmp_path / "nope.json") == {"version": 1, "heroes": []}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_store.py -q
```

Expected: FAIL - `No module named '...solstice.store'`

- [ ] **Step 3: Write the implementation**

Create `services/solstice/store.py`:

```python
"""SQLite storage and committed-manifest reconciliation for Solstice Clash.

SQLite is the local source of truth for *match data*. The committed
`hero_library.json` manifest is the source of truth for the *library*
(slugs, labels, skin links). Reconciliation runs on every startup so a stale
local database cannot shadow an updated committed manifest.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hero (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    label       TEXT,
    is_skin_of  TEXT
);

CREATE TABLE IF NOT EXISTS hero_template (
    id          INTEGER PRIMARY KEY,
    hero_slug   TEXT NOT NULL,
    image_path  TEXT NOT NULL,
    sightings   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (hero_slug, image_path)
);

CREATE TABLE IF NOT EXISTS match (
    id             INTEGER PRIMARY KEY,
    source         TEXT NOT NULL,
    captured_at    TEXT NOT NULL,
    theme          TEXT,
    balance_epoch  TEXT,
    blue_player    TEXT,
    blue_rating    INTEGER,
    blue_rank      INTEGER,
    red_player     TEXT,
    red_rating     INTEGER,
    red_rank       INTEGER,
    outcome        TEXT,
    outcome_source TEXT,
    natural_key    TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS match_hero (
    id                 INTEGER PRIMARY KEY,
    match_id           INTEGER NOT NULL REFERENCES match(id),
    side               TEXT NOT NULL,
    slot               TEXT NOT NULL,
    hero_slug          TEXT,
    recognition_status TEXT NOT NULL,
    confidence         REAL,
    runner_up_score    REAL,
    crop_path          TEXT
);

CREATE TABLE IF NOT EXISTS match_odds (
    id          INTEGER PRIMARY KEY,
    match_id    INTEGER NOT NULL REFERENCES match(id),
    sampled_at  TEXT NOT NULL,
    blue_pool   INTEGER,
    red_pool    INTEGER,
    blue_odds   REAL,
    red_odds    REAL,
    spectators  INTEGER
);
"""

_EMPTY_MANIFEST: dict[str, Any] = {"version": 1, "heroes": []}


def load_manifest(path: Path) -> dict[str, Any]:
    """Load the committed hero manifest, or an empty one if absent."""
    if not path.exists():
        return dict(_EMPTY_MANIFEST)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the hero manifest, sorted for a stable diff."""
    manifest = dict(manifest)
    manifest["heroes"] = sorted(manifest.get("heroes", []), key=lambda h: h["slug"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


class SolsticeStore:
    """SQLite store for Solstice Clash match data and hero library."""

    def __init__(self, db_path: Path) -> None:
        """Open (creating if needed) the SQLite database at db_path."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def create_schema(self) -> None:
        """Create all tables if they do not already exist."""
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def reconcile_library(self, manifest: dict[str, Any]) -> int:
        """Upsert manifest heroes into SQLite, keyed on the immutable slug.

        Runs on every startup. The manifest wins on conflict; slugs that exist
        only locally are left untouched so newly-discovered heroes survive
        until they are labelled and committed.
        """
        rows = manifest.get("heroes", [])
        for h in rows:
            self.conn.execute(
                """
                INSERT INTO hero (slug, label, is_skin_of)
                VALUES (:slug, :label, :is_skin_of)
                ON CONFLICT(slug) DO UPDATE SET
                    label = excluded.label,
                    is_skin_of = excluded.is_skin_of
                """,
                {
                    "slug": h["slug"],
                    "label": h.get("label"),
                    "is_skin_of": h.get("is_skin_of"),
                },
            )
            for image_path in h.get("templates", []):
                self.conn.execute(
                    """
                    INSERT INTO hero_template (hero_slug, image_path)
                    VALUES (?, ?)
                    ON CONFLICT(hero_slug, image_path) DO NOTHING
                    """,
                    (h["slug"], image_path),
                )
        self.conn.commit()
        return len(rows)

    def get_hero(self, slug: str) -> sqlite3.Row | None:
        """Return the hero row for a slug, or None."""
        cur = self.conn.execute("SELECT * FROM hero WHERE slug = ?", (slug,))
        return cur.fetchone()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_store.py -q
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/store.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_store.py
git commit -m "feat(solstice): SQLite schema + manifest reconciliation

Slug-keyed upsert runs every startup so a stale local DB cannot shadow the
committed library; local-only slugs are preserved for labelling."
```

---

### Task 6: Library builder with frequency filter

**Files:**
- Modify: `.../services/solstice/heroes.py`
- Create: `tests/games/afk_journey/services/solstice/test_heroes_library.py`

**Interfaces:**
- Consumes: `extract_portrait`, `extract_padded_portrait`, `is_blank`, `is_banned`, `match_portrait` (Tasks 2-3); `SolsticeScreen`, `classify` (Task 4)
- Produces:
  - `MIN_SIGHTINGS: int = 6`
  - `harvest_candidates(frames: list[np.ndarray], anchors: dict) -> list[tuple[np.ndarray, np.ndarray, int]]` returning `(probe, padded, sightings)`
  - `build_library(frames, anchors, min_sightings=MIN_SIGHTINGS) -> dict[str, np.ndarray]`

- [ ] **Step 1: Write the failing test**

Create `tests/games/afk_journey/services/solstice/test_heroes_library.py`:

```python
"""Library construction: frequency filtering and screen gating."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from adb_auto_player.games.afk_journey.services.solstice import heroes, vision

DATA = Path(__file__).parent / "data"
ANCHORS = (
    Path(__file__).parents[5]
    / "adb_auto_player/games/afk_journey/templates/event/solstice_clash/anchors"
)


@pytest.fixture(scope="module")
def anchors() -> dict:
    return vision.load_anchors(ANCHORS)


@pytest.fixture(scope="module")
def ban_glyphs() -> list:
    glyphs = heroes.load_ban_glyphs(ANCHORS)
    assert len(glyphs) == 2
    return glyphs


def _img(name: str) -> np.ndarray:
    img = cv2.imread(str(DATA / name))
    assert img is not None, name
    return img


def test_single_banning_frame_yields_twenty_candidates(anchors, ban_glyphs):
    """banning.png predates any ban, so all 20 cards are harvestable."""
    cands = heroes.harvest_candidates([_img("banning.png")], anchors, ban_glyphs)
    assert len(cands) == 20


def test_repeated_frames_increase_sightings_not_candidates(anchors, ban_glyphs):
    frames = [_img("banning.png")] * 8
    cands = heroes.harvest_candidates(frames, anchors, ban_glyphs)
    assert len(cands) == 20
    assert all(sightings == 8 for _, _, sightings in cands)


def test_frequency_filter_drops_one_off_candidates(anchors):
    """A single frame gives 1 sighting each, below the threshold of 6."""
    lib = heroes.build_library([_img("banning.png")], anchors, min_sightings=6)
    assert lib == {}


def test_frequency_filter_keeps_recurring_candidates(anchors):
    lib = heroes.build_library([_img("banning.png")] * 6, anchors, min_sightings=6)
    assert len(lib) == 20
    assert all(slug.startswith("hero_") for slug in lib)


def test_non_pool_screens_contribute_nothing(anchors):
    """Regression guard: outworld frames polluted the library during design."""
    lib = heroes.build_library([_img("outworld_night.png")] * 10, anchors)
    assert lib == {}


def test_banned_cards_are_excluded(anchors, ban_glyphs):
    """selecting.png has exactly 2 banned cards, so it yields 18 not 20."""
    cands = heroes.harvest_candidates([_img("selecting.png")], anchors, ban_glyphs)
    assert len(cands) == 18
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_library.py -q
```

Expected: FAIL - `module has no attribute 'harvest_candidates'`

- [ ] **Step 3: Write the implementation**

Append to `services/solstice/heroes.py`:

```python
from .screens import SolsticeScreen
from .vision import classify

# A candidate must recur in at least this many frames before entering the
# library. Transient mid-animation crops appear once and are discarded
# (890 of 1045 candidates during design testing).
MIN_SIGHTINGS: int = 6

# Two candidates matching this well are treated as the same hero.
_DEDUP_THRESHOLD: float = 0.90

_POOL_SCREENS = frozenset(
    {SolsticeScreen.BANNING, SolsticeScreen.SELECTING, SolsticeScreen.SPECTATE_DRAFT}
)


def harvest_candidates(
    frames: list[np.ndarray],
    anchors: dict,
    ban_glyphs: list[np.ndarray] | None = None,
) -> list[tuple[np.ndarray, np.ndarray, int]]:
    """Collect distinct portraits across frames with their sighting counts.

    Only pool screens contribute; blank slots and banned cards are skipped.
    Returns (probe, padded, sightings) triples.
    """
    ban_glyphs = ban_glyphs or []
    probes: list[np.ndarray] = []
    padded: list[np.ndarray] = []
    counts: list[int] = []

    for img in frames:
        screen = classify(img, anchors)
        if screen not in _POOL_SCREENS:
            continue
        rows = 3 if screen is SolsticeScreen.SPECTATE_DRAFT else 4
        for row in range(rows):
            for col in range(5):
                if is_banned(img, row, col, ban_glyphs):
                    continue
                probe = extract_portrait(img, row, col)
                if is_blank(probe):
                    continue
                hit = -1
                for i, known in enumerate(probes):
                    entry = padded[i]
                    if entry.shape[0] < probe.shape[0] or entry.shape[1] < probe.shape[1]:
                        continue
                    score = float(
                        cv2.matchTemplate(entry, probe, cv2.TM_CCOEFF_NORMED).max()
                    )
                    if score > _DEDUP_THRESHOLD:
                        hit = i
                        break
                if hit >= 0:
                    counts[hit] += 1
                else:
                    probes.append(probe)
                    padded.append(extract_padded_portrait(img, row, col))
                    counts.append(1)

    return list(zip(probes, padded, counts, strict=True))


def build_library(
    frames: list[np.ndarray],
    anchors: dict,
    min_sightings: int = MIN_SIGHTINGS,
    ban_glyphs: list[np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """Build a slug-keyed library, keeping only sufficiently recurring cards."""
    kept = [
        (padded, sightings)
        for _, padded, sightings in harvest_candidates(frames, anchors, ban_glyphs)
        if sightings >= min_sightings
    ]
    kept.sort(key=lambda t: -t[1])
    return {f"hero_{i:03d}": padded for i, (padded, _) in enumerate(kept, start=1)}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_library.py -q
```

Expected: PASS, 6 tests. If `test_banned_cards_are_excluded` reports 20, the ban glyphs are not being loaded - check `ban_glyph_red.png` and `ban_glyph_blue.png` exist under the anchors directory and that the `ban_glyphs` fixture asserts a length of 2.

- [ ] **Step 5: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/heroes.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_heroes_library.py
git commit -m "feat(solstice): library builder with screen gating and frequency filter

Only pool screens contribute; banned and blank cards are skipped; candidates
must recur in >= 6 frames, which eliminated 890 of 1045 artefacts in testing."
```

---

### Task 7: Full-suite regression + docs

**Files:**
- Modify: `docs/superpowers/plans/2026-07-25-solstice-clash-phase1.md` (tick boxes as you go)
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6
- Produces: no new code interfaces

- [ ] **Step 1: Run the entire test suite**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest -q 2>&1 | tail -20
```

Expected: all pre-existing tests still pass (97 in `test_hero_scanner.py` alone) **plus** the ~37 new Solstice tests. Zero failures. If anything unrelated broke, fix it before continuing - do not proceed with a red suite.

- [ ] **Step 2: Write the service README**

Create `services/solstice/README.md`:

```markdown
# Solstice Clash services

Vision, hero library and storage for the AFK Journey "Solstice Clash" event.

| Module | Responsibility |
|---|---|
| `geometry.py` | Every pixel coordinate, at 1080x1920. Change here if the UI shifts. |
| `screens.py` | Screen identities and their anchor filenames. |
| `vision.py` | Screen classification from template anchors. |
| `heroes.py` | Portrait extraction, matching, library construction. |
| `store.py` | SQLite schema and committed-manifest reconciliation. |

## Rules that are easy to get wrong

- **Matching direction.** `cv2.matchTemplate(base=<padded library entry>,
  template=<live probe>)`. Inverting it gives ~0.77 instead of ~0.999 and
  raises when the template is larger than the base. Do not route hero matching
  through `game_find_template_match`, which uses the opposite orientation.
- **No pixel-statistic screen detection.** Use anchors. Brightness heuristics
  misclassified result and outworld frames repeatedly during design.
- **`hero.slug` is immutable.** Only `label` changes. Skins link with
  `is_skin_of`; renaming a slug would invalidate every historical
  `natural_key`.
- **Never guess a hero.** Below 0.90 score or 0.10 margin, record `unknown`.

## Testing

All tests run without a device against committed fixture frames:

    uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice -q
```

- [ ] **Step 3: Commit**

```bash
cd /mnt/docs/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/README.md \
        docs/superpowers/plans/2026-07-25-solstice-clash-phase1.md
git commit -m "docs(solstice): service README and plan progress"
```

---

## Deferred - explicitly NOT in this plan

**To Phase 1b** (completes the spec's Phase 1):
- OCR extraction of player names, ratings, ranks, theme, countdown, odds/pools, token balance
- VS-intro and result-screen classification (needs fixtures not committed here)
- The labelling pass: contact sheet, naming, manifest write-back
- The accuracy-measurement report against spec success criteria 1-3

**To Phase 2+** (beyond the spec's Phase 1):
- `SolsticeMixin` and its registration in the `AFKJourneyBase` inheritance list
- Match-row writing, `natural_key` computation, timeout inference
- Any navigation, automation, model or betting logic

## Notes for the implementer

- Fixture frames were captured on Waydroid at 1080x1920. Other resolutions are out of scope.
- `cv2.matchTemplate` with `TM_CCOEFF_NORMED` returns a 1x1 result when both images are the same size, which performs **no alignment search**. This is why library entries are padded.
- Anchor cutting (Task 4 Step 1) uses hand-measured rectangles. If an anchor proves insufficiently specific, re-cut it tighter around unique text; do not lower `ANCHOR_THRESHOLD`.
