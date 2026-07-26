> **COMPLETED 2026-07-26.** All seven tasks implemented, 39 tests, full pre-commit
> gate green, 582 tests pass repo-wide with no regressions. See
> `docs/solstice-clash/README.md` section 15 for what was delivered and what
> measurement changed along the way.

# Solstice Clash Phase 1 Implementation Plan (revised)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Supersedes** `2026-07-25-solstice-clash-phase1.md`. That plan assumed the hero library had to be
*discovered* from gameplay (auto-numbered `hero_001` slugs, frequency filtering, wiki-only art).
None of that is true any more: the library is already built, named, and committed. Read §"What
changed" before touching anything.

**Goal:** Build the vision layer that turns a Solstice Clash screenshot into identified heroes,
and the store that records matches - so Mode A/B/C can be built on top.

**Architecture:** Four services under `games/afk_journey/services/solstice/`:
`config.py` (loads geometry + tunables from `heroes.sqlite`, no hardcoded constants),
`icons.py` (decodes and caches the game icon library), `vision.py` (screen classification +
cell extraction + identification), `store.py` (match recording). No device automation in Phase 1 -
every task is testable from committed fixture frames.

**Tech Stack:** Python 3.12, OpenCV, numpy, `lz4.block`, `texture2ddecoder`, sqlite3, pytest.

---

## Global Constraints

- **Base resolution is 1080x1920.** `games/afk_journey/base.py:57` sets it and
  `game/_screenshot_mixin.py:110-111` forces the device to it. Every coordinate in
  `cell_registry` assumes it. Never measure from a rescaled screenshot.
- **Never auto-bet.** Phase 1 records and identifies only. Mode C displays odds; the player
  moves the slider. Auto-placing is a possible v2, never a default.
- **`unknown` is a first-class outcome.** Below threshold means "sit this round out", not "guess".
- **SQLite always works standalone.** Postgres sync may come later and must never be required.
- **Geometry and match tunables come from the DB** (`cell_registry`, `library_config`) - never
  hardcode a cell rectangle, a scale chain, an accept threshold or the gamma. A few decode-level
  constants stay in code because they are properties of the file format, not tunables:
  `ASTC_BLOCK`, `_FLATTEN_BG`. Anchor positions and the ban threshold live in code for Phase 1
  but **must move to `library_config` before the Phase 2 device loop**, since that is when they
  start varying with live conditions.
- **`hero.slug` is the identity.** `external_id` is the game's id, useful but never the key.
  Positional numbering (roster index, "#1-#20") must never reach the database.
- **Two new deps, both undeclared today:** `lz4` and `texture2ddecoder`. Add BOTH in Task 2
  (`uv add lz4 texture2ddecoder` from `src-tauri/`). UnityPy is not a project dependency, so
  `texture2ddecoder` does not arrive transitively.

---

## What changed since the superseded plan

| Superseded assumption | Reality now |
|---|---|
| Library discovered from gameplay, slugs auto-numbered `hero_001…` | The AST container format is decoded (1,123 icons extracted offline); all 95 **usable-roster** heroes have `external_id` + `game_icon`. The `hero` table holds 153 rows, 32 of which are NPCs/story characters with no game icon - that is expected, not a gap. |
| Wiki art is the source | **Game assets are PRIMARY** (`library_config.icon_priority = game,wiki`). Wiki is fallback. |
| Frequency filter needed to find real heroes | Unnecessary - the library is complete and named |
| Geometry hardcoded in a constants module | Measured and stored in `cell_registry` (32 cells, 3 types) |
| One cell type | Three, with different aspects: `locked_pick` 100x85, `draft_locked_pick` 100x74, `draft_card` 110x120 |
| Spectate and compete share geometry | **They do not** (best anchor match 0.763). Spectate is out of Phase 1. |
| Ban detection is critical for identification | Secondary - the pool is captured before bans. Still needed for pick/ban assist. |
| Colour-cast ban detector | Glyph templates; a third red variant is still uncut (see Task 5) |

Measured baselines this plan must not regress:

| Surface | Result |
|---|---|
| `locked_pick`, 54 cells, 9 matches | **54/54 correct**, median 0.9731, min 0.9249, margin median +0.460 |
| `draft_card`, 18 labelled cells | **18/18 >= 0.90**, median 0.9550 raw / 0.9718 with gamma |
| Accept rule | `score >= 0.70 AND margin >= 0.10` -> 47/54 accepted, zero bad matches admitted |

---

## File Structure

```
src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/
    __init__.py
    config.py     SolsticeConfig: cells, tunables, hero rows - all from heroes.sqlite
    icons.py      AST/LZ4/ASTC decode + gamma + in-memory library
    vision.py     classify_screen, extract_cells, identify_cell, identify_pool
    store.py      match / match_hero / match_odds recording
tests/games/afk_journey/services/solstice/
    conftest.py       fixtures: db path, fixture frames, library
    test_config.py
    test_icons.py
    test_vision.py
    test_store.py
    data/             committed fixture frames (see Task 1)
```

`data/solstice_clash/heroes.sqlite` is the shipped database. Note that `build_hero_db.py`
and `migrate.py` both resolve it relative to the script (`HERE`), not the cwd - if a task needs
them to act on a different copy, pass the path explicitly - **both take it as `argv[1]`**, and
`build_hero_db.py` writes its JSON/wikitext artefacts beside whichever database it targets. Icons are NOT committed
(430MB); `icons.py` reads them from a configurable directory, defaulting to the vault path.

---

## Task 1: Fixture frames + config service

**Files:**
- Create: `.../services/solstice/__init__.py`
- Create: `.../services/solstice/config.py`
- Create: `tests/.../solstice/conftest.py`
- Create: `tests/.../solstice/test_config.py`
- Create: `tests/.../solstice/data/` (3 frames, see Step 1)

**Interfaces:**
- Produces:
  - `SolsticeConfig.load(db_path: Path) -> SolsticeConfig`
  - `.cells(cell_type: str) -> list[Cell]` where `Cell` is a frozen dataclass
    `(name, cell_type, x0, y0, x1, y1, side, slot)`
  - `.tunable(key: str) -> str` and `.tunable_float(key: str) -> float`
  - `.scale_chain(cell_type: str) -> tuple[float, ...]`
  - `.heroes() -> dict[str, HeroRow]` keyed by slug, `HeroRow(slug, name, faction, external_id, game_icon, wiki_icon)`
  - `.resolve_alias(name: str) -> str | None` (slug or None)

- [x] **Step 1: Copy three fixture frames into the test data directory**

These are raw `adb exec-out screencap` frames at 1080x1920. They already exist:

```bash
cd /mnt/docs/adbautoplayer
mkdir -p src-tauri/src-python/tests/games/afk_journey/services/solstice/data
cp /tmp/solstice/draft.png      src-tauri/src-python/tests/games/afk_journey/services/solstice/data/draft_selecting.png
cp /tmp/solstice/prematch.png   src-tauri/src-python/tests/games/afk_journey/services/solstice/data/prematch_locked.png
cp /mnt/vault/solstice/frames/$(ls /mnt/vault/solstice/frames | head -1) \
   src-tauri/src-python/tests/games/afk_journey/services/solstice/data/spectate.png
python3 -c "
import cv2,glob
for f in sorted(glob.glob('src-tauri/src-python/tests/games/afk_journey/services/solstice/data/*.png')):
    print(f, cv2.imread(f).shape)"
```

Expected: three files, each `(1920, 1080, 3)`. If any is not, you have a rescaled image - stop
and re-capture. `draft_selecting.png` is the known stuck match: its 5x4 grid is
Indris/Viperian/Dionel/Laios/Reinier, Berial/Smokey & Meerky/Odie/Pang/Rowan,
Sinbad/Nerion/Parisa/Thador/Lily May, Granny Dahnie/Lyca/Cryonaia/Sonja/Tilaya, with
Berial (r1c0) and Tilaya (r3c4) banned, and Rowan/Lily May/Reinier already picked so their grid
cells render **skinned**.

- [x] **Step 2: Write the failing test**

`tests/games/afk_journey/services/solstice/conftest.py`:

```python
from pathlib import Path
import pytest

# conftest.py sits at src-tauri/src-python/tests/games/afk_journey/services/solstice/
# parents[5] = src-python, parents[6] = src-tauri, parents[7] = repo root.
REPO = Path(__file__).resolve().parents[7]
SRC = Path(__file__).resolve().parents[5] / "adb_auto_player"
DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"
DATA = Path(__file__).parent / "data"

@pytest.fixture(scope="session")
def db_path() -> Path:
    assert DB.exists(), f"database missing: {DB}"
    return DB

@pytest.fixture(scope="session")
def frames() -> dict[str, Path]:
    return {p.stem: p for p in DATA.glob("*.png")}
```

