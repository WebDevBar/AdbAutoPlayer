# Side Integrity and Canonical Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish whether our recorded left/right labels are trustworthy, repair the ones that are not, and replace match identity with an orientation-invariant key so one match can never occupy two rows.

**Architecture:** Four parts in a mandatory order. A read-only frame audit adjudicates our summary-screen side labels against the draft screen, which carries true blue/red plates. A gated repair then corrects the mirrored rows locally. The ledger records what today's measurements closed. Finally, identity moves from `outcome | sides | time-bucket` to a sorted hero-composition hash scoped by event, with a server-side proximity merge replacing the time bucket entirely.

**Tech Stack:** Python 3.12 + OpenCV + RapidOCR (client, SQLite); FastAPI + SQLAlchemy + Alembic (server, Postgres 17); pytest both sides.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-31-side-integrity-and-canonical-identity-design.md`. Read it before Task 1.
- **Mandatory ordering: Part 1 → Part 2 → Part 3 → Part 4.** Running the Part 4 server migration before the Part 1 audit falls back to survivor rule 3 (earliest capture), and for pair 1108/1109 the earliest row is the *mirrored* one — that ordering would write the wrong orientation into the pool. This is a specified error, not a scheduling preference.
- Both databases are LIVE and grow during work. **Every script takes `--cutoff <ISO timestamp>` and ignores later rows.** No test asserts an absolute row count; assert derived relationships only.
- Snapshots: client (SQLite) uses `sqlite3.Connection.backup` or `VACUUM INTO`; server (Postgres 17) uses `pg_dump` into a scratch database. `VACUUM INTO` does not exist in Postgres.
- **Never use the Edit tool on code files** — it converts straight quotes to curly ones. Use `git apply` with a unified diff; verify with `git diff` and a syntax check.
- **Client pytest runs from `src-tauri/src-python/`, NOT `src-tauri/`.** The tests live at
  `src-tauri/src-python/tests/`. Measured: `uv run pytest tests/...` from `src-tauri/`
  collects **0 items**; from `src-tauri/src-python/` it collects **249**. Every per-file
  TDD step in this plan depends on that, or the "confirm it fails" step misfires with a
  path error instead of the expected `ModuleNotFoundError`.
- Other client commands (`uv sync`, `uv run pytest` with no path) run from `src-tauri/`.
  Lint with `uvx ruff check --fix` and `uvx ruff format` from the repo ROOT, never from
  `src-tauri/`.
- Server commands run from `gameretro-adb-api/`: `pytest` (testpaths = `tests`), `alembic upgrade head`. Current Alembic head is `0004`.
- Ruff: line length 88, Google docstrings, no magic values in comparisons (name a constant), modern typing (`X | None`), `time.monotonic()` never `time.time`, numpy indexing never `cv2.split`.
- K&R braces for any brace-language code.
- Nothing in this plan touches `odds.py`, the betting rule, the auto-bet settings, or the model.

---

## File Structure

**Client — `~/Dev/webdevbar/adbautoplayer`**

| File | Responsibility |
|---|---|
| `src-tauri/src-python/scripts/solstice_frame_side_audit.py` | NEW. Audit CLI: classify every draft frame, write the report, and (with `--apply`) repair. |
| `.../services/solstice/frameside.py` | NEW. Pure functions: read a draft frame's blue/red trios, classify a row against them. No I/O, so it is testable on fixtures. |
| `.../services/solstice/matchkey.py` | MODIFY. ADD `comps_key`. `natural_key` stays until Task 14 removes it. |
| `.../services/solstice/store.py` | MODIFY. Schema migration, `finalise_identity`, push gate, origin-aware `adopt_canonical`, superseded filters. |
| `.../mixins/solstice_clash.py` | MODIFY. Call `finalise_identity` where `set_natural_key` was called. |
| `docs/solstice-clash/model-findings-ledger.md` | MODIFY. Today's closures and corrections. |

**Server — `~/Dev/webdevbar/gameretro-adb-api`**

| File | Responsibility |
|---|---|
| `app/identity.py` | MODIFY. `comps_key`, and the occurrence rule shared by ingest and migration. |
| `app/occurrence.py` | NEW. Single-linkage coalescing against stored bounds. One responsibility, used by two callers. |
| `app/models.py` | MODIFY. `comps_key`, `occurrence`, `superseded_by`, bounds, `MatchSupersession`. |
| `app/routers/matches.py` | MODIFY. Ingest via occurrence lookup; pull excludes superseded and serves tombstones. |
| `migrations/versions/0005_canonical_identity.py` | NEW. Columns, backfill, occurrence assignment, merge log. |

---

## Task 1: Read a draft frame's two trios

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/frameside.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_frameside.py`

**Interfaces:**
- Consumes: `config.SolsticeConfig.cells(cell_type)` returning `Cell(name, cell_type, x0, y0, x1, y1, side, slot)`; `vision.extract_cell(frame, cell)`; `vision.identify_cell(cell_gray, cell_type, library, cfg, candidates=None) -> Identification` with `.slug`.
- Produces: `DRAFT_CELL_TYPE = "draft_pick"`; `read_frame_sides(frame, cfg, library) -> tuple[frozenset[str], frozenset[str]]` returning `(blue_slugs, red_slugs)`.

The `draft_pick` cells are registered `left 1, right 2, right 3, left 4, left 5, right 6` — the side is on the cell, so grouping is by `cell.side`, never by slot arithmetic.

- [ ] **Step 1: Write the failing test**

```python
"""Reading a draft frame's two trios."""

import numpy as np
import pytest

from adb_auto_player.games.afk_journey.services.solstice.frameside import (
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
            _Cell("left", 1), _Cell("right", 2), _Cell("right", 3),
            _Cell("left", 4), _Cell("left", 5), _Cell("right", 6),
        ]

    def scale_chain(self, cell_type: str) -> list[float]:
        return [1.0]


_BY_NAME = {
    "left1": "lucca", "right2": "talene", "right3": "lilymay",
    "left4": "perseus", "left5": "gerda", "right6": "koko",
}


def test_groups_by_cell_side_not_slot(monkeypatch):
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    def fake_identify(cell_gray, cell_type, library, cfg, candidates=None):
        raise AssertionError("patched per-cell below")

    calls = []

    def fake_extract(frame_in, cell):
        calls.append(cell.name)
        return np.zeros((4, 4), dtype=np.uint8)

    import adb_auto_player.games.afk_journey.services.solstice.frameside as mod

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
    import adb_auto_player.games.afk_journey.services.solstice.frameside as mod

    monkeypatch.setattr(mod, "extract_cell", lambda f, c: np.zeros((4, 4), np.uint8))
    monkeypatch.setattr(
        mod,
        "identify_cell",
        lambda gray, ct, lib, cfg, candidates=None: type("I", (), {"slug": None})(),
    )

    blue, red = read_frame_sides(frame, _Cfg(), object())
    assert blue == frozenset()
    assert red == frozenset()
```

- [ ] **Step 2: Run it and confirm it fails**

Run from `src-tauri/src-python/`:
```bash
uv run pytest tests/games/afk_journey/services/solstice/test_frameside.py -v
```
Expected: FAIL, `ModuleNotFoundError: ... frameside`.

- [ ] **Step 3: Write the implementation**

```python
"""Reading the two hero trios off a DRAFT frame.

The draft screen is the only screen that carries true blue/red plates, which is what
makes it able to adjudicate a summary read. Pure: no device, no database, so it can be
run against saved frames.
"""

import numpy as np

from .config import SolsticeConfig
from .icons import IconLibrary
from .vision import extract_cell, identify_cell

# The draft screen numbers its cells 1-6 ACROSS both teams and carries the side on the
# cell itself, so grouping is by `cell.side`. Never infer a side from slot arithmetic -
# the per-side screens number 1-3 within a side and the two schemes do not agree.
DRAFT_CELL_TYPE = "draft_pick"


def read_frame_sides(
    frame: np.ndarray,
    cfg: SolsticeConfig,
    library: IconLibrary,
) -> tuple[frozenset[str], frozenset[str]]:
    """The blue and red hero slugs on one draft frame.

    Args:
        frame: BGR frame, 1080x1920.
        cfg: Solstice config, for the cell geometry.
        library: Icon library to identify against.

    Returns:
        `(blue_slugs, red_slugs)`. Unidentified cells are omitted rather than guessed,
        so a caller can tell a partial read from a complete one by set size.
    """
    sides: dict[str, set[str]] = {"left": set(), "right": set()}
    for cell in cfg.cells(DRAFT_CELL_TYPE):
        if cell.side not in sides:
            continue
        result = identify_cell(extract_cell(frame, cell), DRAFT_CELL_TYPE, library, cfg)
        if result.slug is not None:
            sides[cell.side].add(result.slug)
    return frozenset(sides["left"]), frozenset(sides["right"])
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_frameside.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Lint and commit**

```bash
cd .. && uvx ruff check --fix && uvx ruff format && cd src-tauri
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/frameside.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_frameside.py
git commit -m "feat(solstice): read a draft frame's two trios by cell side"
```

---

## Task 2: Classify a stored row against its frame

**Files:**
- Modify: `.../services/solstice/frameside.py`
- Test: `.../tests/games/afk_journey/services/solstice/test_frameside.py`

**Interfaces:**
- Consumes: `read_frame_sides` from Task 1.
- Produces: `Verdict` (StrEnum: `AGREE`, `MIRRORED`, `PARTIAL`, `UNREADABLE`, `INCOMPLETE`, `NO_ROW`) and `classify(frame_blue, frame_red, row_left, row_right) -> Verdict`.

`PARTIAL`, `UNREADABLE` and `INCOMPLETE` stay distinct: collapsing them would let a frame-reading failure count as evidence about the summary reader.

- [ ] **Step 1: Write the failing test**

```python
from adb_auto_player.games.afk_journey.services.solstice.frameside import (
    Verdict,
    classify,
)

_B = frozenset({"lucca", "perseus", "gerda"})
_R = frozenset({"talene", "lilymay", "koko"})


def test_agree_when_frame_blue_matches_row_left():
    assert classify(_B, _R, _B, _R) is Verdict.AGREE


def test_mirrored_when_frame_blue_matches_row_right():
    assert classify(_B, _R, _R, _B) is Verdict.MIRRORED


def test_unreadable_when_the_frame_gave_fewer_than_six():
    assert classify(frozenset({"lucca"}), _R, _B, _R) is Verdict.UNREADABLE


def test_incomplete_when_the_ROW_has_fewer_than_six():
    assert classify(_B, _R, frozenset({"lucca"}), _R) is Verdict.INCOMPLETE


def test_partial_when_both_are_complete_but_neither_orientation_matches():
    other = frozenset({"lucca", "perseus", "koko"})
    assert classify(_B, _R, other, _R) is Verdict.PARTIAL
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_frameside.py -v -k classify
```
Expected: FAIL, `ImportError: cannot import name 'Verdict'`.

- [ ] **Step 3: Write the implementation**

Append to `frameside.py`:

```python
from enum import StrEnum