`tests/games/afk_journey/services/solstice/test_config.py`:

```python
from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig


def test_loads_all_three_cell_types(db_path):
    cfg = SolsticeConfig.load(db_path)
    assert len(cfg.cells("locked_pick")) == 6
    assert len(cfg.cells("draft_locked_pick")) == 6
    assert len(cfg.cells("draft_card")) == 20


def test_locked_pick_cells_have_expected_geometry(db_path):
    cfg = SolsticeConfig.load(db_path)
    cells = cfg.cells("locked_pick")
    assert [c.x0 for c in cells] == [62, 206, 349, 635, 779, 922]
    assert all((c.y0, c.y1) == (1495, 1580) for c in cells)
    assert [c.side for c in cells] == ["blue"] * 3 + ["red"] * 3


def test_draft_card_grid_is_an_exact_lattice(db_path):
    """Every column shares one x-range, every row one y-range."""
    cfg = SolsticeConfig.load(db_path)
    cells = sorted(cfg.cells("draft_card"), key=lambda c: c.slot)
    cols, rows = {}, {}
    for c in cells:
        r, col = divmod(c.slot - 1, 5)
        cols.setdefault(col, set()).add((c.x0, c.x1))
        rows.setdefault(r, set()).add((c.y0, c.y1))
    assert all(len(v) == 1 for v in cols.values())
    assert all(len(v) == 1 for v in rows.values())
    xs = sorted({c.x0 for c in cells})
    ys = sorted({c.y0 for c in cells})
    assert [b - a for a, b in zip(xs, xs[1:])] == [160, 160, 160, 160]
    assert [b - a for a, b in zip(ys, ys[1:])] == [235, 235, 235]


def test_tunables_present(db_path):
    cfg = SolsticeConfig.load(db_path)
    assert cfg.tunable_float("accept_score") == 0.70
    assert cfg.tunable_float("accept_margin") == 0.10
    assert cfg.tunable("icon_priority") == "game,wiki"
    assert cfg.scale_chain("locked_pick") == (1.01, 0.95, 1.08)
    assert cfg.scale_chain("draft_card") == (1.19, 1.10, 1.30)


def test_hero_rows_and_aliases(db_path):
    cfg = SolsticeConfig.load(db_path)
    heroes = cfg.heroes()
    assert heroes["sonja"].external_id == 66
    assert heroes["sonja"].game_icon == "spui_herohead_66.png"
    assert cfg.resolve_alias("Lucy Heartfilia") == "lucy"
    assert cfg.resolve_alias("Natsu Dragneel") == "natsu"
    assert cfg.resolve_alias("nobody at all") is None
```

- [x] **Step 3: Run it and watch it fail**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_config.py -q
```

Expected: FAIL, `ModuleNotFoundError: ... solstice.config`.

- [x] **Step 4: Implement `config.py`**

```python
"""Geometry and tunables, loaded from heroes.sqlite. No hardcoded constants.

Everything here was measured on raw 1080x1920 ADB frames. Changing a number means
re-measuring and updating the database, not editing code.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Cell:
    name: str
    cell_type: str
    x0: int
    y0: int
    x1: int
    y1: int
    side: str | None
    slot: int | None

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass(frozen=True)
class HeroRow:
    slug: str
    name: str
    faction: str | None
    external_id: int | None
    game_icon: str | None
    wiki_icon: str | None


class SolsticeConfig:
    def __init__(self, cells, tunables, heroes, aliases):
        self._cells = cells
        self._tunables = tunables
        self._heroes = heroes
        self._aliases = aliases

    @classmethod
    def load(cls, db_path: Path) -> "SolsticeConfig":
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cells: dict[str, list[Cell]] = {}
            for row in con.execute(
                "SELECT cell_name,cell_type,x0,y0,x1,y1,side,slot FROM cell_registry "
                "ORDER BY cell_type, slot"
            ):
                cells.setdefault(row[1], []).append(Cell(*row))
            tunables = dict(con.execute("SELECT key,value FROM library_config"))
            heroes = {
                r[0]: HeroRow(*r)
                for r in con.execute(
                    "SELECT slug,name,faction,external_id,game_icon,wiki_icon FROM hero"
                )
            }
            aliases = dict(con.execute("SELECT alias,hero_slug FROM hero_alias"))
        finally:
            con.close()
        return cls(cells, tunables, heroes, aliases)

    def cells(self, cell_type: str) -> list[Cell]:
        return list(self._cells.get(cell_type, ()))

    def tunable(self, key: str) -> str:
        return self._tunables[key]

    def tunable_float(self, key: str) -> float:
        return float(self._tunables[key])

    def scale_chain(self, cell_type: str) -> tuple[float, ...]:
        key = "scale_draft_card" if cell_type == "draft_card" else "scale_chain"
        return tuple(float(x) for x in self._tunables[key].split(","))

    def heroes(self) -> dict[str, HeroRow]:
        return dict(self._heroes)

    def resolve_alias(self, name: str) -> str | None:
        """Slug for an alias, an exact name, or a case-insensitive name. Else None."""
        if name in self._aliases:
            return self._aliases[name]
        lowered = name.lower()
        for slug, hero in self._heroes.items():
            if hero.name.lower() == lowered:
                return slug
        return None
```

- [x] **Step 5: Run the tests**

```bash
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_config.py -q
```

Expected: PASS, 5 tests.

- [x] **Step 6: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/ \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/
git commit -m "feat(solstice): config service reading geometry and tunables from the database"
```

---

## Task 2: Icon library (AST decode + gamma)

**Files:**
- Create: `.../services/solstice/icons.py`
- Create: `tests/.../solstice/test_icons.py`
- Modify: `src-tauri/pyproject.toml` (add `lz4`); `uv.lock` is at the REPO ROOT

**Interfaces:**
- Consumes: `SolsticeConfig` (for `gamma`, `icon_priority`, hero rows)
- Produces:
  - `decode_ast(path: Path) -> np.ndarray` - BGRA, correctly oriented
  - `IconLibrary.build(cfg, icon_dir: Path) -> IconLibrary`
  - `.entries() -> list[IconEntry]` where `IconEntry(slug, art_ref, art_kind, gray)`
  - `.for_slugs(slugs: set[str]) -> list[IconEntry]` (the pool-constrained subset)

- [x] **Step 0: Register the `network` marker**

`test_build_hero_db_does_not_touch_match_tables` is marked `@pytest.mark.network`. Neither
`pyproject.toml` registers markers today, so the mark warns now and would ERROR under
`--strict-markers`. Add to the ROOT `pyproject.toml` (that is where `[tool.pytest.ini_options]`
lives - `testpaths = ["src-tauri/src-python/tests"]`):

```toml
[tool.pytest.ini_options]
testpaths = ["src-tauri/src-python/tests"]
markers = [
    "network: test hits an external service (deselect with -m 'not network')",
]
```

Verify:

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev pytest --markers | grep network
```

Expected: the marker is listed.

- [x] **Step 1: Add the dependency**

```bash
cd /mnt/docs/adbautoplayer/src-tauri
uv add lz4 texture2ddecoder
uv run python -c "import lz4.block, texture2ddecoder; print('ok')"
```

Expected: `ok`. **Both must be added explicitly** - neither is currently a repo dependency
(`texture2ddecoder` ships with UnityPy, but UnityPy is not in this project). `icons.py` imports
`texture2ddecoder` at module level, so a missing dependency fails at test *collection*, before
the `skipif` for the icon directory can take effect.

- [x] **Step 2: Write the failing test**

`tests/games/afk_journey/services/solstice/test_icons.py`:

```python
import numpy as np
import pytest
from pathlib import Path

from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig
from adb_auto_player.games.afk_journey.services.solstice.icons import (
    IconLibrary, decode_ast,
)

ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")
pytestmark = pytest.mark.skipif(not ICON_DIR.exists(), reason="game icons not extracted")


def test_decodes_a_known_icon_to_the_documented_size():
    img = decode_ast(ICON_DIR / "hero" / "spui_herohead_66.png")   # Sonja
    assert img.shape == (248, 180, 4)
    assert img.dtype == np.uint8


def test_decode_is_not_upside_down():
    """Unity's origin is bottom-left. A hero portrait has its face in the TOP half,
    so the top half carries more opaque pixels than the bottom."""
    img = decode_ast(ICON_DIR / "hero" / "spui_herohead_66.png")
    alpha = img[:, :, 3]
    assert alpha[:124].mean() > alpha[124:].mean()


def test_library_covers_every_usable_roster_hero(db_path):
    cfg = SolsticeConfig.load(db_path)
    lib = IconLibrary.build(cfg, ICON_DIR)
    slugs = {e.slug for e in lib.entries()}
    import sqlite3
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    usable = {
        r[0] for r in con.execute(
            "SELECT h.slug FROM solstice_roster r JOIN hero h ON h.name = r.name "
            "WHERE r.status='usable'")
    }
    con.close()
    assert usable - slugs == set(), f"missing from library: {sorted(usable - slugs)}"


def test_library_includes_skins_mapped_to_their_hero(db_path):
    cfg = SolsticeConfig.load(db_path)
    lib = IconLibrary.build(cfg, ICON_DIR)
    skins = [e for e in lib.entries() if e.art_kind == "skin"]
    assert skins, "no skin entries"
    assert any(e.slug == "eironn" and "15_s1" in e.art_ref for e in skins)
    assert all(e.slug in cfg.heroes() for e in skins)


def test_gamma_brightens(db_path):
    cfg = SolsticeConfig.load(db_path)
    raw = decode_ast(ICON_DIR / "hero" / "spui_herohead_66.png")
    lib = IconLibrary.build(cfg, ICON_DIR)
    entry = next(e for e in lib.entries() if e.art_ref == "spui_herohead_66")
    opaque = raw[:, :, 3] > 200
    import cv2
    raw_gray = cv2.cvtColor(raw[:, :, :3], cv2.COLOR_BGR2GRAY)
    assert entry.gray[opaque].mean() > raw_gray[opaque].mean()
```

- [x] **Step 3: Run it, expect failure**

```bash
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_icons.py -q
```

Expected: FAIL, no module `solstice.icons`.

- [x] **Step 4: Implement `icons.py`**

Format reference, verified on all 1,123 files (see `data/solstice_clash/extract_game_icons.py`):

```python
"""Hero icon library, decoded from the installed game's own assets.

Container: files are named *.png but are NOT PNGs.
    bytes 0-2   "AST"
    byte  3,4   width  = b3 + b4*256
    byte  5,6   height = b5 + b6*256
    byte  7     13 (block code; every observed file is ASTC 6x6)
    bytes 8-11  uncompressed size, uint32 LE
    bytes 12+   LZ4 *block* (not frame) compressed ASTC
Decoded output must be flipped vertically - Unity's origin is bottom-left.

Naming: spui_herohead_<ID>.png is a base icon and <ID> is the game's hero id;
any suffix after the id (e.g. _s1) marks a skin. IDs >= 1000 are NPCs.

Gamma: decoded RGB is darker than the game renders. Applying library_config.gamma
(exponent 1/1.8) raised match median from 0.9550 to 0.9718 on labelled cells. It is
applied here, at library-build time, never baked into the files on disk.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

import cv2
import lz4.block
import numpy as np
import texture2ddecoder

ASTC_BLOCK = 6
_FLATTEN_BG = 190.0      # matches how cells are flattened in vision.py
_NPC_ID_FLOOR = 1000


def decode_ast(path: Path) -> np.ndarray:
    data = path.read_bytes()
    if data[:3] != b"AST":
        raise ValueError(f"not an AST container: {path}")
    width = data[3] + data[4] * 256
    height = data[5] + data[6] * 256
    raw_size = struct.unpack("<I", data[8:12])[0]
    raw = lz4.block.decompress(data[12:], uncompressed_size=raw_size)
    rgba = texture2ddecoder.decode_astc(raw, width, height, ASTC_BLOCK, ASTC_BLOCK)
    img = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    return cv2.flip(img, 0)


def _to_gray(bgra: np.ndarray, gamma: float) -> np.ndarray:
    rgb = np.clip(bgra[:, :, :3].astype(np.float32) / 255.0, 0, 1)
    rgb = 255.0 * np.power(rgb, gamma)
    alpha = bgra[:, :, 3:4].astype(np.float32) / 255.0
    flat = rgb * alpha + _FLATTEN_BG * (1 - alpha)
    return cv2.cvtColor(flat.astype(np.uint8), cv2.COLOR_BGR2GRAY)


@dataclass(frozen=True)
class IconEntry:
    slug: str
    art_ref: str
    art_kind: str          # 'base' | 'skin'
    gray: np.ndarray


class IconLibrary:
    def __init__(self, entries: list[IconEntry]):
        self._entries = entries

    @classmethod
    def build(cls, cfg, icon_dir: Path) -> "IconLibrary":
        gamma = cfg.tunable_float("gamma")
        by_external = {
            h.external_id: slug
            for slug, h in cfg.heroes().items()
            if h.external_id is not None
        }
        entries: list[IconEntry] = []
        for path in sorted((icon_dir / "hero").glob("spui_herohead_*.png")):
            stem = path.stem.replace("spui_herohead_", "")
            m = re.match(r"^(\d+)(?:_(.+))?$", stem)
            if not m:
                continue
            hero_id = int(m.group(1))
            if hero_id >= _NPC_ID_FLOOR:
                continue
            slug = by_external.get(hero_id)
            if slug is None:
                continue          # unmapped id: an NPC or an unreleased hero
            entries.append(IconEntry(
                slug=slug,
                art_ref=path.stem,
                art_kind="skin" if m.group(2) else "base",
                gray=_to_gray(decode_ast(path), gamma),
            ))
        return cls(entries)

    def entries(self) -> list[IconEntry]:
        return list(self._entries)

    def for_slugs(self, slugs: set[str]) -> list[IconEntry]:
        return [e for e in self._entries if e.slug in slugs]
```

- [x] **Step 5: Run the tests**

```bash
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_icons.py -q
```

Expected: PASS, 5 tests. If `test_library_covers_every_usable_roster_hero` fails, an
`external_id` is missing - check `SELECT name FROM hero WHERE external_id IS NULL`, do NOT relax
the test.

- [x] **Step 6: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/icons.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_icons.py \
        src-tauri/pyproject.toml uv.lock
git commit -m "feat(solstice): icon library decoding the game's AST/LZ4/ASTC hero icons"
```

---

## Task 3: Cell extraction and identification

**Files:**
- Create: `.../services/solstice/vision.py`
- Create: `tests/.../solstice/test_vision.py`

**Interfaces:**
- Consumes: `SolsticeConfig`, `IconLibrary`
- Produces:
  - `extract_cell(frame: np.ndarray, cell: Cell) -> np.ndarray` (grayscale)
  - `identify_cell(gray, cell_type, library, cfg, candidates=None) -> Identification`
  - `Identification(slug | None, art_ref, score, margin, status)` where `status` is
    `"identified"` or `"unknown"`

**The matching rule, and why it is what it is:**

Fix the **scale**, not the offset. `matchTemplate` searches offsets internally and for free;
fixing the offset dropped Temesia from 0.978 to 0.408 because the correct offset varies per
hero. Slide the **cell** across the **scaled icon** (the icon is the larger image), which is the
opposite orientation to `game_find_template_match`.

Accept only when `score >= accept_score AND margin >= accept_margin`. The margin is what
actually catches errors: every wrong match observed had a collapsed margin (0.01-0.04) while
plenty of correct ones scored 0.70-0.80.

- [x] **Step 1: Write the failing test**