TRIO_SIZE = 3


class Verdict(StrEnum):
    """What one frame says about one stored row."""

    AGREE = "agree"
    MIRRORED = "mirrored"
    PARTIAL = "partial"
    UNREADABLE = "unreadable"
    INCOMPLETE = "incomplete"
    NO_ROW = "no_row"


def classify(
    frame_blue: frozenset[str],
    frame_red: frozenset[str],
    row_left: frozenset[str],
    row_right: frozenset[str],
) -> Verdict:
    """Compare a frame's trios against a row's, as SETS, ignoring slot.

    Order of checks matters. The frame is judged first: if we could not read it, we
    have no basis to say anything about the row, and calling that `partial` would put
    a reader failure into the evidence for a summary-reader defect.

    Args:
        frame_blue: Blue trio read from the draft frame.
        frame_red: Red trio read from the draft frame.
        row_left: The row's `side='left'` slugs.
        row_right: The row's `side='right'` slugs.

    Returns:
        The verdict for this row.
    """
    if len(frame_blue) != TRIO_SIZE or len(frame_red) != TRIO_SIZE:
        return Verdict.UNREADABLE
    if len(row_left) != TRIO_SIZE or len(row_right) != TRIO_SIZE:
        return Verdict.INCOMPLETE
    if frame_blue == row_left and frame_red == row_right:
        return Verdict.AGREE
    if frame_blue == row_right and frame_red == row_left:
        return Verdict.MIRRORED
    return Verdict.PARTIAL
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_frameside.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

```bash
cd .. && uvx ruff check --fix && uvx ruff format && cd src-tauri
git add -A src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/frameside.py \
           src-tauri/src-python/tests/games/afk_journey/services/solstice/test_frameside.py
git commit -m "feat(solstice): classify a stored row against its draft frame"
```

---

## Task 3: The audit script (read-only)

**Files:**
- Create: `src-tauri/src-python/scripts/solstice_frame_side_audit.py`
- Test: `src-tauri/src-python/tests/scripts/test_frame_side_audit.py`

**Interfaces:**
- Consumes: `Verdict`, `classify`, `read_frame_sides`.
- Produces: `audit(db_path, frame_dir, cutoff) -> list[Row]` where `Row` has `match_id`, `verdict`, `captured_at`, `outcome`, `row_left`, `row_right`, `frame_blue`, `frame_red`; and `write_report(rows, path, cutoff) -> None`.

The database connection is opened read-only with `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`. Writing is impossible, not merely avoided.

- [ ] **Step 1: Write the failing test**

```python
"""The audit is read-only and separates its verdict classes."""

import sqlite3
from pathlib import Path

import pytest

from scripts.solstice_frame_side_audit import open_readonly, summarise


def test_connection_is_read_only(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    sqlite3.connect(db).execute("CREATE TABLE t(a INTEGER)")
    con = open_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO t(a) VALUES (1)")


def test_summarise_counts_each_verdict_separately():
    from adb_auto_player.games.afk_journey.services.solstice.frameside import Verdict

    counts = summarise(
        [Verdict.AGREE, Verdict.AGREE, Verdict.MIRRORED, Verdict.INCOMPLETE]
    )
    assert counts[Verdict.AGREE] == 2
    assert counts[Verdict.MIRRORED] == 1
    assert counts[Verdict.INCOMPLETE] == 1
    assert counts[Verdict.PARTIAL] == 0
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/scripts/test_frame_side_audit.py -v
```
Expected: FAIL, `ModuleNotFoundError: scripts.solstice_frame_side_audit`.

- [ ] **Step 3: Write the implementation**

```python
"""Does our stored side agree with what the draft frame shows?

READ ONLY. It opens the database with `mode=ro` so a write raises rather than being
merely avoided, and its only output is a markdown report.

Run:  uv run python scripts/solstice_frame_side_audit.py --cutoff 2026-07-31T12:00:00Z
"""

import argparse
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import cv2

from adb_auto_player.games.afk_journey.services.solstice.frameside import (
    Verdict,
    classify,
    read_frame_sides,
)

DEFAULT_DB = Path.home() / ".local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite"
DEFAULT_FRAMES = Path(
    os.environ.get("SOLSTICE_FRAME_DIR", "/mnt/vault/adbautoplayer/solstice-frames")
)
_FRAME_NAME = re.compile(r"^draft-(\d+)\.png$")


@dataclass(frozen=True)
class Row:
    """One frame's verdict on one match."""

    match_id: int
    verdict: Verdict
    captured_at: str
    outcome: str


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the database so that writes are impossible.

    Args:
        db_path: Path to heroes.sqlite.

    Returns:
        A read-only connection.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def summarise(verdicts: list[Verdict]) -> Counter:
    """Count each verdict, including the ones that did not occur.

    Args:
        verdicts: One verdict per audited frame.

    Returns:
        A counter carrying every member of `Verdict`, zero included.
    """
    counts = Counter({member: 0 for member in Verdict})
    counts.update(verdicts)
    return counts
```

The row-walk and report writer follow the same shape; keep them in this file. The report goes to `docs/solstice-clash/side-audit-<cutoff date>.md` and contains, per the spec: counts and rate per verdict with a Wilson interval on the mirrored rate; 2x2 tables with a two-proportion z for mirroring against `outcome`, hour of day, `left_rating > right_rating`, and having a known duplicate; the accuracy correction implied by P1a; the per-match table for every non-`AGREE` verdict; and the statement that this compares our summary read against our draft read on one machine and cannot judge another contributor's rows.

**It also writes a machine-readable sidecar**, `docs/solstice-clash/side-audit-<cutoff
date>.json`, one object per audited row:

```json
{"natural_key": "sha256:...", "match_id": 1043, "verdict": "mirrored",
 "frame_blue": ["lorsan", "sonja", "valka"], "frame_red": ["hepler", "silven", "thador"]}
```

Keyed by `natural_key`, not `match_id`: the server has no idea what our local ids mean,
and this file is the ONLY channel by which Task 11's migration learns which orientation a
frame confirmed. Rows never pushed carry `null` so the file stays a complete record, and
the migration ignores those.

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/scripts/test_frame_side_audit.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Run it for real and read the report**

```bash
uv run python scripts/solstice_frame_side_audit.py --cutoff 2026-07-31T12:00:00Z
```

**STOP after this step and show the operator the report.** A mirrored rate near 50% means the frame reader is broken, not that half the corpus is mirrored — do not proceed to Task 4 on that reading.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src-python/scripts/solstice_frame_side_audit.py \
        src-tauri/src-python/tests/scripts/test_frame_side_audit.py \
        docs/solstice-clash/side-audit-*.md
git commit -m "feat(solstice): audit stored sides against the draft frames"
```

---

## Task 4: The repair, gated and two-phase

**Files:**
- Modify: `src-tauri/src-python/scripts/solstice_frame_side_audit.py`
- Test: `src-tauri/src-python/tests/scripts/test_frame_side_repair.py`

**Interfaces:**
- Consumes: `Row`, `Verdict` from Task 3.
- Produces: `snapshot(db_path) -> Path`; `swap_sides(con, match_id) -> None`.

Two properties the tests must pin. `match_hero` carries `UNIQUE(match_id, side, slot)` and SQLite checks constraints per row, so a single `UPDATE ... SET side = CASE ...` fails mid-statement — the swap goes through a sentinel. And **`predicted_left`, player names, ratings and ranks are never touched**: the prediction is draft-relative (`solstice_clash.py:872`), the header is side-correct, and ratings are draft-derived (`solstice_clash.py:1198-1199`).

- [ ] **Step 1: Write the failing test**

```python
"""The repair flips only sides and outcome, and needs two phases to do it."""

import sqlite3
from pathlib import Path

import pytest

from scripts.solstice_frame_side_audit import swap_sides

_SCHEMA = """
CREATE TABLE match(
  id INTEGER PRIMARY KEY, outcome TEXT, predicted_left REAL,
  left_player TEXT, right_player TEXT, left_rating INTEGER, right_rating INTEGER);
CREATE TABLE match_hero(
  id INTEGER PRIMARY KEY, match_id INTEGER, side TEXT, slot INTEGER, hero_slug TEXT,
  UNIQUE(match_id, side, slot));
"""


def _db(tmp_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO match(id, outcome, predicted_left, left_player, right_player,"
        " left_rating, right_rating) VALUES (1,'left',0.72,'MERLIN','Elithes',4341,4382)"
    )
    for slot, slug in ((1, "sonja"), (2, "lorsan"), (3, "valka")):
        con.execute(
            "INSERT INTO match_hero(match_id, side, slot, hero_slug) VALUES (1,'left',?,?)",
            (slot, slug),
        )
    for slot, slug in ((1, "thador"), (2, "silven"), (3, "hepler")):
        con.execute(
            "INSERT INTO match_hero(match_id, side, slot, hero_slug) VALUES (1,'right',?,?)",
            (slot, slug),
        )
    con.commit()
    return con


def test_naive_single_update_violates_the_unique_constraint(tmp_path):
    con = _db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "UPDATE match_hero SET side = CASE side WHEN 'left' THEN 'right'"
            " ELSE 'left' END WHERE match_id=1"
        )


def test_swap_flips_sides_and_outcome(tmp_path):
    con = _db(tmp_path)
    swap_sides(con, 1)
    left = {r[0] for r in con.execute(
        "SELECT hero_slug FROM match_hero WHERE match_id=1 AND side='left'")}
    assert left == {"thador", "silven", "hepler"}
    assert con.execute("SELECT outcome FROM match WHERE id=1").fetchone()[0] == "right"


def test_swap_leaves_prediction_players_and_ratings_alone(tmp_path):
    con = _db(tmp_path)
    before = con.execute(
        "SELECT predicted_left, left_player, right_player, left_rating, right_rating"
        " FROM match WHERE id=1").fetchone()
    swap_sides(con, 1)
    after = con.execute(
        "SELECT predicted_left, left_player, right_player, left_rating, right_rating"
        " FROM match WHERE id=1").fetchone()
    assert before == after


def test_swap_applied_twice_returns_the_original(tmp_path):
    con = _db(tmp_path)
    swap_sides(con, 1)
    swap_sides(con, 1)
    left = {r[0] for r in con.execute(
        "SELECT hero_slug FROM match_hero WHERE match_id=1 AND side='left'")}
    assert left == {"sonja", "lorsan", "valka"}
    assert con.execute("SELECT outcome FROM match WHERE id=1").fetchone()[0] == "left"
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/scripts/test_frame_side_repair.py -v
```
Expected: FAIL, `ImportError: cannot import name 'swap_sides'`.

- [ ] **Step 3: Write the implementation**

```python
_SENTINEL_SIDE = "__swap__"