```python
import cv2
import pytest
from pathlib import Path

from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig
from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary
from adb_auto_player.games.afk_journey.services.solstice import vision

ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")
pytestmark = pytest.mark.skipif(not ICON_DIR.exists(), reason="game icons not extracted")

# Ground truth for data/draft_selecting.png. Banned cells are excluded: their portrait is
# covered by the ban glyph. Rowan / Lily May / Reinier are already picked in this frame so
# their grid cells render SKINNED - they must still resolve to the right hero.
GRID_TRUTH = {
    1: "indris", 2: "viperian", 3: "dionel", 4: "laios", 5: "reinier",
    7: "smokey_meerky", 8: "odie", 9: "pang", 10: "rowan",
    11: "sinbad", 12: "nerion", 13: "parisa", 14: "thador", 15: "lily_may",
    16: "granny_dahnie", 17: "lyca", 18: "cryonaia", 19: "sonja",
}
BANNED_SLOTS = {6, 20}


@pytest.fixture(scope="module")
def library(db_path):
    return IconLibrary.build(SolsticeConfig.load(db_path), ICON_DIR)


def test_extract_cell_returns_declared_size(db_path, frames):
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    cell = cfg.cells("draft_card")[0]
    out = vision.extract_cell(frame, cell)
    assert out.shape == (cell.height, cell.width) == (120, 110)


def test_identifies_every_unbanned_draft_cell(db_path, frames, library):
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    wrong, low = [], []
    for cell in cfg.cells("draft_card"):
        if cell.slot in BANNED_SLOTS:
            continue
        res = vision.identify_cell(
            vision.extract_cell(frame, cell), "draft_card", library, cfg)
        expected = GRID_TRUTH[cell.slot]
        if res.slug != expected:
            wrong.append((cell.slot, expected, res.slug, round(res.score, 3)))
        if res.score < 0.90:
            low.append((cell.slot, expected, round(res.score, 3)))
    assert not wrong, f"misidentified: {wrong}"
    assert not low, f"below the 0.90 baseline: {low}"


def test_skinned_cells_still_resolve_to_the_hero(db_path, frames, library):
    """Rowan and Lily May are picked in this frame, so their grid cells show a skin."""
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    by_slot = {c.slot: c for c in cfg.cells("draft_card")}
    for slot, slug in ((10, "rowan"), (15, "lily_may")):
        res = vision.identify_cell(
            vision.extract_cell(frame, by_slot[slot]), "draft_card", library, cfg)
        assert res.slug == slug
        assert res.art_ref.endswith("_s1"), f"expected a skin, got {res.art_ref}"


def test_unknown_when_no_candidate_fits(db_path, frames, library):
    """A banned cell has its portrait covered - it must come back unknown, not guessed."""
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    banned = next(c for c in cfg.cells("draft_card") if c.slot == 6)
    res = vision.identify_cell(
        vision.extract_cell(frame, banned), "draft_card", library, cfg)
    assert res.status == "unknown"


# Ground truth for data/prematch_locked.png, confirmed by the user. Slots 1-3 blue, 4-6 red.
# Igor and Galahad are wearing skins in this frame.
LOCKED_TRUTH = {1: "berial", 2: "eironn", 3: "igor",
                4: "nara", 5: "galahad", 6: "temesia"}


def test_identifies_every_locked_pick(db_path, frames, library):
    """The 54/54 locked-pick baseline is quoted in this plan - encode it, do not just cite it."""
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["prematch_locked"]))
    wrong, low = [], []
    for cell in cfg.cells("locked_pick"):
        res = vision.identify_cell(
            vision.extract_cell(frame, cell), "locked_pick", library, cfg)
        expected = LOCKED_TRUTH[cell.slot]
        if res.slug != expected:
            wrong.append((cell.slot, expected, res.slug, round(res.score, 3)))
        if res.score < 0.90:
            low.append((cell.slot, expected, round(res.score, 3)))
    assert not wrong, f"misidentified: {wrong}"
    assert not low, f"below the measured 0.9249 floor: {low}"


def test_locked_picks_resolve_skins_to_the_hero(db_path, frames, library):
    """Igor and Galahad are skinned here; both must still resolve to the hero."""
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["prematch_locked"]))
    by_slot = {c.slot: c for c in cfg.cells("locked_pick")}
    for slot, slug in ((3, "igor"), (5, "galahad")):
        res = vision.identify_cell(
            vision.extract_cell(frame, by_slot[slot]), "locked_pick", library, cfg)
        assert res.slug == slug, f"slot {slot}: expected {slug}, got {res.slug}"


def test_pool_constraint_narrows_candidates(db_path, frames, library):
    """Restricting to the match pool must not change the answer, and must raise the margin."""
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    cell = next(c for c in cfg.cells("draft_card") if c.slot == 19)   # Sonja
    full = vision.identify_cell(vision.extract_cell(frame, cell), "draft_card", library, cfg)
    pool = set(GRID_TRUTH.values())
    narrowed = vision.identify_cell(
        vision.extract_cell(frame, cell), "draft_card", library, cfg, candidates=pool)
    assert narrowed.slug == full.slug == "sonja"
    assert narrowed.margin >= full.margin
```

- [x] **Step 2: Run it, expect failure**

```bash
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_vision.py -q
```

Expected: FAIL, no module `solstice.vision`.

- [x] **Step 3: Implement `vision.py`**

```python
"""Cell extraction and hero identification."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np

from .config import Cell, SolsticeConfig
from .icons import IconLibrary

_FLATTEN_BG = 190.0


@dataclass(frozen=True)
class Identification:
    slug: str | None
    art_ref: str | None
    score: float
    margin: float
    status: str                        # 'identified' | 'unknown'
    runner_up_slug: str | None = None  # provenance: what nearly won, and by how little
    runner_up_score: float | None = None
    candidate_scope: str | None = None # 'pool' | 'full_library', set by identify_with_pool
    pool_miss: int | None = None


def extract_cell(frame: np.ndarray, cell: Cell) -> np.ndarray:
    crop = frame[cell.y0:cell.y1, cell.x0:cell.x1]
    if crop.shape[:2] != (cell.height, cell.width):
        raise ValueError(
            f"cell {cell.name} does not fit the frame - is it 1080x1920? "
            f"got crop {crop.shape[:2]}, expected {(cell.height, cell.width)}")
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop


def _best_over_scales(icon: np.ndarray, cell_gray: np.ndarray,
                      scales: tuple[float, ...]) -> float:
    """Slide the CELL across the scaled ICON. The icon is the larger image, so the
    alignment search happens inside matchTemplate - free, and necessary because the
    correct offset varies per hero."""
    best = -1.0
    ch, cw = cell_gray.shape
    for scale in scales:
        w, h = int(icon.shape[1] * scale), int(icon.shape[0] * scale)
        if w < cw or h < ch:
            continue
        resized = cv2.resize(icon, (w, h))
        score = float(cv2.matchTemplate(resized, cell_gray, cv2.TM_CCOEFF_NORMED).max())
        best = max(best, score)
    return best


def identify_cell(cell_gray: np.ndarray, cell_type: str, library: IconLibrary,
                  cfg: SolsticeConfig, candidates: set[str] | None = None) -> Identification:
    entries = library.for_slugs(candidates) if candidates else library.entries()
    if not entries:
        return Identification(None, None, 0.0, 0.0, "unknown")

    scales = cfg.scale_chain(cell_type)
    best_per_slug: dict[str, tuple[float, str]] = {}
    for entry in entries:
        score = _best_over_scales(entry.gray, cell_gray, scales)
        current = best_per_slug.get(entry.slug)
        if current is None or score > current[0]:
            best_per_slug[entry.slug] = (score, entry.art_ref)

    ranked = sorted(
        ((score, slug, art) for slug, (score, art) in best_per_slug.items()), reverse=True)
    top_score, top_slug, top_art = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
    margin = top_score - runner_up

    accepted = (top_score >= cfg.tunable_float("accept_score")
                and margin >= cfg.tunable_float("accept_margin"))
    runner_slug = ranked[1][1] if len(ranked) > 1 else None
    return Identification(
        slug=top_slug if accepted else None,
        art_ref=top_art if accepted else None,
        score=top_score,
        margin=margin,
        status="identified" if accepted else "unknown",
        runner_up_slug=runner_slug,
        runner_up_score=runner_up if len(ranked) > 1 else None,
    )
```

- [x] **Step 4: Run the tests**

```bash
uv run --group dev pytest src-tauri/src-python/tests/games/afk_journey/services/solstice/test_vision.py -q
```

Expected: PASS, 5 tests. If `test_identifies_every_unbanned_draft_cell` fails, do NOT lower the
0.90 assertion - it is a measured baseline (18/18 at median 0.9550). Check the gamma value and
the scale chain first.

- [x] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/vision.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_vision.py
git commit -m "feat(solstice): cell extraction and hero identification with the margin rule"
```

---

## Task 4: Screen classifier

**Files:**
- Modify: `.../services/solstice/vision.py`
- Modify: `tests/.../solstice/test_vision.py`
- Create: `.../templates/event/solstice_clash/anchors/draft_selecting.png`

**Interfaces:**
- Produces: `classify_screen(frame, cfg, anchor_dir) -> str` returning
  `"draft"`, `"prematch_locked"` or `"unknown"`

Brightness heuristics were tried and scored 1/5. Use template anchors. The draft anchor is a
static UI region that scored **0.999** on draft screens and **0.763** on spectate frames.

- [x] **Step 1: Cut the draft anchor**

```bash
cd /mnt/docs/adbautoplayer
python3 - <<'PY'
import cv2, os
src = "src-tauri/src-python/tests/games/afk_journey/services/solstice/data/draft_selecting.png"
out = ("src-tauri/src-python/adb_auto_player/games/afk_journey/templates/"
       "event/solstice_clash/anchors")
os.makedirs(out, exist_ok=True)
img = cv2.imread(src)
assert img is not None and img.shape[:2] == (1920, 1080), img.shape
cv2.imwrite(os.path.join(out, "draft_selecting.png"), img[630:720, 320:480])
print("draft anchor 160x90 written")

pre = cv2.imread(src.replace("draft_selecting", "prematch_locked"))
assert pre is not None and pre.shape[:2] == (1920, 1080), pre.shape
cv2.imwrite(os.path.join(out, "prematch_locked.png"), pre[1440:1500, 470:610])
print("prematch anchor 140x60 written")
PY
```

Then MEASURE both anchors before relying on them - a brightness heuristic scored 1/5 here,
so an anchor is not trusted until its separation is measured:

```bash
python3 - <<'MEASURE'
import cv2, glob
out = ("src-tauri/src-python/adb_auto_player/games/afk_journey/templates/"
       "event/solstice_clash/anchors")
data = "src-tauri/src-python/tests/games/afk_journey/services/solstice/data"
for anchor in ("draft_selecting", "prematch_locked"):
    a = cv2.imread(f"{out}/{anchor}.png", cv2.IMREAD_GRAYSCALE)
    print(f"--- {anchor} ---")
    for f in sorted(glob.glob(f"{data}/*.png")):
        g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        v = float(cv2.matchTemplate(g, a, cv2.TM_CCOEFF_NORMED).max())
        print(f"   {f.split('/')[-1]:26s} {v:.3f}")
MEASURE
```

Expected: each anchor scores >= 0.99 on its own screen and clearly under 0.90 on the other two.
If a non-matching screen scores above 0.90, choose a different region - do NOT lower the
threshold.

- [x] **Step 2: Write the failing test**

```python
ANCHORS = (Path(__file__).resolve().parents[5] / "adb_auto_player" / "games" /
           "afk_journey" / "templates" / "event" / "solstice_clash" / "anchors")


def test_classifies_the_three_fixture_screens(db_path, frames):
    cfg = SolsticeConfig.load(db_path)
    assert vision.classify_screen(
        cv2.imread(str(frames["draft_selecting"])), cfg, ANCHORS) == "draft"
    assert vision.classify_screen(
        cv2.imread(str(frames["prematch_locked"])), cfg, ANCHORS) == "prematch_locked"


def test_prematch_is_not_mistaken_for_draft_or_spectate(db_path, frames):
    """Each anchor must fire only on its own screen."""
    cfg = SolsticeConfig.load(db_path)
    assert vision.classify_screen(
        cv2.imread(str(frames["spectate"])), cfg, ANCHORS) != "prematch_locked"
    assert vision.classify_screen(
        cv2.imread(str(frames["draft_selecting"])), cfg, ANCHORS) != "prematch_locked"


def test_spectate_is_not_mistaken_for_draft(db_path, frames):
    """Spectate does NOT share the compete geometry - it must not classify as draft."""
    cfg = SolsticeConfig.load(db_path)
    assert vision.classify_screen(
        cv2.imread(str(frames["spectate"])), cfg, ANCHORS) != "draft"
```

- [x] **Step 3: Run it, expect failure** (`AttributeError: classify_screen`)

- [x] **Step 4: Implement**

```python
# Anchors, not brightness. Brightness/colour classification was tried and scored 1/5
# (docs/solstice-clash/README.md), so BOTH screens use template anchors.
_DRAFT_ANCHOR_AT = (630, 320)      # y, x where the anchor was cut
_DRAFT_ANCHOR_PAD = 25             # search window so matchTemplate can align
_DRAFT_ANCHOR_MIN = 0.90           # draft scores 0.999; spectate scores 0.763
_PREMATCH_ANCHOR_AT = (1440, 470)  # static divider between the two team plates
_PREMATCH_ANCHOR_PAD = 25
_PREMATCH_ANCHOR_MIN = 0.90


def classify_screen(frame: np.ndarray, cfg: SolsticeConfig, anchor_dir) -> str:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame

    anchor = cv2.imread(str(anchor_dir / "draft_selecting.png"), cv2.IMREAD_GRAYSCALE)
    if anchor is not None:
        y, x = _DRAFT_ANCHOR_AT
        p = _DRAFT_ANCHOR_PAD
        window = gray[max(0, y - p):y + anchor.shape[0] + p,
                      max(0, x - p):x + anchor.shape[1] + p]
        if window.shape[0] >= anchor.shape[0] and window.shape[1] >= anchor.shape[1]:
            score = float(cv2.matchTemplate(window, anchor, cv2.TM_CCOEFF_NORMED).max())
            if score >= _DRAFT_ANCHOR_MIN:
                return "draft"

    prematch = cv2.imread(str(anchor_dir / "prematch_locked.png"), cv2.IMREAD_GRAYSCALE)
    if prematch is not None:
        y, x = _PREMATCH_ANCHOR_AT
        p = _PREMATCH_ANCHOR_PAD
        window = gray[max(0, y - p):y + prematch.shape[0] + p,
                      max(0, x - p):x + prematch.shape[1] + p]
        if window.shape[0] >= prematch.shape[0] and window.shape[1] >= prematch.shape[1]:
            score = float(cv2.matchTemplate(window, prematch, cv2.TM_CCOEFF_NORMED).max())
            if score >= _PREMATCH_ANCHOR_MIN:
                return "prematch_locked"
    return "unknown"
```

- [x] **Step 5: Run the tests.** Expected: PASS, 7 tests total in the file.

- [x] **Step 6: Commit**

```bash
git add -A src-tauri/src-python/adb_auto_player/games/afk_journey/
git commit -m "feat(solstice): template-anchored screen classifier"
```

---

## Task 5: Pool capture and the two-tier candidate strategy

**Files:**
- Modify: `.../services/solstice/vision.py`
- Modify: `tests/.../solstice/test_vision.py`
- Create: `.../templates/event/solstice_clash/anchors/ban_glyph_red_v2.png`

**Interfaces:**
- Produces:
  - `is_banned(cell_gray, ban_glyphs, threshold) -> bool`
  - `identify_pool(frame, cfg, library, anchor_dir) -> PoolRead`
  - `PoolRead(slugs: set[str], per_slot: dict[int, Identification], banned_slots: set[int])`

**Why this matters more than anything else in the plan:** capturing the 20-hero grid at draft
start constrains every later identification from 121 candidates to <= 20. It also self-validates -
a locked pick that is not in the pool is a *detected* error rather than a silent wrong answer.
The capture window is multi-second (a human has to read the grid and decide a ban), so a 3/sec
poll catches it comfortably.

Tier 1: match locked picks against the pool. Tier 2: on failure, fall back to the full library -
this covers grid cells that came back `unknown`. Still nothing: `unknown`.

- [x] **Step 1: Cut the third ban glyph**

The committed red/blue pair misses Tilaya's overlay (scored 0.279/0.241, under the 0.60
threshold), so a banned card leaks into results as a phantom hero.

```bash
cd /mnt/docs/adbautoplayer
python3 - <<'PY'
import cv2, os
src = "src-tauri/src-python/tests/games/afk_journey/services/solstice/data/draft_selecting.png"
out = ("src-tauri/src-python/adb_auto_player/games/afk_journey/templates/"
       "event/solstice_clash/anchors")
img = cv2.imread(src)
# grid cell 20 (r3c4) is Tilaya, banned in this frame
gray = cv2.cvtColor(img[1370+45:1370+165, 795+20:795+130], cv2.COLOR_BGR2GRAY)
cv2.imwrite(os.path.join(out, "ban_glyph_red_v2.png"), gray[10:-10, 10:-10])
print("ban_glyph_red_v2", gray.shape)
PY
```

- [x] **Step 2: Write the failing test**

```python
def test_ban_detection_finds_exactly_the_two_banned_cells(db_path, frames):
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    glyphs = vision.load_ban_glyphs(ANCHORS)
    assert len(glyphs) >= 3, "need red, blue and the red_v2 variant"
    banned = {
        c.slot for c in cfg.cells("draft_card")
        if vision.is_banned(vision.extract_cell(frame, c), glyphs, 0.60)
    }
    assert banned == BANNED_SLOTS, f"expected {BANNED_SLOTS}, got {banned}"


def test_identify_with_pool_reports_which_tier_answered(db_path, frames, library):
    """A pool hit and a full-library fallback must be distinguishable by the caller."""
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    cell = next(c for c in cfg.cells("draft_card") if c.slot == 19)   # Sonja
    gray = vision.extract_cell(frame, cell)

    hit = vision.identify_with_pool(gray, "draft_card", library, cfg, {"sonja", "lyca"})
    assert hit.slug == "sonja" and hit.candidate_scope == "pool" and hit.pool_miss == 0

    # a pool that cannot contain the answer must fall back and say so
    miss = vision.identify_with_pool(gray, "draft_card", library, cfg, {"berial", "tilaya"})
    assert miss.candidate_scope == "full_library" and miss.pool_miss == 1
    assert miss.slug == "sonja"