def swap_sides(con: sqlite3.Connection, match_id: int) -> None:
    """Flip one match's hero sides and its outcome. Nothing else.

    Two phases because `match_hero` carries UNIQUE(match_id, side, slot) and SQLite
    checks constraints per ROW, not at statement end: a single CASE update collides with
    the row it has not moved yet. A sentinel side empties one value first.

    Deliberately untouched: `predicted_left` (draft-relative, so it already refers to the
    correct side and the repair is what brings `outcome` onto the same frame), player
    names (summary HEADER, read by x-position, side-correct), and ratings and ranks
    (draft-derived). Swapping any of them would move correct data onto the wrong side.

    Args:
        con: An open, writable connection.
        match_id: The match to flip.
    """
    con.execute(
        "UPDATE match_hero SET side=? WHERE match_id=? AND side='left'",
        (_SENTINEL_SIDE, match_id),
    )
    con.execute(
        "UPDATE match_hero SET side='left' WHERE match_id=? AND side='right'",
        (match_id,),
    )
    con.execute(
        "UPDATE match_hero SET side='right' WHERE match_id=? AND side=?",
        (match_id, _SENTINEL_SIDE),
    )
    con.execute(
        "UPDATE match SET outcome = CASE outcome WHEN 'left' THEN 'right'"
        " WHEN 'right' THEN 'left' ELSE outcome END WHERE id=?",
        (match_id,),
    )
    con.commit()
```

Add `snapshot(db_path)` using `sqlite3.Connection.backup` into `heroes.sqlite.bak-<UTC>`, verifying the copy opens and its schema matches, aborting otherwise. Add the `--apply` flag: default dry-run prints the changes and exits non-zero if any exist; `--apply` snapshots, repairs every `MIRRORED` row, leaves `PARTIAL`/`UNREADABLE`/`INCOMPLETE` alone, and writes a repair log beside the snapshot listing every id before and after.

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/scripts/test_frame_side_repair.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Verify the dry run reports without writing**

```bash
uv run python scripts/solstice_frame_side_audit.py --cutoff 2026-07-31T12:00:00Z
echo "exit: $?"
```

**STOP. The operator runs `--apply` themselves, or explicitly authorises it.** This plan does not authorise mutating their database.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src-python/scripts/solstice_frame_side_audit.py \
        src-tauri/src-python/tests/scripts/test_frame_side_repair.py
git commit -m "feat(solstice): gated two-phase side repair"
```

---

## Task 5: Ledger corrections

**Files:**
- Modify: `docs/solstice-clash/model-findings-ledger.md` (line 278 carries `+0.244`; line 309 carries the "all three machines" claim)

No tests — this is documentation. It is a task rather than a footnote because the ledger is what stops these dead ends being re-derived in three weeks.

- [ ] **Step 1: Add the crowd-as-FILTER closure to "Do NOT test these again"**

Distinct from the already-closed crowd-as-model-input. Evidence: 90-cell grid; best agreement gain +3.8 points; conditional permutation p = 0.577; both cross-theme directions reverse; continuous forms (pool log-ratio, pool x crowd size, spectator calibration) all worsen held-out logloss with the pool coefficient flipping sign across themes (+0.146 / -0.156); spectator count carries no independent information (p = 0.16); `left_odds`/`right_odds` redundant with pools (R² = 0.70) and worsen logloss.

- [ ] **Step 2: Add the left-only / never-stake-right closure**

Permutation p = 0.25 under three nulls (Fable) and 0.577-0.97 (Codex); reverses out of theme; its rationale was a base-rate error — against a 42.8% right base rate the model's right calls carry +5.3 lift versus +2.6 for its left calls.

- [ ] **Step 3: Correct the intercept at line 278**

Replace the static `+0.244` with the walk-forward trajectory: ~+0.10 early, ~+0.31 mid-theme, +0.16 at the theme's end. Mark it a moving quantity, not a constant.

- [ ] **Step 4: Strike the "all three machines" claim at line 309**

Replace with the mirrored-pair evidence and a pointer to the audit report from Task 3.

- [ ] **Step 5: Add the three new entries**

Record: (a) `install.instance_uuid` appears in `match.contributor_uuid` on rows the pool echoes back, so our own rows can be miscounted as an external collector — this moved a confound test from p = 0.019 to p = 0.13; (b) P1a, that scored accuracy is understated while mirrored rows exist; (c) **pick order is closed as moot** — the plate number is a constant function of `(side, slot)`, so it carries no information the model does not already have from side, and cannot explain the left bias because it *is* the left bias restated.

- [ ] **Step 6: Commit**

```bash
git add docs/solstice-clash/model-findings-ledger.md
git commit -m "docs(solstice): record today's two closures and correct two stale claims"
```

---

## Task 6: The comps key, both sides

**Files:**
- Modify: `.../services/solstice/matchkey.py`
- Modify: `gameretro-adb-api/app/identity.py`
- Test: `.../tests/games/afk_journey/services/solstice/test_matchkey.py`
- Test: `gameretro-adb-api/tests/test_identity.py`

**Interfaces:**
- Produces, identically on both sides: `comps_key(event_slug: str, side_a_slugs: list[str], side_b_slugs: list[str]) -> str`.

The outcome is NOT an input. Sorting each trio internally kills a shuffle within a side; sorting the two trios against each other kills the swap between them and any disagreement about who won.

- [ ] **Step 1: Write the failing test (client)**

```python
from adb_auto_player.games.afk_journey.services.solstice.matchkey import comps_key

_A = ["lorsan", "sonja", "valka"]
_B = ["hepler", "silven", "thador"]

# Pinned. The server test asserts the SAME literal; if they ever diverge, both fail.
_EXPECTED_PIN = "solstice-clash|hepler,silven,thador|lorsan,sonja,valka"


def test_orientation_does_not_change_the_key():
    assert comps_key("solstice-clash", _A, _B) == comps_key("solstice-clash", _B, _A)


def test_shuffling_within_a_side_does_not_change_the_key():
    assert comps_key("solstice-clash", _A, _B) == comps_key(
        "solstice-clash", list(reversed(_A)), list(reversed(_B))
    )


def test_a_different_event_gives_a_different_key():
    assert comps_key("solstice-clash", _A, _B) != comps_key("other-event", _A, _B)


def test_payload_is_the_pinned_shape():
    import hashlib

    expected = "sha256:" + hashlib.sha256(_EXPECTED_PIN.encode()).hexdigest()
    assert comps_key("solstice-clash", _A, _B) == expected
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_matchkey.py -v -k comps
```
Expected: FAIL, `ImportError: cannot import name 'comps_key'`.

- [ ] **Step 3: Implement in `matchkey.py`**

```python
def comps_key(event_slug: str, side_a_slugs: list[str], side_b_slugs: list[str]) -> str:
    """Identity for a match: the event and its two hero trios, nothing else.

    NOT in the key, each for a measured reason:

    - The OUTCOME. Winner-first ordering survives a disagreement about which SIDE a trio
      sat on, but not a disagreement about which trio WON - and a misread panel tint is a
      failure mode on record. Sorting the trios against each other removes the outcome
      from identity entirely.
    - The TIME. A bucket splits at its boundaries: ids 1042/1044 are one match nine
      seconds apart on opposite sides of a ten-minute wall. Proximity is handled by a
      server-side lookup instead.
    - Player NAMES, ranks and ratings. Ranks are NULL on every row. Names are OCR-fragile
      in a SIDE-DEPENDENT way - profile art reads as `GAME` on one side and `GAMERETRO` on
      the other, and rows 1133/1136 read one player as `m` and `mn`. A field that reads
      differently per side is the worst possible component of a key whose whole purpose is
      to make both sides agree.
    - The THEME. It is resolved server-side from the capture window and can be backfilled
      later, which would change the key retroactively (see `identity.py`).

    Args:
        event_slug: The event this match belongs to.
        side_a_slugs: One side's hero slugs, any order.
        side_b_slugs: The other side's hero slugs, any order.

    Returns:
        `sha256:<hex>`.
    """
    a, b = sorted([",".join(sorted(side_a_slugs)), ",".join(sorted(side_b_slugs))])
    payload = f"{event_slug}|{a}|{b}"
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
```

- [ ] **Step 4: Write the identical test and implementation on the server**

`gameretro-adb-api/tests/test_identity.py` gets the same four tests importing `from app.identity import comps_key`, asserting the same `_EXPECTED_PIN` literal. Copy the function verbatim into `app/identity.py`.

- [ ] **Step 5: Run both suites**

```bash
cd ~/Dev/webdevbar/adbautoplayer/src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_matchkey.py -v
cd ~/Dev/webdevbar/gameretro-adb-api && pytest tests/test_identity.py -v
```
Expected: 4 passed each. The pinned digest is what makes a future divergence fail loudly on both sides at once.

- [ ] **Step 6: Commit both repos**

```bash
cd ~/Dev/webdevbar/adbautoplayer && git add -A && git commit -m "feat(solstice): outcome-free comps key scoped by event"
cd ~/Dev/webdevbar/gameretro-adb-api && git add -A && git commit -m "feat: outcome-free comps key scoped by event"
```

---

## Task 7: Single-linkage coalescing on bounds

**Files:**
- Create: `gameretro-adb-api/app/occurrence.py`
- Test: `gameretro-adb-api/tests/test_occurrence.py`

**Interfaces:**
- Produces: `WINDOW_SECONDS = 120`; `Cluster` dataclass with `occurrence: int`, `min_at: datetime`, `max_at: datetime`; `assign(clusters, captured_at) -> tuple[int | None, list[int]]` returning `(target_occurrence, coalesced_occurrences)`; and `coalesce(clusters, target, absorbed, captured_at) -> Cluster`, which owns the bounds merge and is used by ingest, the migration and the tests alike.

Bounds are sufficient for single-linkage in one dimension: the nearest point of a cluster to any new capture is always one of its two extremes.

- [ ] **Step 1: Write the failing test**

```python
"""Occurrence assignment is single-linkage and order-independent."""

from datetime import UTC, datetime, timedelta
from itertools import permutations

from app.occurrence import Cluster, assign, coalesce

T0 = datetime(2026, 7, 31, 8, 0, 0, tzinfo=UTC)


def _replay(offsets: list[int]) -> list[frozenset[int]]:
    """Feed offsets in the given order; return the resulting cluster memberships."""
    clusters: list[Cluster] = []
    members: dict[int, set[int]] = {}
    next_occ = 0
    for offset in offsets:
        at = T0 + timedelta(seconds=offset)
        target, merged = assign(clusters, at)
        if target is None:
            target = next_occ
            next_occ += 1
            clusters.append(Cluster(occurrence=target, min_at=at, max_at=at))
            members[target] = {offset}
        else:
            for occ in merged:
                members[target] |= members.pop(occ)
            members[target].add(offset)
            # coalesce() owns the bounds merge AND the removal. Doing it by hand here
            # is what hid the defect in review round 1.
            coalesce(clusters, target, merged, at)
    return sorted((frozenset(v) for v in members.values()), key=min)


def test_two_captures_seconds_apart_are_one_cluster():
    assert _replay([0, 9]) == [frozenset({0, 9})]


def test_captures_far_apart_are_separate_clusters():
    assert _replay([0, 3600]) == [frozenset({0}), frozenset({3600})]


def test_a_bridging_capture_coalesces_in_every_arrival_order():
    # 0 and 181 are 181s apart (outside the 120s window); 91 is within both.
    expected = [frozenset({0, 91, 181})]
    for order in permutations([0, 181, 91]):
        assert _replay(list(order)) == expected, order


def test_the_round_four_case_in_every_arrival_order():
    expected = [frozenset({0, 100, 200})]
    for order in permutations([0, 100, 200]):
        assert _replay(list(order)) == expected, order


def test_four_points_are_order_independent():
    """The case three points cannot reach.

    With the absorbed cluster's bounds discarded on merge, these offsets produce THREE
    different memberships depending on arrival order. This test is the reason
    `coalesce` exists as its own function rather than being inlined.
    """
    expected = [frozenset({0, 20, 140, 160})]
    for order in permutations([0, 20, 140, 160]):
        assert _replay(list(order)) == expected, order
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd ~/Dev/webdevbar/gameretro-adb-api && pytest tests/test_occurrence.py -v
```
Expected: FAIL, `ModuleNotFoundError: app.occurrence`.

- [ ] **Step 3: Write the implementation**

```python
"""Which occurrence a capture belongs to.

The one rule, used by BOTH ingest and the migration. They had different rules twice
during review and both times the two disagreed for out-of-order arrivals.
"""

from dataclasses import dataclass
from datetime import datetime

# Observed same-match capture gaps are 1-16 seconds, so 120 is a 7x margin at the far
# end. The nearest genuinely distinct same-comps pair in the corpus (ids 1 and 45) is
# 31.6 HOURS apart, so nothing real sits between the two scales.
WINDOW_SECONDS = 120


@dataclass
class Cluster:
    """One occurrence and the time range its captures span."""

    occurrence: int
    min_at: datetime
    max_at: datetime


def _within(cluster: Cluster, at: datetime) -> bool:
    """Whether `at` is within the window of the cluster's nearest extreme."""
    if cluster.min_at <= at <= cluster.max_at:
        return True
    if at < cluster.min_at:
        return (cluster.min_at - at).total_seconds() <= WINDOW_SECONDS
    return (at - cluster.max_at).total_seconds() <= WINDOW_SECONDS


def assign(
    clusters: list[Cluster], captured_at: datetime
) -> tuple[int | None, list[int]]:
    """Where a capture goes, and what it merges on the way.

    A capture joins EVERY cluster it is within the window of. When it bridges more than
    one they are coalesced into the lowest-numbered, which is what makes the result
    independent of arrival order - attaching only to the nearest is not enough, because
    existing clusters are otherwise never revisited.

    Bounds rather than stored captures are sufficient: in one dimension the nearest point
    of a cluster to any new capture is always one of its two extremes.

    Args:
        clusters: Existing clusters for this comps_key, any order.
        captured_at: The incoming capture's timestamp, timezone-aware.

    Returns:
        `(target_occurrence, coalesced)`. `target_occurrence` is None when the capture
        starts a new cluster; `coalesced` lists occurrences absorbed into the target.
    """
    hits = sorted(c.occurrence for c in clusters if _within(c, captured_at))
    if not hits:
        return None, []
    return hits[0], hits[1:]


def coalesce(
    clusters: list[Cluster], target: int, absorbed: list[int], captured_at: datetime
) -> Cluster:
    """Merge `absorbed` into `target` and widen the result to cover everything.

    The target's bounds must take the MINIMUM and MAXIMUM across itself, the incoming
    capture, and every absorbed cluster. Widening by the incoming point alone leaves
    bounds that no longer describe the merged membership, and the clustering then becomes
    order-dependent again - review round 1 reproduced three different outcomes across
    permutations of offsets {0, 20, 140, 160} with that bug present, and exactly one
    without it. Three-point cases cannot expose it, which is why the four-point
    permutation test below is the one that matters.

    Args:
        clusters: The live cluster list, mutated in place.
        target: Occurrence to keep.
        absorbed: Occurrences being merged into it.
        captured_at: The incoming capture.

    Returns:
        The surviving cluster, with corrected bounds.
    """
    survivor = next(c for c in clusters if c.occurrence == target)
    survivor.min_at = min(survivor.min_at, captured_at)
    survivor.max_at = max(survivor.max_at, captured_at)
    for occurrence in absorbed:
        gone = next(c for c in clusters if c.occurrence == occurrence)
        survivor.min_at = min(survivor.min_at, gone.min_at)
        survivor.max_at = max(survivor.max_at, gone.max_at)
        clusters.remove(gone)
    return survivor
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_occurrence.py -v
```
Expected: 5 passed — including all six arrival orders of both three-point cases and all
24 orders of the four-point case.

- [ ] **Step 5: Commit**

```bash
git add app/occurrence.py tests/test_occurrence.py
git commit -m "feat: single-linkage occurrence assignment on stored bounds"
```

---

## Task 8: Server test fixtures

**Files:**
- Modify: `gameretro-adb-api/tests/conftest.py`

**Interfaces:**
- Produces: fixtures `client`, `auth`, `upgraded` (a session with Alembic at head), and
  `staged` (a session stopped at revision `0005`, before the backfill).
  Existing fixtures are `session` and `seeded`; the helper in `test_post_matches.py` is
  `match(index, when, outcome, **kw)` — note the name, there is no `_match`.

**There is no `pg_snapshot` fixture, and there must not be one.** Round 10 caught the
earlier plan inventing it: this repo's tests run on IN-MEMORY SQLITE
(`create_engine("sqlite://", poolclass=StaticPool)`) and `alembic_config` uses a temp
SQLite file, so there is no Postgres test database for `pg_dump` to dump. The migration
tests therefore run on the same SQLite harness that already tests revisions 0001-0004.

`pg_dump` remains the mechanism for the REAL production migration run — an operational
precondition in Task 11, not a pytest fixture.

This has one consequence for how the migration is written: **it must be portable enough
to run on SQLite as well as Postgres**, since the tests execute it on SQLite. Use
`op.batch_alter_table` for anything SQLite cannot do in place, and avoid Postgres-only
DDL. The existing revisions already meet this bar.

Review round 1 found Tasks 9-11 written against fixtures that do not exist. Building them
is a blocking step of its own: without it, Tasks 9-12 fail at fixture resolution before
any assertion runs.

- [ ] **Step 1: Read what already exists**

```bash
cd ~/Dev/webdevbar/gameretro-adb-api
sed -n 1,80p tests/conftest.py
sed -n 20,60p tests/test_migrations.py     # `alembic_config` lives here, not in conftest
sed -n 40,70p tests/test_post_matches.py   # the `match` helper and how requests are made
```

- [ ] **Step 2: Add `client` and `auth`**

Follow whatever `test_post_matches.py` does today to build a TestClient and an authorised
header, and lift it into `conftest.py` unchanged. Do not invent a new auth scheme.

- [ ] **Step 3: Move `alembic_config` into conftest, then add `upgraded`**

`alembic_config` is defined at `tests/test_migrations.py:24` — inside a test MODULE, so a
fixture in `conftest.py` cannot consume it. Move it to `conftest.py` first (leaving
`test_migrations.py` to pick it up from there, which it will), then define TWO fixtures.

Round 4 caught the move; round 11 caught the second fixture being missing.

- **`upgraded`** - migrated to head. For the schema tests in Task 9.
- **`staged`** - migrated to **`0005` only**, stopped before the backfill:

```python
@pytest.fixture
def staged(alembic_config):
    """A database at revision 0005: new columns present, backfill NOT yet run.

    Task 11's tests must insert legacy rows with OLD natural keys and then run 0006
    over them. With a head-migrated fixture the backfill has already happened, so
    every membership, survivor and count assertion would be vacuous on zero legacy
    rows - which is exactly what round 11 caught.
    """
    command.upgrade(alembic_config, "0005")
    ...  # yield a session bound to that database
```

- [ ] **Step 4: Prove the fixtures work**

```bash
pytest tests/ -v
```
Expected: the existing suite still passes, with the new fixtures importable.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py
git commit -m "test: fixtures for client, auth and a migrated session"
```

---

## Task 9: Server schema — columns, supersession, merge log

**Files:**
- Modify: `gameretro-adb-api/app/models.py`
- Create: `gameretro-adb-api/migrations/versions/0005_canonical_identity.py`
- Test: `gameretro-adb-api/tests/test_migrations.py`

**Interfaces:**
- Produces on `Match`: `comps_key: str | None` (indexed, NOT unique), `occurrence: int | None`, `superseded_by: int | None` (FK `match.seq`), `captures_min_at: datetime | None`, `captures_max_at: datetime | None`. New tables `match_supersession(seq PK autoincrement, natural_key, superseded_by_natural_key)` and `match_merge_log(...)`.

`down_revision = "0004"`. Every column is added before any step uses it.

- [ ] **Step 1: Write the failing test**

```python
def test_0005_adds_identity_columns(upgraded):
    from sqlalchemy import inspect

    cols = {c["name"] for c in inspect(
        upgraded.bind).get_columns("match")}
    assert {"comps_key", "occurrence", "superseded_by",
            "captures_min_at", "captures_max_at"} <= cols


def test_comps_key_index_is_not_unique(upgraded):
    from sqlalchemy import inspect

    indexes = inspect(upgraded.bind).get_indexes("match")
    comps = [i for i in indexes if "comps_key" in i["column_names"]]
    assert comps, "comps_key must be indexed"
    assert not any(i["unique"] for i in comps), (
        "comps_key must NOT be unique - genuine rematches share it"
    )


def test_supersession_table_has_its_own_cursor(upgraded):
    from sqlalchemy import inspect

    cols = {c["name"] for c in inspect(
        upgraded.bind).get_columns("match_supersession")}
    assert {"seq", "natural_key", "superseded_by_natural_key"} <= cols
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
pytest tests/test_migrations.py -v -k 0005
```
Expected: FAIL — `match_supersession` does not exist.

- [ ] **Step 3: Write the migration and the model changes**

The Alembic revision adds the five `match` columns, creates `match_supersession` with its own autoincrementing `seq`, and creates `match_merge_log(seq, comps_key, survivor_seq, superseded_seq, survivor_natural_key, superseded_natural_key, orientation_verdict)`. Mirror all of it on the SQLAlchemy models.

**`0005` is SCHEMA ONLY.** The backfill is a separate revision `0006` in Task 11. Review
round 1 caught the original plan applying `0005` here and then editing its body later —
Alembic does not re-run an applied revision, so production would have gained the columns
and never run the backfill.

**Every add must be CONDITIONAL.** `0001_initial.py` builds from `Base.metadata`, so the
moment Task 9 adds these columns and tables to `app/models.py`, a FRESH database gets them
during `0001` — and an unconditional `0005` then tries to add them again and fails. This
is not hypothetical: `0002_predictions.py` already handles exactly this, inspecting first:

```python
def _match_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("match")}
```

Follow that pattern for all five columns and both new tables, and make `downgrade` check
existence the same way. Round 3 caught this; without it, `alembic upgrade head` on a fresh
database fails and the prescribed test command cannot pass.

The tombstone table needs its own cursor because marking an old row superseded does **not** advance that row's `Match.seq` — a client whose match cursor is already past it would never see the notice.

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
alembic upgrade head && pytest tests/test_migrations.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models.py migrations/versions/0005_canonical_identity.py tests/test_migrations.py
git commit -m "feat: schema for canonical identity, supersession and the merge log"
```