def test_identify_pool_reads_the_whole_grid(db_path, frames, library):
    cfg = SolsticeConfig.load(db_path)
    frame = cv2.imread(str(frames["draft_selecting"]))
    pool = vision.identify_pool(frame, cfg, library, ANCHORS)
    assert pool.banned_slots == BANNED_SLOTS
    assert pool.slugs == set(GRID_TRUTH.values())
    assert all(pool.per_slot[s].status == "identified" for s in GRID_TRUTH)
```

- [x] **Step 3: Run it, expect failure**

- [x] **Step 4: Implement**

```python
def load_ban_glyphs(anchor_dir) -> list[np.ndarray]:
    """Every ban_glyph_*.png in the anchor directory. There are at least three
    variants; matching only one leaks banned cards in as phantom heroes."""
    return [
        img for img in (
            cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            for p in sorted(anchor_dir.glob("ban_glyph_*.png"))
        ) if img is not None
    ]


def is_banned(cell_gray: np.ndarray, ban_glyphs: list[np.ndarray],
              threshold: float = 0.60) -> bool:
    """Template-match the circle-slash overlay. A colour cast was tried and
    false-positived on 10 of 20 real cards - red-haired heroes trip it."""
    for glyph in ban_glyphs:
        if glyph.shape[0] > cell_gray.shape[0] or glyph.shape[1] > cell_gray.shape[1]:
            continue
        if float(cv2.matchTemplate(cell_gray, glyph, cv2.TM_CCOEFF_NORMED).max()) >= threshold:
            return True
    return False


@dataclass(frozen=True)
class PoolRead:
    slugs: set[str]
    per_slot: dict[int, Identification]
    banned_slots: set[int]


def identify_pool(frame: np.ndarray, cfg: SolsticeConfig, library: IconLibrary,
                  anchor_dir) -> PoolRead:
    glyphs = load_ban_glyphs(anchor_dir)
    per_slot: dict[int, Identification] = {}
    banned: set[int] = set()
    for cell in cfg.cells("draft_card"):
        gray = extract_cell(frame, cell)
        if is_banned(gray, glyphs):
            banned.add(cell.slot)
            continue
        per_slot[cell.slot] = identify_cell(gray, "draft_card", library, cfg)
    slugs = {r.slug for r in per_slot.values() if r.slug}
    return PoolRead(slugs=slugs, per_slot=per_slot, banned_slots=banned)


def identify_with_pool(cell_gray: np.ndarray, cell_type: str, library: IconLibrary,
                       cfg: SolsticeConfig, pool: set[str] | None) -> Identification:
    """Tier 1: the match pool. Tier 2: the full library. Then unknown.

    The returned Identification carries `candidate_scope` and `pool_miss` so the caller can
    record WHICH tier answered. Without that, "the pool read was wrong" and "a legitimate
    hero outside the pool" are indistinguishable in the data forever.
    """
    if pool:
        first = identify_cell(cell_gray, cell_type, library, cfg, candidates=pool)
        if first.status == "identified":
            return replace(first, candidate_scope="pool", pool_miss=0)
    fallback = identify_cell(cell_gray, cell_type, library, cfg)
    return replace(fallback, candidate_scope="full_library", pool_miss=1 if pool else 0)
```

- [x] **Step 5: Run the tests.** Expected: PASS. If `test_ban_detection...` returns extra
slots, the glyph threshold is too low - print per-cell scores before changing it; measured
separation was 1.00 for bans versus 0.35 for other cards.

- [x] **Step 6: Commit**

```bash
git add -A src-tauri/src-python/adb_auto_player/games/afk_journey/
git commit -m "feat(solstice): pool capture, third ban glyph, two-tier candidate strategy"
```

---

## Task 6: Match store

**Files:**
- Create: `.../services/solstice/store.py`
- Create: `tests/.../solstice/test_store.py`
- Modify: `data/solstice_clash/schema.sql` (add the three match tables)
- Modify: `data/solstice_clash/migrate.py` (bump `SCHEMA_VERSION` to 2)

**Interfaces:**
- Produces:
  - `MatchStore(db_path)`, `.record_match(MatchRecord) -> int`, `.record_heroes(match_id, list[HeroSlot])`,
    `.record_odds(match_id, OddsSample)`, `.match_by_natural_key(key) -> int | None`

Match tables are **locally earned** - `build_hero_db.py` must never touch them. Add them to
`schema.sql` under the locally-earned section so that stays obvious.

- [x] **Step 1: Extend the schema**

Append to `schema.sql`:

```sql
-- ------------------------------------------------- match data (locally earned)

CREATE TABLE IF NOT EXISTS match(
  id             INTEGER PRIMARY KEY,
  -- NULLABLE on purpose. A match observed mid-draft has no stable key yet (unknown heroes,
  -- no outcome). Rows with a NULL key are local-only and excluded from any future sync;
  -- the key is computed once enough stable facts exist. SQLite allows many NULLs in a
  -- UNIQUE column, which is exactly what we want.
  natural_key    TEXT UNIQUE,
  source         TEXT NOT NULL,          -- 'compete' | 'spectate'
  captured_at    TEXT NOT NULL,
  theme          TEXT,                   -- readable on the draft screen
  balance_epoch  TEXT,                   -- hash of the roster adjustments at capture time
  blue_player    TEXT, blue_rating INTEGER, blue_rank INTEGER,
  red_player     TEXT, red_rating  INTEGER, red_rank  INTEGER,
  outcome        TEXT,                   -- 'blue' | 'red' | 'draw' | NULL
  outcome_source TEXT
);

CREATE TABLE IF NOT EXISTS match_hero(
  id            INTEGER PRIMARY KEY,
  match_id      INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
  side          TEXT NOT NULL,           -- 'blue' | 'red'
  slot          INTEGER NOT NULL,
  hero_slug     TEXT REFERENCES hero(slug),
  art_ref       TEXT,
  status        TEXT NOT NULL,           -- 'identified' | 'unknown'
  score         REAL,
  margin        REAL,
  -- Provenance. Without this a bad pool read and a legitimate out-of-pool recovery are
  -- indistinguishable forever, and a failed identification cannot be relabelled by hand.
  cell_type     TEXT,                    -- 'locked_pick' | 'draft_locked_pick' | 'draft_card'
  cell_name     TEXT,
  candidate_scope TEXT,                  -- 'pool' | 'full_library'
  pool_miss     INTEGER,                 -- 1 = pool tier failed and full library was used
  runner_up_slug  TEXT,
  runner_up_score REAL,
  crop_path     TEXT,                    -- saved cell crop, for relabelling
  frame_path    TEXT,                    -- source frame, for re-measuring geometry
  UNIQUE(match_id, side, slot)
);

-- The 20 heroes on offer, and which were banned. Phase 2 identifies these and Phase 5
-- needs them: "who was available but not picked" is a real signal, and a pick that is not
-- in the pool is a DETECTED error rather than a silent wrong answer. Computing this and
-- discarding it would be the single most expensive omission to retrofit.
CREATE TABLE IF NOT EXISTS match_pool(
  id            INTEGER PRIMARY KEY,
  match_id      INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
  slot          INTEGER NOT NULL,        -- 1..20, row-major in the 5x4 grid
  hero_slug     TEXT REFERENCES hero(slug),
  art_ref       TEXT,
  status        TEXT NOT NULL,           -- 'identified' | 'unknown' | 'banned'
  banned        INTEGER NOT NULL DEFAULT 0,
  score         REAL,
  margin        REAL,
  runner_up_slug  TEXT,
  runner_up_score REAL,
  crop_path     TEXT,
  frame_path    TEXT,
  UNIQUE(match_id, slot)
);