These tests use the `upgraded` fixture built in Task 8.

---

## Task 10: Ingest via occurrence lookup

**Files:**
- Modify: `gameretro-adb-api/app/routers/matches.py` (the key computation at ~line 75; the `IntegrityError` branch at ~124-138)
- Test: `gameretro-adb-api/tests/test_post_matches.py`

**Interfaces:**
- Consumes: `comps_key` (Task 6), `Cluster`/`assign`/`coalesce` (Task 7), the fixtures (Task 8), the schema (Task 9).

**Use the REAL helpers, version and slugs.** All three were wrong in earlier drafts and
each one silently turns a test into a 422 that never reaches the code under test:

- The helper is `match(index, when, outcome, **kw)` in `tests/test_post_matches.py` —
  there is no `_match`, and it takes `when`, not `at`. Build request bodies with the
  existing `batch(matches, version=4)` / `post(client, matches, uuid, version=4)` helpers.
- **`schema_version` must be 4.** `app/config.py:24` declares
  `schema_versions_supported = {4}`; version 1 is rejected outright.
- **Hero slugs must exist in the `hero` table.** `_validate()` rejects unknown slugs, and
  the `seeded` fixture inserts only `aliceth, alna, alsa, antandra, arden, atalanta`
  (`tests/conftest.py:64`). The existing `heroes(left, right)` helper in
  `test_post_matches.py:42` already defaults to exactly those six, so the mirrored-pair
  test is `heroes()` one way and `heroes(left=..., right=...)` swapped the other. Do not
  assume corpus slugs like `lorsan`/`hepler` exist — and the helper is `heroes`, not
  `_heroes`.
- Produces: ingest that returns `duplicate` for a same-match capture and `accepted` for a genuine rematch.

Two behaviours the tests pin. A capture accepted as a duplicate **still widens the occurrence's bounds in the same transaction** — otherwise the bridging evidence is lost when the insert rolls back, and ingest diverges from the migration. And the `IntegrityError` branch must **retry**, not return `duplicate`: two concurrent inserts of genuinely distinct matches would both mint the same occurrence, and the loser silently dropping a real match is a data-loss bug.

- [ ] **Step 1: Write the failing test**

```python
def test_mirrored_captures_of_one_match_collapse(client, auth):
    """The same match, sides and outcome mirrored, seven seconds apart."""
    a = match(when="2026-07-31T08:12:39Z", outcome="left",
              heroes=heroes())
    b = match(when="2026-07-31T08:12:46Z", outcome="right",
              heroes=heroes(left=("antandra", "arden", "atalanta"),
                            right=("aliceth", "alna", "alsa")))
    assert client.post("/v1/matches", json=batch( [a]), headers=auth
                       ).json()["results"][0]["status"] == "accepted"
    assert client.post("/v1/matches", json=batch( [b]), headers=auth
                       ).json()["results"][0]["status"] == "duplicate"


def test_the_bucket_boundary_pair_collapses(client, auth):
    """08:09:53 and 08:10:02 - nine seconds, opposite sides of a ten-minute wall."""
    a = match(when="2026-07-31T08:09:53Z")
    b = match(when="2026-07-31T08:10:02Z")
    client.post("/v1/matches", json=batch( [a]), headers=auth)
    assert client.post("/v1/matches", json=batch( [b]), headers=auth
                       ).json()["results"][0]["status"] == "duplicate"


def test_a_genuine_rematch_gets_its_own_occurrence(client, auth):
    """Ids 1 and 45 in the real corpus: same trios, opposite winners, 31.6h apart."""
    a = match(when="2026-07-26T21:39:42Z", outcome="right")
    b = match(when="2026-07-28T05:16:13Z", outcome="left")
    client.post("/v1/matches", json=batch( [a]), headers=auth)
    assert client.post("/v1/matches", json=batch( [b]), headers=auth
                       ).json()["results"][0]["status"] == "accepted"


def test_a_bridging_capture_merges_two_persisted_occurrences(client, auth, session):
    """[0, 181, 91]: the first two are outside the window, the third bridges them.

    This is the case the [0,100,200] test cannot reach - it needs TWO pre-existing
    occurrences before the bridging capture arrives.
    """
    from app.models import Match

    for when in ("2026-07-31T09:00:00Z", "2026-07-31T09:03:01Z"):
        client.post("/v1/matches",
                    json=batch([match(when=when)]),
                    headers=auth)
    assert len({m.occurrence for m in session.query(Match).all()}) == 2

    client.post("/v1/matches",
                json=batch([match(when="2026-07-31T09:01:31Z")]),
                headers=auth)
    rows = session.query(Match).all()
    assert len({m.occurrence for m in rows}) == 1, (
        "the bridging capture computed a merge but never persisted it"
    )
    assert sum(1 for m in rows if m.superseded_by is None) == 1


def test_a_merged_duplicate_widens_the_bounds(client, auth, session):
    """t, t+100s, t+200s must end as ONE occurrence - the middle capture bridges."""
    for at in ("2026-07-31T09:00:00Z", "2026-07-31T09:01:40Z", "2026-07-31T09:03:20Z"):
        client.post("/v1/matches", json=batch([match(when=at)]), headers=auth)
    from app.models import Match

    occurrences = {m.occurrence for m in session.query(Match).all()}
    assert occurrences == {0}, (
        "the middle capture rolled back without widening the bounds, so the third "
        "capture started a second occurrence"
    )
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
pytest tests/test_post_matches.py -v -k "mirrored or boundary or rematch or widens"
```
Expected: FAIL — the current key is orientation-sensitive, so the mirrored pair is `accepted` twice.

- [ ] **Step 3: Write the implementation**

Compute `comps_key` from the client's heroes and the resolved event slug; load existing
clusters for that `comps_key` within this event; call `assign`.

**On a hit, persist the COALESCENCE, not merely the bounds.** `assign` returns
`(target, coalesced)`, and `coalesced` lists the occurrences this capture bridged. In one
transaction: widen the target's bounds via `coalesce`; re-point every match in each
absorbed occurrence to the target; mark the absorbed occurrences' active rows
`superseded_by` the target's active row; write one `match_supersession` row per retirement
and one `match_merge_log` row per merge; then return `duplicate` with the target's
`natural_key`.

Round 2 caught the earlier wording widening the bounds and dropping `coalesced` on the
floor: arrival order `[0, 181, 91]` would leave two occurrences in the database while
Task 7's algorithm merges them. The `[0, 100, 200]` test cannot catch it, because it never
creates two clusters before the bridging capture arrives.

On a miss, mint the next occurrence and insert with
`natural_key = comps_key + ":" + occurrence`. Keep the savepoint and `IntegrityError`
handling for concurrency, but make it re-run the lookup once and retry rather than
answering `duplicate`.

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
pytest tests/test_post_matches.py -v
```
Expected: all passing, including the pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add app/routers/matches.py tests/test_post_matches.py
git commit -m "feat: ingest by comps key and proximity, replacing the time bucket"
```

---

## Task 11: The server backfill migration

**Files:**
- Create: `gameretro-adb-api/migrations/versions/0006_backfill_identity.py` (`down_revision = "0005"`)
- Test: `gameretro-adb-api/tests/test_migrations.py`

**Interfaces:**
- Consumes: `comps_key` (Task 6), `assign` and `coalesce` (Task 7), the schema (Task 9).

A NEW revision, not an edit of `0005`. Alembic never re-runs an applied revision, so the
backfill has to arrive as its own migration or it silently never executes in production.

Order within the migration, all inside one transaction: (1) all columns already exist from Task 9; (2) backfill `comps_key` for every match from its stored hero compositions, outcome not an input; (3) assign occurrences by inserting in `captured_at` order under the **coalescing** rule, initialising and widening bounds so the result matches replayed ingest; (4) apply the identity-survivor rule, set `superseded_by`, write `match_merge_log`; (4b) **CORRECT the survivor's orientation**; (5) rewrite `natural_key` to `comps_key:occurrence` for ACTIVE rows only.

**Step 4b is not optional and is not a verdict.** Where the frame-confirmed orientation
from the sidecar disagrees with the survivor, flip the survivor's `outcome` and its
`match_hero.side` values. The final reviewer caught that recording an
`orientation_verdict` and stopping lets an implementer pass every prescribed test while
skipping the correction — which silently defeats the entire reason Part 1 must precede
Part 4 (pair 1108/1109, where the earliest row is the mirrored one).

**Task 4's client sentinel CANNOT be reused here.** Server `match_hero` carries
`UniqueConstraint("match_id","side","slot")` AND
`CheckConstraint("side IN ('left','right')")` (`app/models.py:205-208`), so
`side='__swap__'` violates the check while a single CASE flip violates the unique
constraint. Use either a delete-and-reinsert of the six hero rows inside the same
transaction, or a slot-offset sentinel (`slot += 10`, flip side, `slot -= 10`) which stays
inside the CHECK. `match` also carries `CheckConstraint("outcome IN ('left','right')")`
(`app/models.py:122`), so the outcome flip must go straight from one legal value to the
other, never through a placeholder.

**Spec orientation rule 2 is deliberately dropped**: "prefer the contributor with the
lower measured mirrored rate" needs a per-contributor rate the audit cannot produce (we
have only our own frames). Groups with no frame evidence fall to rule 3 and are flagged
`orientation_unresolved`.

**Identity survival never moves a key between rows.** The row holding the lowest occurrence's key keeps it and stays active; every other row retains its own existing key. That is what makes a collision impossible rather than merely unlikely. Orientation is then a separate data question, resolved from the Task 3 audit's frame-confirmed verdict, falling back to earliest capture and flagging `orientation_unresolved`.

- [ ] **Step 1: Write the failing test**

```python
def test_migration_membership_matches_replayed_ingest(staged, alembic_config):
    """Cluster MEMBERSHIP must match ingest. Occurrence NUMBERS need not."""
    migrated = _membership_after_migration(staged, alembic_config)
    replayed = _membership_after_replaying_ingest(staged)
    assert migrated == replayed


def test_superseded_rows_keep_their_old_key(staged, alembic_config):
    for row in _superseded_rows(staged):
        assert not row.natural_key.endswith(f":{row.occurrence}"), (
            "a superseded row was given a canonical key; only active rows get one"
        )


def test_every_group_has_exactly_one_active_row(staged, alembic_config):
    for group in _groups(staged):
        assert sum(1 for r in group if r.superseded_by is None) == 1


def test_the_survivor_orientation_is_CORRECTED_not_just_recorded(staged, alembic_config):
    """The whole point of running the audit before the migration.

    Uses the real 1108/1109 shape: the EARLIEST row is the mirrored one, so survivor
    rule 3 alone enshrines the WRONG orientation. With the sidecar marking the later row
    frame-confirmed, the survivor must end up carrying that orientation instead.
    """
    _insert_legacy_mirrored_pair(staged)          # earliest row is the mirrored one
    _write_sidecar(confirmed_natural_key="sha256:later")
    command.upgrade(alembic_config, "0006")

    survivor = _active_row(staged)
    assert survivor.outcome == "left"
    assert _sides(staged, survivor) == {
        "left": {"aliceth", "alna", "alsa"},
        "right": {"antandra", "arden", "atalanta"},
    }


def test_counts_are_derived_not_pinned(staged, alembic_config):
    total = _completed_row_count(staged)
    assert _distinct_occurrences(staged) + _merge_count(staged) == total
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
pytest tests/test_migrations.py -v -k "membership or superseded or active or derived"
```
Expected: FAIL — the backfill does not exist yet.

- [ ] **Step 3: Import the audit verdicts**

The orientation rule needs Task 3's verdicts and they live in the CLIENT repo. Copy the
sidecar across and load it:

```bash
mkdir -p ~/Dev/webdevbar/gameretro-adb-api/migrations/data
cp ~/Dev/webdevbar/adbautoplayer/docs/solstice-clash/side-audit-*.json \
   ~/Dev/webdevbar/gameretro-adb-api/migrations/data/
```

The migration reads it as `{natural_key: verdict}` and joins on `match.natural_key`. A
server row whose key is absent has no frame evidence and falls to survivor rule 3, flagged
`orientation_unresolved` in the merge log.

**If the file is missing the migration ABORTS.** Falling back for every row is exactly
what the mandatory Part 1 → Part 4 ordering exists to prevent, and a silent version of
that is undetectable afterwards.

- [ ] **Step 4: Write the helpers the tests need**

Also write `_insert_legacy_mirrored_pair`, `_write_sidecar`, `_active_row` and `_sides`
for the orientation-correction test.

They take `staged`, not `upgraded`. Each test inserts representative legacy rows - the
mirrored pair, the boundary pair, and the 31.6-hour rematch - with OLD-format
`natural_key` values and their `match_hero` rows, then runs
`command.upgrade(alembic_config, "0006")` over that same database and asserts against
the result. That is what actually exercises the backfill, and it is also what proves
the DML runs on SQLite.

`_membership_after_migration`, `_membership_after_replaying_ingest`, `_superseded_rows`,
`_groups`, `_completed_row_count`, `_distinct_occurrences` and `_merge_count` do not
exist. Write them in the test module before the assertions that call them — round 1
caught them being referenced by tests that no step created.

- [ ] **Step 5: Write the migration body**

Follow the five steps above, using `coalesce` from Task 7 so the migration's bounds merge
is identical to ingest's.

Keep the DDL portable — the tests run this revision on SQLite via `upgraded`, while
production runs it on Postgres. Use `op.batch_alter_table` where SQLite needs it.

**Before the real production run** (an operational step, not part of the test suite):
`pg_dump` the live database and restore it into a scratch database, run the migration
there first, and compare the merge log against expectations. `VACUUM INTO` is SQLite-only
and does not exist in Postgres.

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
alembic upgrade head && pytest tests/test_migrations.py -v
```
Expected: all passed. Note the assertions are relationships, never absolute totals — the corpus grows.

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/0006_backfill_identity.py migrations/data/ tests/test_migrations.py
git commit -m "feat: backfill comps keys, assign occurrences, log every merge"
```

---

## Task 12: Pull excludes superseded rows and serves tombstones

**Files:**
- Modify: `gameretro-adb-api/app/routers/matches.py` (pull at ~line 218), `app/schemas.py`
- Test: `gameretro-adb-api/tests/test_get_matches.py`

**Interfaces:**
- Produces: pull response gains `superseded: list[{natural_key, superseded_by_natural_key}]` and a `supersession_cursor`.

- [ ] **Step 1: Write the test support these tests need**

Neither `_merged_pair` nor the `Match` import exists. Add to the top of
`tests/test_get_matches.py`:

```python
from app.models import Match, MatchSupersession


def _merged_pair(session):
    """A survivor and a row superseded by it, plus the tombstone that announces it.

    Returns:
        `(survivor, superseded)` as ORM objects.
    """
    survivor = session.query(Match).order_by(Match.seq).first()
    superseded = session.query(Match).order_by(Match.seq).offset(1).first()
    superseded.superseded_by = survivor.seq
    session.add(
        MatchSupersession(
            natural_key=superseded.natural_key,
            superseded_by_natural_key=survivor.natural_key,
        )
    )
    session.commit()
    return survivor, superseded
```

This assumes the `seeded` fixture has left at least two matches; if it has not, insert
them here rather than depending on ordering.

- [ ] **Step 2: Write the failing test**

```python
def test_pull_omits_superseded_rows(client, auth, session):
    survivor, superseded = _merged_pair(session)
    keys = {m["natural_key"] for m in client.get(
        "/v1/matches?since=0", headers=auth).json()["matches"]}
    assert survivor.natural_key in keys
    assert superseded.natural_key not in keys


def test_tombstone_reaches_a_client_whose_match_cursor_is_past_it(client, auth, session):
    """Marking a row superseded does not advance its Match.seq."""
    survivor, superseded = _merged_pair(session)
    ahead = session.query(Match).order_by(Match.seq.desc()).first().seq + 1
    body = client.get(
        f"/v1/matches?since={ahead}&supersession_since=0", headers=auth).json()
    assert body["matches"] == []
    assert {t["natural_key"] for t in body["superseded"]} == {superseded.natural_key}
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
pytest tests/test_get_matches.py -v -k "omits or tombstone"
```
Expected: FAIL — `KeyError: 'superseded'`.

- [ ] **Step 4: Write the implementation**

Filter `superseded_by IS NULL` from the match query, and serve tombstones from `match_supersession` on its own cursor.

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
pytest tests/test_get_matches.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app/routers/matches.py app/schemas.py tests/test_get_matches.py
git commit -m "feat: pull excludes superseded rows and serves tombstones on their own cursor"
```

---

## Task 13: Client schema migration

**Files:**
- Modify: `data/solstice_clash/migrate.py` — `ADD_COLUMNS` at line 29 (the column list)
- Modify: `.../services/solstice/store.py` — the cursor accessors; `_connect` at ~307
- Test: `.../tests/games/afk_journey/services/solstice/test_store_migration.py`

**Interfaces:**
- Produces on `match`: `comps_key TEXT` (indexed, NOT unique), `superseded_by INTEGER`, `captures_min_at TEXT`, `captures_max_at TEXT`.
- Produces on `install`: `supersession_cursor TEXT`.

`install` currently carries only `pull_cursor`, and `SyncClient.pull()` sends only `since`
(`sync.py:219-220`). Task 12's tombstones ride a SEPARATE server sequence — marking a row
superseded does not advance its `Match.seq` — so the client needs its own cursor. Round 5
caught this missing entirely: without it the client either cannot request tombstones, or
asks from zero every time, re-reading page one forever and permanently missing later
retirements once tombstones paginate.

`natural_key` is already nullable and UNIQUE (`schema.sql:151`), so no constraint change is needed there.

- [ ] **Step 1: Write the failing test**

```python
def test_migration_adds_the_identity_columns(tmp_path):
    store = MatchStore(tmp_path / "h.sqlite")
    cols = {r[1] for r in store._connect().execute("PRAGMA table_info(match)")}
    assert {"comps_key", "superseded_by",
            "captures_min_at", "captures_max_at"} <= cols


def test_comps_key_is_not_unique(tmp_path):
    """Local mirrored pairs must be able to coexist until the server reconciles them."""
    store = MatchStore(tmp_path / "h.sqlite")
    con = store._connect()
    con.execute("INSERT INTO match(source, captured_at, comps_key)"
                " VALUES ('spectate_summary','2026-07-31T08:00:00Z','sha256:x')")
    con.execute("INSERT INTO match(source, captured_at, comps_key)"
                " VALUES ('spectate_summary','2026-07-31T08:00:07Z','sha256:x')")
    con.commit()  # must not raise


def test_migration_is_idempotent(tmp_path):
    """`_schema_ensured` is a CLASS-level cache, so a second MatchStore(path) is a no-op.

    Without the discard this passes while proving nothing - the existing suite already
    knows the idiom (`test_store.py:763`). Caught by the final reviewer.
    """
    path = tmp_path / "h.sqlite"
    MatchStore(path)
    MatchStore._schema_ensured.discard(path)
    MatchStore(path)  # must not raise "duplicate column name"


def test_install_gains_a_supersession_cursor(tmp_path):
    store = MatchStore(tmp_path / "h.sqlite")
    cols = {r[1] for r in store._connect().execute("PRAGMA table_info(install)")}
    assert "supersession_cursor" in cols


def test_the_two_cursors_move_independently(tmp_path):
    store = MatchStore(tmp_path / "h.sqlite")
    store.set_pull_cursor(120)
    store.set_supersession_cursor(3)
    assert store.pull_cursor() == 120
    assert store.supersession_cursor() == 3
    store.set_pull_cursor(200)
    assert store.supersession_cursor() == 3, (
        "the match cursor must not drag the supersession cursor with it"
    )
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_store_migration.py -v
```

- [ ] **Step 3: Write the implementation**

Extend the existing startup migration in the same style it already uses for
`predicted_left`. That earlier gap cost a contributor every match to
`no such column: predicted_left` — 54 occurrences in one collaborator's log — so the
idempotence test is not ceremony.

Add `supersession_cursor` to `install`, plus `MatchStore.supersession_cursor()` and
`set_supersession_cursor(seq)` mirroring `pull_cursor()` / `set_pull_cursor()` at
`store.py:568-575`.

**The column list lives in `data/solstice_clash/migrate.py` at the REPO ROOT**, not in
`store.py`. `ADD_COLUMNS` is at `migrate.py:29`, applied at `migrate.py:203`, and the
precedent this task follows — `("match", "predicted_left", "REAL")` — is at
`migrate.py:45`. Add the four `match` columns and `install.supersession_cursor` there, in
that form.

`store.py` does not own the list. `_ensure_schema` (line 224) loads the module
dynamically through `resource_file(Path("solstice_clash") / "migrate.py")` and reads
`getattr(migrate_module, "ADD_COLUMNS", [])` at line 295. Its docstring's "a shipped build
never runs `migrate.py`" means nobody runs it BY HAND — the next sentence says
"`migrate.py` is called rather than reimplemented. Two copies of a migration list drift,
and the one that drifts is the one nobody runs by hand."

So: columns go in `migrate.py`; the cursor accessor methods go in `store.py`. Do not
reimplement the column adds in `store.py` — that is the exact drift the docstring warns
about, and an earlier draft of this note sent an implementer looking for a list in
`store.py` that does not exist.

- [ ] **Step 4: Run the tests and confirm they pass**

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(solstice): client columns for comps key, supersession and bounds"
```