CREATE TABLE IF NOT EXISTS match_odds(
  id          INTEGER PRIMARY KEY,
  match_id    INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
  sampled_at  TEXT NOT NULL,
  blue_pool   INTEGER, red_pool INTEGER,
  blue_odds   REAL,    red_odds REAL,
  spectators  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_match_hero_match ON match_hero(match_id);
CREATE INDEX IF NOT EXISTS idx_match_pool_match ON match_pool(match_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_match ON match_odds(match_id);
CREATE INDEX IF NOT EXISTS idx_match_outcome    ON match(outcome);
```

Bump `SCHEMA_VERSION = 2` in `migrate.py`.

Then **apply it to the shipped database**, otherwise every test in this task fails with
`no such table: match` - `conftest.py` copies `data/solstice_clash/heroes.sqlite` as-is:

```bash
cd /mnt/docs/adbautoplayer/data/solstice_clash
python3 migrate.py
python3 -c "
import sqlite3
c = sqlite3.connect('heroes.sqlite')
t = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")]
print('match tables:', sorted(x for x in t if x.startswith('match')))
print('schema version:', c.execute('SELECT MAX(version) FROM schema_version').fetchone()[0])"
```

Expected: `match tables: ['match', 'match_hero', 'match_odds', 'match_pool']` and `schema version: 2`.

`build_hero_db.py` must NOT be given a path to these tables - they are locally earned and it
already only touches `hero_skill` and `solstice_roster`.

- [x] **Step 2: Write the failing test**

```python
import shutil

import pytest

from adb_auto_player.games.afk_journey.services.solstice.store import (
    MatchStore, MatchRecord, HeroSlot, PoolSlot, OddsSample,
)


@pytest.fixture
def tmp_db(tmp_path, db_path):
    p = tmp_path / "heroes.sqlite"
    shutil.copy(db_path, p)
    return p


def test_records_a_match_and_its_heroes(tmp_db):
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(
        natural_key="t1", source="compete", captured_at="2026-07-26T10:00:00",
        theme="Fierce Duel", blue_player="GameRetro", red_player="Dan"))
    store.record_heroes(mid, [
        HeroSlot("blue", 1, "dionel", "spui_herohead_48", "identified", 0.97, 0.34),
        HeroSlot("red", 6, None, None, "unknown", 0.41, 0.02),
    ])
    rows = store.heroes_for(mid)
    assert len(rows) == 2
    assert rows[0].hero_slug == "dionel"
    assert rows[1].status == "unknown" and rows[1].hero_slug is None


def test_natural_key_dedupes(tmp_db):
    store = MatchStore(tmp_db)
    a = store.record_match(MatchRecord(natural_key="same", source="spectate",
                                       captured_at="2026-07-26T10:00:00"))
    b = store.record_match(MatchRecord(natural_key="same", source="spectate",
                                       captured_at="2026-07-26T10:05:00"))
    assert a == b
    assert store.match_by_natural_key("same") == a


def test_unknown_heroes_are_stored_not_dropped(tmp_db):
    """An unidentified slot must be recorded as unknown - never silently omitted,
    or a 3v3 match would look like a 2v3."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(natural_key="u1", source="compete",
                                         captured_at="2026-07-26T10:00:00"))
    store.record_heroes(mid, [HeroSlot("blue", i, None, None, "unknown", 0.5, 0.0)
                              for i in (1, 2, 3)])
    assert len(store.heroes_for(mid)) == 3


def test_pool_is_recorded_including_banned_slots(tmp_db):
    """The 20 offered heroes and the bans are the context Phase 5 needs. Computing the
    pool and discarding it is the most expensive omission to retrofit."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(source="compete", captured_at="2026-07-26T10:00:00"))
    store.record_pool(mid, [
        PoolSlot(1, "indris", "spui_herohead_87", "identified", 0, 0.95, 0.34),
        PoolSlot(6, None, None, "banned", 1),
        PoolSlot(20, None, None, "banned", 1),
    ])
    rows = store.pool_for(mid)
    assert len(rows) == 3
    assert {r.slot for r in rows if r.banned} == {6, 20}
    assert rows[0].hero_slug == "indris"


def test_pool_fallback_is_recorded(tmp_db):
    """A pool miss and a legitimate out-of-pool hero must be distinguishable afterwards."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(source="compete", captured_at="2026-07-26T10:00:00"))
    store.record_heroes(mid, [
        HeroSlot("blue", 1, "sonja", "x", "identified", 0.97, 0.4,
                 cell_type="locked_pick", cell_name="locked_pick_1",
                 candidate_scope="pool", pool_miss=0),
        HeroSlot("blue", 2, "zorya", "y", "identified", 0.92, 0.5,
                 cell_type="locked_pick", cell_name="locked_pick_2",
                 candidate_scope="full_library", pool_miss=1),
    ])
    rows = store.heroes_for(mid)
    assert rows[0].candidate_scope == "pool" and rows[0].pool_miss == 0
    assert rows[1].candidate_scope == "full_library" and rows[1].pool_miss == 1


def test_a_match_can_exist_without_a_natural_key(tmp_db):
    """Mid-draft observations have no stable key yet; they must still record."""
    store = MatchStore(tmp_db)
    a = store.record_match(MatchRecord(source="compete", captured_at="2026-07-26T10:00:00"))
    b = store.record_match(MatchRecord(source="compete", captured_at="2026-07-26T10:00:01"))
    assert a != b, "unkeyed observations must not collapse into one row"
    store.set_natural_key(a, "later-key")
    assert store.match_by_natural_key("later-key") == a


def test_odds_samples_accumulate(tmp_db):
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(natural_key="o1", source="spectate",
                                         captured_at="2026-07-26T10:00:00"))
    store.record_odds(mid, OddsSample("2026-07-26T10:00:01", 1000, 2000, 2.93, 1.45, 12))
    store.record_odds(mid, OddsSample("2026-07-26T10:00:05", 1500, 2000, 2.30, 1.70, 15))
    assert len(store.odds_for(mid)) == 2


@pytest.mark.network
def test_build_hero_db_does_not_touch_match_tables(tmp_db):
    """Match data is locally earned. A full wiki refresh must leave it alone.

    This runs build_hero_db.py for real - it hits the Fandom API, so it is marked
    `network` and can be deselected with `-m "not network"`. Running only migrate.py
    here would NOT test the claim, since migrate.py never writes rows.
    """
    import subprocess, sys
    from pathlib import Path
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(natural_key="keep", source="compete",
                                         captured_at="2026-07-26T10:00:00"))
    store.record_heroes(mid, [HeroSlot("blue", 1, "sonja", "x", "identified", 0.9, 0.3)])
    store.record_odds(mid, OddsSample("2026-07-26T10:00:01", 100, 200, 2.9, 1.4, 5))

    builder = Path(__file__).resolve().parents[7] / "data" / "solstice_clash" / "build_hero_db.py"
    # Pass the temp database EXPLICITLY. build_hero_db.py resolves its default path relative
    # to the script, not the cwd, so a cwd-based "redirect" would rewrite the SHIPPED database
    # and leave this assertion checking an untouched copy - passing trivially.
    subprocess.run([sys.executable, str(builder), str(tmp_db)], check=True)

    assert store.match_by_natural_key("keep") == mid
    assert len(store.heroes_for(mid)) == 1
    assert len(store.odds_for(mid)) == 1
```

- [x] **Step 3: Run it, expect failure**

- [x] **Step 4: Implement `store.py`**

```python
"""Match recording. These tables are LOCALLY EARNED - build_hero_db.py never touches them."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MatchRecord:
    source: str               # 'compete' | 'spectate'
    captured_at: str
    natural_key: str | None = None   # NULL until the match has enough stable facts
    theme: str | None = None
    balance_epoch: str | None = None
    blue_player: str | None = None
    blue_rating: int | None = None
    blue_rank: int | None = None
    red_player: str | None = None
    red_rating: int | None = None
    red_rank: int | None = None
    outcome: str | None = None            # 'blue' | 'red' | 'draw' | None
    outcome_source: str | None = None


@dataclass(frozen=True)
class HeroSlot:
    side: str                 # 'blue' | 'red'
    slot: int
    hero_slug: str | None     # None when status == 'unknown'
    art_ref: str | None
    status: str               # 'identified' | 'unknown'
    score: float | None = None
    margin: float | None = None
    # Provenance - see the schema comment. candidate_scope/pool_miss are what separate
    # "the pool read was wrong" from "a legitimate hero outside the pool".
    cell_type: str | None = None
    cell_name: str | None = None
    candidate_scope: str | None = None
    pool_miss: int | None = None
    runner_up_slug: str | None = None
    runner_up_score: float | None = None
    crop_path: str | None = None
    frame_path: str | None = None


@dataclass(frozen=True)
class PoolSlot:
    slot: int                 # 1..20, row-major
    hero_slug: str | None
    art_ref: str | None
    status: str               # 'identified' | 'unknown' | 'banned'
    banned: int = 0
    score: float | None = None
    margin: float | None = None
    runner_up_slug: str | None = None
    runner_up_score: float | None = None
    crop_path: str | None = None
    frame_path: str | None = None


@dataclass(frozen=True)
class OddsSample:
    sampled_at: str
    blue_pool: int | None = None
    red_pool: int | None = None
    blue_odds: float | None = None
    red_odds: float | None = None
    spectators: int | None = None


class MatchStore:
    def __init__(self, db_path: Path):
        self._db = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db)
        # Required for the ON DELETE CASCADE on match_hero / match_odds. SQLite defaults
        # this OFF per connection, so setting it in the schema alone is not enough.
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def record_match(self, rec: MatchRecord) -> int:
        """Insert, or return the existing id if this natural_key was already seen.

        Re-observing the same match must not duplicate it or raise - Mode B will see the
        same match on consecutive polls.
        """
        with self._connect() as con:
            con.execute(
                "INSERT INTO match(natural_key,source,captured_at,theme,balance_epoch,"
                "blue_player,blue_rating,blue_rank,red_player,red_rating,red_rank,"
                "outcome,outcome_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(natural_key) DO NOTHING",
                (rec.natural_key, rec.source, rec.captured_at, rec.theme, rec.balance_epoch,
                 rec.blue_player, rec.blue_rating, rec.blue_rank,
                 rec.red_player, rec.red_rating, rec.red_rank,
                 rec.outcome, rec.outcome_source))
            if rec.natural_key is None:
                # No key yet: this is a fresh local-only observation, so it always inserts.
                row = con.execute("SELECT last_insert_rowid()").fetchone()
            else:
                row = con.execute("SELECT id FROM match WHERE natural_key=?",
                                  (rec.natural_key,)).fetchone()
        return int(row[0])

    def match_by_natural_key(self, natural_key: str) -> int | None:
        with self._connect() as con:
            row = con.execute("SELECT id FROM match WHERE natural_key=?",
                              (natural_key,)).fetchone()
        return int(row[0]) if row else None

    _HERO_COLS = ("side", "slot", "hero_slug", "art_ref", "status", "score", "margin",
                  "cell_type", "cell_name", "candidate_scope", "pool_miss",
                  "runner_up_slug", "runner_up_score", "crop_path", "frame_path")

    def record_heroes(self, match_id: int, slots: list[HeroSlot]) -> None:
        """Every slot is stored, including unknown ones. Dropping an unidentified slot
        would make a 3v3 look like a 2v3 and silently corrupt the training data."""
        cols = ",".join(self._HERO_COLS)
        placeholders = ",".join("?" * (len(self._HERO_COLS) + 1))
        updates = ",".join(f"{c}=excluded.{c}" for c in self._HERO_COLS[2:])
        with self._connect() as con:
            con.executemany(
                f"INSERT INTO match_hero(match_id,{cols}) VALUES({placeholders}) "
                f"ON CONFLICT(match_id,side,slot) DO UPDATE SET {updates}",
                [(match_id, *(getattr(s, c) for c in self._HERO_COLS)) for s in slots])

    def heroes_for(self, match_id: int) -> list[HeroSlot]:
        cols = ",".join(self._HERO_COLS)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {cols} FROM match_hero WHERE match_id=? ORDER BY side, slot",
                (match_id,)).fetchall()
        return [HeroSlot(*r) for r in rows]

    _POOL_COLS = ("slot", "hero_slug", "art_ref", "status", "banned", "score", "margin",
                  "runner_up_slug", "runner_up_score", "crop_path", "frame_path")

    def record_pool(self, match_id: int, slots: list[PoolSlot]) -> None:
        """The 20 offered heroes and which were banned. Never skip banned slots - "this
        hero was available but banned" is a distinct fact from "this slot was not read"."""
        cols = ",".join(self._POOL_COLS)
        placeholders = ",".join("?" * (len(self._POOL_COLS) + 1))
        updates = ",".join(f"{c}=excluded.{c}" for c in self._POOL_COLS[1:])
        with self._connect() as con:
            con.executemany(
                f"INSERT INTO match_pool(match_id,{cols}) VALUES({placeholders}) "
                f"ON CONFLICT(match_id,slot) DO UPDATE SET {updates}",
                [(match_id, *(getattr(s, c) for c in self._POOL_COLS)) for s in slots])

    def pool_for(self, match_id: int) -> list[PoolSlot]:
        cols = ",".join(self._POOL_COLS)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {cols} FROM match_pool WHERE match_id=? ORDER BY slot",
                (match_id,)).fetchall()
        return [PoolSlot(*r) for r in rows]

    def set_natural_key(self, match_id: int, natural_key: str) -> None:
        """Set once a match has enough stable facts to be keyed. Until then it stays NULL
        and the row is local-only."""
        with self._connect() as con:
            con.execute("UPDATE match SET natural_key=? WHERE id=?", (natural_key, match_id))

    def record_odds(self, match_id: int, sample: OddsSample) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO match_odds(match_id,sampled_at,blue_pool,red_pool,"
                "blue_odds,red_odds,spectators) VALUES(?,?,?,?,?,?,?)",
                (match_id, sample.sampled_at, sample.blue_pool, sample.red_pool,
                 sample.blue_odds, sample.red_odds, sample.spectators))

    def odds_for(self, match_id: int) -> list[OddsSample]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT sampled_at,blue_pool,red_pool,blue_odds,red_odds,spectators "
                "FROM match_odds WHERE match_id=? ORDER BY sampled_at", (match_id,)).fetchall()
        return [OddsSample(*r) for r in rows]

    def set_outcome(self, match_id: int, outcome: str, source: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE match SET outcome=?, outcome_source=? WHERE id=?",
                        (outcome, source, match_id))