---

## Task 14: `finalise_identity` replaces `set_natural_key`

**Files:**
- Modify: `.../services/solstice/store.py`
- Modify: `.../mixins/solstice_clash.py` — **BOTH** `set_natural_key` call sites: line 614
  (compete flow) and line 1339 (spectate flow)
- Test: `.../tests/games/afk_journey/services/solstice/test_finalise_identity.py`

**Interfaces:**
- Produces: `MatchStore.finalise_identity(match_id: int) -> None`.

`record_match()` cannot do this: the spectate flow creates the row at `solstice_clash.py:1186`, **before** the heroes exist, so `comps_key` is not computable there. A provisional row keeps `comps_key` NULL, which the push gate excludes.

`pushable_matches()` yields DICTS keyed `local_id`, not tuples — `m[0]` raises
`KeyError: 0`. Round 4 caught every assertion in Tasks 14 and 15 indexing it as a tuple.

**There are THREE `natural_key` usages in `solstice_clash.py`, and this task owns all of
them.** Task 6 is purely ADDITIVE — it adds `comps_key` and leaves `natural_key` in place,
because `solstice_clash.py:49` imports it and the existing `test_matchkey.py` still tests
it. Removing it at Task 6 would break the client at import and leave it broken for eight
tasks. The final reviewer caught that contradiction.

The three sites:

1. `set_natural_key` at line 614 — the compete flow.
2. `set_natural_key` at line 1339 — the spectate flow.
3. **The compete-flow backstop at lines 566-573**: `key = natural_key(...)` followed by
   `match_by_natural_key(key)`, logged as `[SC-41]`. No earlier draft touched it. Migrate
   it to compute `comps_key` and look up by that instead — the backstop's whole purpose is
   recognising a match we already recorded, and an orientation-sensitive key cannot do
   that. If it is left alone it becomes either an undefined name or inert dead code.

Only once all three are migrated may `natural_key` and its tests be removed from
`matchkey.py`, in this task.

Event slug resolution is an ordered fallback — `match.event_id → event.slug`, else `match.theme_id → theme.event_id → event.slug`, else leave `comps_key` NULL and skip. 120 of 1,200 keyed rows have `event_id` NULL and all 120 resolve through `theme_id`.

- [ ] **Step 1: Write the shared client test support**

Create `tests/games/afk_journey/services/solstice/_support.py`. Tasks 14 and 15 both
import from it. None of this exists today — round 8 found nine helpers and the `store`
fixture referenced by tests that no step created.