```

Note `heroes_for` returns `HeroSlot(*r)` and the SELECT column order matches the dataclass
field order exactly - if you reorder either, reorder both.

- [x] **Step 5: Run the tests.** Expected: PASS, 5 tests.

- [x] **Step 6: Commit**

```bash
git add -A src-tauri/src-python/ data/solstice_clash/
git commit -m "feat(solstice): match/match_hero/match_odds store (schema v2)"
```

---

## Task 7: Regression, lint, docs

- [x] **Step 0: Lint**

```bash
cd /mnt/docs/adbautoplayer
uv run --group dev ruff check \
  src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/ \
  src-tauri/src-python/tests/games/afk_journey/services/solstice/
```

Expected: no findings. `E402` (import not at top) and `F401` (unused import) are the likely ones.

- [x] **Step 1: Full suite**

```bash
uv run --group dev pytest src-tauri/src-python/tests -q
```

Expected: no new failures versus `main`. If the icon-dependent tests skip, that is correct on a
machine without the extracted icons - but they MUST run and pass on the dev machine.

- [x] **Step 2: Verify the database refresh is still non-destructive**

```bash
cd data/solstice_clash
cp heroes.sqlite /tmp/before.sqlite
python3 migrate.py
python3 build_hero_db.py
python3 - <<'PY'
import sqlite3
a=sqlite3.connect("/tmp/before.sqlite"); b=sqlite3.connect("heroes.sqlite")
for t in ("hero","hero_alias","hero_skin","hero_skill","solstice_roster",
          "cell_registry","art_transform","library_config","match","match_hero"):
    x=a.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    y=b.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t:16s} {x:>5} -> {y:>5} {'OK' if x==y else 'DRIFT'}")
PY
```

- [x] **Step 3: Update the docs**

Add a "Phase 1 delivered" section to `docs/solstice-clash/README.md` listing the four services
and their entry points, and update `CHANGELOG.md`.

- [x] **Step 4: Commit**

---

## Rules that are easy to get wrong

1. **Never fix the crop offset.** Fix the scale and let `matchTemplate` search. Fixing the offset
   dropped Temesia from 0.978 to 0.408.
2. **The margin catches errors, not the score.** Every wrong match had margin 0.01-0.04; correct
   ones sat happily at 0.70-0.80. Require both.
3. **Never filter candidates by roster status.** Doing so caused 100% failure on two cells - both
   Zorya, who is listed as both usable and banned on the wiki.
4. **Icons are not all the same size.** Read width/height from the AST header. Assuming 180x248
   squashed `Sword of Misarte` (184x248) and cost ~0.15 of score.
5. **Flip decoded textures.** Unity's origin is bottom-left.
6. **A hero picked in the draft renders SKINNED in the grid.** A calibration frame with picks
   already made is tainted; capture the pool in the first ~3 seconds.
7. **Never conclude from a truncated listing.** The datamining route was wrongly declared dead
   three times this way.

## Deferred - explicitly NOT in this plan

- Spectate-mode geometry (spectate does not share the compete layout)
- Odds parsing and the prediction model
- Any device automation, navigation or tapping
- Postgres sync
- Auto-betting (a possible v2, gated on proven accuracy)

## Notes for the implementer

- Icons live at `/mnt/vault/solstice/gamefiles/ui/icon` and are NOT committed (430MB). Tests
  skip cleanly without them.
- Regenerate icons with `data/solstice_clash/extract_game_icons.py`; refresh the wiki data with
  `build_hero_db.py`. Both are safe to re-run.
- Fixture frames must be raw `adb exec-out screencap` output at 1080x1920. A screenshot that has
  passed through a chat client is rescaled and will silently produce wrong coordinates.