```python
"""Shared fixtures and row builders for the identity tests."""

import pytest

from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore

_LEFT = ("aliceth", "alna", "alsa")
_RIGHT = ("antandra", "arden", "atalanta")


@pytest.fixture
def store(tmp_path):
    """A fresh, migrated database with its foreign keys satisfied.

    `store.py:311` enables `PRAGMA foreign_keys = ON`, and `match.event_id`,
    `match.theme_id` and `match_hero.hero_slug` are all real foreign keys. Without these
    reference rows the very first `_record()` raises
    `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
    """
    store = MatchStore(tmp_path / "heroes.sqlite")
    with store._connect() as con:
        con.execute(
            "INSERT OR IGNORE INTO event(id, slug, name, game)"
            " VALUES (1, 'solstice-clash', 'Solstice Clash', 'afk-journey')"
        )
        con.execute(
            "INSERT OR IGNORE INTO theme(id, event_id, slug, name, is_default)"
            " VALUES (4, 1, 'flourishing-wilds', 'Flourishing Wilds', 0)"
        )
        # `hero.name` is NOT NULL - a slug-only insert fails.
        for slug in _LEFT + _RIGHT:
            con.execute(
                "INSERT OR IGNORE INTO hero(slug, name) VALUES (?, ?)",
                (slug, slug.title()),
            )
    return store


def _record(store, at="2026-07-31T08:12:39Z", mirrored=False,
            event_id=1, theme_id=4, origin="local"):
    """Insert one complete match with six identified heroes.

    Returns:
        The new local match id.
    """
    left, right = (_RIGHT, _LEFT) if mirrored else (_LEFT, _RIGHT)
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin, outcome, event_id, theme_id)"
            " VALUES ('spectate_summary', ?, ?, 'left', ?, ?)",
            (at, origin, event_id, theme_id),
        )
        match_id = int(cur.lastrowid)
        for side, slugs in (("left", left), ("right", right)):
            for slot, slug in enumerate(slugs, start=1):
                con.execute(
                    "INSERT INTO match_hero(match_id, side, slot, hero_slug, status)"
                    " VALUES (?, ?, ?, ?, 'identified')",
                    (match_id, side, slot, slug),
                )
    return match_id


def _record_without_heroes(store, at="2026-07-31T08:12:39Z"):
    """A provisional row: created before its heroes were read."""
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin)"
            " VALUES ('spectate_summary', ?, 'local')",
            (at,),
        )
        return int(cur.lastrowid)


def _two_local_rows_sharing_a_comps_key(store):
    """Two local observations of one match, mirrored, seven seconds apart."""
    a = _record(store, at="2026-07-31T08:12:39Z")
    b = _record(store, at="2026-07-31T08:12:46Z", mirrored=True)
    return a, b


def _duplicate_pair_one_superseded(store):
    a, b = _two_local_rows_sharing_a_comps_key(store)
    store.finalise_identity(a)
    store.finalise_identity(b)
    return a, b


def _local_row(store):
    return _record(store, origin="local")


def _synced_row(store, natural_key):
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin, natural_key)"
            " VALUES ('spectate_summary', '2026-07-31T08:00:00Z', 'synced', ?)",
            (natural_key,),
        )
        return int(cur.lastrowid)


def _pushed_local_row(store):
    match_id = _record(store)
    store.finalise_identity(match_id)
    store.adopt_canonical(match_id, "sha256:k:0", None, None)
    return match_id


def _scalar(store, sql, *params):
    with store._connect() as con:
        row = con.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _superseded_by(store, match_id):
    return _scalar(store, "SELECT superseded_by FROM match WHERE id=?", match_id)


def _comps_key(store, match_id):
    return _scalar(store, "SELECT comps_key FROM match WHERE id=?", match_id)


def _natural_key(store, match_id):
    return _scalar(store, "SELECT natural_key FROM match WHERE id=?", match_id)


def _exists(store, match_id):
    return _scalar(store, "SELECT 1 FROM match WHERE id=?", match_id) is not None


def _hero_count(store, match_id):
    return _scalar(
        store, "SELECT COUNT(*) FROM match_hero WHERE match_id=?", match_id
    )


def _row_for_key(store, natural_key):
    return _scalar(store, "SELECT id FROM match WHERE natural_key=?", natural_key)


def _insert_synced_row(store, natural_key):
    return _synced_row(store, natural_key)
```

**Import these EXPLICITLY, never with a wildcard.** Every helper is underscore-prefixed,
and `from ._support import *` silently skips underscore names unless the module defines
`__all__` — which it deliberately does not, because the fixture must be imported by name
for pytest to see it. Both test modules start with:

```python
from ._support import (  # noqa: F401 - `store` is a fixture, used by name
    store,
    _comps_key,
    _duplicate_pair_one_superseded,
    _exists,
    _hero_count,
    _insert_synced_row,
    _local_row,
    _natural_key,
    _pushed_local_row,
    _record,
    _record_without_heroes,
    _row_for_key,
    _superseded_by,
    _synced_row,
    _two_local_rows_sharing_a_comps_key,
)
```

The reference inserts already account for the real NOT NULL columns: `event.slug`,
`event.name`, `event.game`, `theme.name` and `hero.name`.

- [ ] **Step 2: Write the failing test**

```python
def test_second_observation_is_superseded_not_inserted_twice(store):
    first = _record(store, at="2026-07-31T08:12:39Z")
    second = _record(store, at="2026-07-31T08:12:46Z", mirrored=True)
    store.finalise_identity(first)
    store.finalise_identity(second)
    assert _superseded_by(store, second) == first
    assert _superseded_by(store, first) is None


def test_a_bridging_capture_coalesces_two_local_occurrences(store):
    a = _record(store, at="2026-07-31T09:00:00Z")
    b = _record(store, at="2026-07-31T09:03:01Z")
    store.finalise_identity(a)
    store.finalise_identity(b)
    c = _record(store, at="2026-07-31T09:01:31Z")
    store.finalise_identity(c)
    assert _superseded_by(store, b) == a
    assert _superseded_by(store, c) == a


def test_a_superseded_row_is_still_pushable(store):
    """Withholding it would starve the server of the bridging evidence."""
    first = _record(store, at="2026-07-31T08:12:39Z")
    second = _record(store, at="2026-07-31T08:12:46Z")
    store.finalise_identity(first)
    store.finalise_identity(second)
    assert second in {m["local_id"] for m in store.pushable_matches()}


def test_a_provisional_row_without_heroes_is_never_pushable(store):
    match_id = _record_without_heroes(store)
    assert match_id not in {m["local_id"] for m in store.pushable_matches()}


def test_event_slug_falls_back_through_theme(store):
    match_id = _record(store, event_id=None, theme_id=4)
    store.finalise_identity(match_id)
    assert _comps_key(store, match_id) is not None


def test_no_set_natural_key_call_sites_remain():
    """Both the compete (614) and spectate (1339) paths must have migrated."""
    from pathlib import Path

    # Derive the path from the imported module, never from the CWD.
    from adb_auto_player.games.afk_journey.mixins import solstice_clash

    source = Path(solstice_clash.__file__).read_text()
    assert "set_natural_key(" not in source
    assert "natural_key(" not in source, (
        "the SC-41 backstop at 566-573 still computes an orientation-sensitive key"
    )
    assert source.count("finalise_identity(") >= 2
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_finalise_identity.py -v
```

- [ ] **Step 4: Write the implementation**

`finalise_identity` computes `comps_key`, finds local matches sharing it whose bounds are within the window, and either stores the key and initialises bounds, or widens the earliest match's bounds and marks the new row plus any other bridged rows `superseded_by` that earliest row, re-pointing existing chains one level deep. Hero rows, the draft frame and the odds sample stay on the superseded row — Task 3's audit needs them.

Change the push gate to `origin='local' AND comps_key IS NOT NULL AND pushed_at IS NULL AND push_rejected_reason IS NULL`. **No supersession term**: the server is the sole deduplicator, and withholding a superseded row withholds the bridging evidence.

- [ ] **Step 5: Run the tests and confirm they pass**

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(solstice): finalise identity after the heroes are known"
```

---

## Task 15: Origin-aware adoption, tombstones, and superseded-free analysis

**Files:**
- Modify: `.../services/solstice/store.py` (`adopt_canonical` at 655-694, `matches_for_fit`, `scored_predictions` at ~534), `.../services/solstice/sync.py` — the pull path at ~188 AND `pull()` at ~214-232, which currently requests only `/v1/matches?since={since}&limit={PULL_LIMIT}`
- Modify: `scripts/solstice_side_audit.py`, `scripts/solstice_crowd_agreement.py`
- Test: `.../tests/games/afk_journey/services/solstice/test_adopt_and_tombstones.py`

**Interfaces:**
- Consumes: everything above.

Four changes. `adopt_canonical` currently deletes **any** row holding the server's key (`store.py:677-682`) on the documented assumption it can only be a synced copy — under an orientation-invariant key two LOCAL rows collide and it would delete the frame-confirmed one with its hero evidence. Tombstone retirement must clear `natural_key`, `pushed_at` **and** `push_rejected_reason` together, or the row is permanently unpushable. And `matches_for_fit` and `scored_predictions` must exclude superseded rows, or every duplicate stays a full observation in the model fit and in every accuracy figure.

**Supersession must resolve to a ROOT, and it must be acyclic.** Review round 1 found a
cycle: Task 14 may already have set `B.superseded_by = A`; if `B` then adopts the canonical
key first, a naive "mark the adopting row superseded" sets `A.superseded_by = B` while
`B.superseded_by = A` still stands. Both rows drop out of the fit and the scoring, and
retirement — which skips already-superseded rows — can never break the cycle. Adoption must
therefore walk to the existing supersession root, keep that root active, and never write a
link that points back into the chain.

- [ ] **Step 1: Write the failing test**

```python
def test_adoption_never_deletes_a_local_row(store):
    a, b = _two_local_rows_sharing_a_comps_key(store)
    store.adopt_canonical(a, "sha256:k:0", "flourishing-wilds", "window")
    store.adopt_canonical(b, "sha256:k:0", "flourishing-wilds", "window")
    assert _exists(store, a) and _exists(store, b)
    assert _superseded_by(store, b) == a
    assert _hero_count(store, b) == 6


def test_adoption_still_deletes_a_clashing_synced_row(store):
    local = _local_row(store)
    synced = _synced_row(store, natural_key="sha256:k:0")
    store.adopt_canonical(local, "sha256:k:0", None, None)
    assert not _exists(store, synced)


def test_retiring_a_local_row_makes_it_pushable_again_exactly_once(store):
    row = _pushed_local_row(store)
    store.retire_for_tombstone(row)
    assert row in {m["local_id"] for m in store.pushable_matches()}
    store.adopt_canonical(row, "sha256:k:0", None, None)
    assert row not in {m["local_id"] for m in store.pushable_matches()}


def test_the_fit_and_the_scoring_exclude_superseded_rows(store):
    """One match's worth of evidence, not two.

    `matches_for_fit()` returns one row PER IDENTIFIED HERO (store.py:539-553), so a
    three-a-side match is six rows, not one - assert on distinct match ids instead. And
    `scored_predictions()` selects only rows with `predicted_left IS NOT NULL`, so the
    prediction has to be recorded explicitly or the count is zero either way. Round 12
    caught both.
    """
    a, b = _duplicate_pair_one_superseded(store)
    for match_id in (a, b):
        store.record_prediction(
            match_id, 0.72, "r+h", 6, "2026-07-31T08:12:40Z"
        )

    fit_match_ids = {row[0] for row in store.matches_for_fit()}
    assert fit_match_ids == {a}, "the superseded row must not contribute hero evidence"

    assert len(store.scored_predictions()) == 1, (
        "a duplicate observation must not be scored twice"
    )


def test_the_supersession_cursor_advances_independently(store, monkeypatch):
    """A tombstone is consumed and its cursor advances without the match cursor.

    No fake HTTP server: `SyncClient._request` is the seam. Stubbing it keeps the test
    on the cursor logic, which is what is under test, and avoids standing up a transport
    harness that would itself need testing.
    """
    from adb_auto_player.games.afk_journey.services.solstice.sync import (
        SyncClient,
        SyncConfig,
    )

    # SyncConfig is a frozen dataclass with FOUR required fields and no defaults
    # (sync.py:69-74). Passing only `enabled` raises TypeError before pull() is reached.
    client = SyncClient(
        store,
        SyncConfig(base_url="http://test.invalid", api_key="k",
                   enabled=True, timeout=1.0),
        client_version="test",
    )
    seen: list[str] = []

    def fake_request(method, path, body=None):
        seen.append(path)
        return {
            "matches": [],
            "superseded": [
                {"natural_key": "sha256:old",
                 "superseded_by_natural_key": "sha256:new:0"}
            ],
            "supersession_cursor": 7,
        }

    monkeypatch.setattr(client, "_request", fake_request)
    _insert_synced_row(store, natural_key="sha256:old")
    store.set_pull_cursor(500)

    client.pull()

    assert "supersession_since=0" in seen[0], "the second cursor must be sent"
    assert store.supersession_cursor() == 7
    assert store.pull_cursor() == 500, (
        "an empty match page must not move the match cursor"
    )
    assert _row_for_key(store, "sha256:old") is None


def test_adoption_out_of_order_does_not_create_a_cycle(store):
    """B already superseded by A locally, then B adopts the canonical key FIRST."""
    a, b = _two_local_rows_sharing_a_comps_key(store)
    store.finalise_identity(a)
    store.finalise_identity(b)          # sets B.superseded_by = A
    store.adopt_canonical(b, "sha256:k:0", None, None)
    store.adopt_canonical(a, "sha256:k:0", None, None)

    roots = [r for r in (a, b) if _superseded_by(store, r) is None]
    assert len(roots) == 1, "exactly one row must remain active"
    assert _natural_key(store, roots[0]) == "sha256:k:0", (
        "the sole active root must own the canonical key"
    )
    assert _natural_key(store, [r for r in (a, b) if r != roots[0]][0]) is None
    # Distinct match ids, not row count: matches_for_fit returns one row per identified
    # hero (store.py:539-553), so one three-a-side match is six rows.
    assert {row[0] for row in store.matches_for_fit()} == {roots[0]}
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/games/afk_journey/services/solstice/test_adopt_and_tombstones.py -v
```
Expected: FAIL — the first test fails by deleting row `a`.

- [ ] **Step 3: Write the implementation**

Import the shared support module built in Task 14 —
`tests/games/afk_journey/services/solstice/_support.py` — which provides the `store`
fixture and every `_`-prefixed helper these tests use. Build the test seam on top of it. There is no `fake_server` fixture in this
repo and this plan does not create one — `SyncClient._request` (`sync.py:110`) is the
seam, and stubbing it keeps the test on the cursor logic instead of on a transport harness
that would itself need testing.

`SyncConfig` is a frozen dataclass with four required fields and no defaults
(`sync.py:69-74`), so construct it fully.

`_insert_synced_row` and `_row_for_key` already live in that module; they are repeated
here so this task reads standalone:

```python
def _insert_synced_row(store, natural_key: str) -> int:
    """A pulled row, as `upsert_synced` would have left it."""
    with store._connect() as con:
        cur = con.execute(
            "INSERT INTO match(source, captured_at, origin, natural_key)"
            " VALUES ('spectate_summary', '2026-07-31T08:00:00Z', 'synced', ?)",
            (natural_key,),
        )
        return int(cur.lastrowid)


def _row_for_key(store, natural_key: str) -> int | None:
    """The local row id holding this key, or None once it has been retired."""
    with store._connect() as con:
        row = con.execute(
            "SELECT id FROM match WHERE natural_key=?", (natural_key,)
        ).fetchone()
    return None if row is None else int(row[0])
```

Round 6 caught the earlier test referencing a fixture and a module-level `sync` name that
do not exist; round 7 caught these two helpers and the `SyncConfig` signature.

Wire the second cursor through `pull()`: send
`&supersession_since={store.supersession_cursor()}`, consume the response's `superseded`
list, and advance `set_supersession_cursor(...)` from the response's own cursor — never
from the match cursor, which moves independently.

Make the delete conditional on `origin='synced'`. For a `local` clash, resolve the chain
to its ROOT and make that root the sole active, keyed row:

1. Walk `superseded_by` from the clashing row to its root.
2. If the root is not the row being adopted, mark the adopted row `superseded_by` the
   root, set its `pushed_at`, and leave its `natural_key` NULL.
3. If the root IS the row being adopted but the canonical key is currently held by a row
   further down the chain, **move the key in one transaction**: clear it from that row,
   then set it on the root. Client `natural_key` is UNIQUE, so assigning it to the root
   while another row still holds it raises; and leaving it on a superseded row means the
   active root is unkeyed and can never be recognised on the next pull.

Round 2 found the earlier wording satisfied neither ordering: adopting B before A either
raised on the unique constraint or left the key on the superseded row. Add `retire_for_tombstone`, skipping rows already superseded locally so the cycle cannot restart. Add `AND superseded_by IS NULL` to `matches_for_fit`, `scored_predictions`, and both audit scripts.

- [ ] **Step 4: Run the whole client suite**

```bash
uv run pytest
```
Expected: everything green. This is the task most likely to break existing tests, because the analysis queries change shape.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(solstice): origin-aware adoption, tombstones, superseded-free analysis"
```

---

## Self-Review

**Spec coverage.** P1 → Tasks 1-4. P1a → Task 4 step 3 (`predicted_left` untouched) and Task 5 step 5. P2 → Tasks 6-12. P3 → Task 5. Snapshot discipline → Global Constraints, Task 4 step 3, Task 11 step 4. D1 closed → Task 5 step 5. D2 closed → Task 11 plus the ordering constraint. Client identity vs server identity → Tasks 13-15. Occurrence coalescing → Task 7. Accepted limitation → Task 10's rematch test. Pull semantics → Task 12. Server test fixtures → Task 8.

**Placeholders.** Task 3's report writer and Task 11's migration body are described by their required content rather than shown line-by-line; both are long and mechanical, and every field, ordering rule and output section they must produce is enumerated. Every other code step carries runnable code.

**Type consistency.** `comps_key(event_slug, side_a_slugs, side_b_slugs) -> str` is identical on both sides and pinned by a shared digest literal. `assign(clusters, captured_at) -> (int | None, list[int])` is used only by Tasks 9 and 10. `Verdict` members are referenced by the same names in Tasks 2, 3 and 4. `swap_sides(con, match_id)` and `finalise_identity(match_id)` match their call sites.

**One deliberate ordering dependency:** Task 11's survivor rule reads the Task 3 audit's verdicts. Running Part 4 before Part 1 is the specified error described in Global Constraints.
