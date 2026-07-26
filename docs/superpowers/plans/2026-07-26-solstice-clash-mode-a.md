# Solstice Clash Mode A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an unattended AFK Journey mode that spectates Solstice Clash matches in a loop, records each match outcome from the post-match summary, and uses that OCR-confirmed ground truth to measure and tune hero identification on the draft and prematch screens.

**Architecture:** A thin device-facing mixin (`SolsticeClashMixin`) owns navigation, taps and the loop. Everything interpretive lives in pure modules (`summary.py`, `naming.py`, `tuning.py`) that take a frame and return data, so they are unit-testable against committed fixture frames with no device. Persistence extends the existing `MatchStore`. Schema moves v2 -> v3, adding a `screen` registry, an `identification_audit` trail, and `hero_screen_transform` learned parameters gated by database triggers.

**Tech Stack:** Python 3.13, OpenCV, RapidOCR (via `adb_auto_player.ocr`), SQLite, pytest. All existing AdbAutoPlayer primitives are reused - see Global Constraints.

**Spec:** `docs/superpowers/specs/2026-07-26-solstice-clash-phase2-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Reuse before building.** Check AdbAutoPlayer for an existing component before writing any capability. Confirmed reusable: `DeviceStream`/`get_screenshot()`, `wait_for_template`, `_navigate_menu_chain`, `navigate_to_world`, `PopupMessageHandler`/`PopupMessage`, `RapidOCRBackend`, `StringHelper.fuzzy_substring_match`, `@register_command` + `GUIMetadata`. New code is justified only where nothing equivalent exists.
- **`device_streaming=False`.** Start with `self.start_up(device_streaming=False)`. H264 degrades OCR of stylised text (`'DefeatVictory'` from screencap vs `'Defe'`+`'featVictory'` from stream) and OCR is this mode's ground truth. Six existing mixins already do this.
- **Accept rule unchanged:** `score >= 0.70 AND margin >= 0.10`. Tuning maximises **margin**, not raw score.
- **Never derive side from the `Ally`/`Enemy` labels.** Tab colours are static UI (identical to within 0.05 across matches). In spectate, blue = left player = Ally panel. Verified twice.
- **Never tick "Don't remind for 7 days"** - `PopupMessage.has_dont_remind_me` must stay `False`; when True the handler taps the checkbox (`popup_message_handler.py:278-280`), changing the user's game settings.
- **Never `shutil.rmtree` the training frame directory.** `guild_member_scan.py:52-53` does this for debug output; copying it would delete every archived training frame.
- **Large files go to `/mnt/vault`**, never `/tmp` (16GB tmpfs, already filled once by this project).
- **No em dashes** in any prose, comments or commit messages. Use a spaced hyphen.
- **K&R braces** where applicable; match existing file style when editing.
- **No Claude attribution** in commit messages.
- **Edit code via `git apply` of a unified diff**, not the Edit tool (it corrupts quotes in code files). Verify every edit with `git diff` plus the narrowest test.
- Existing 46 solstice tests and the repo-wide suite must stay green after every task.

## File Structure

| file | responsibility |
|---|---|
| `data/solstice_clash/schema.sql` | **modify** - add `screen`, `identification_audit`, `hero_screen_transform`, 2 triggers, 5 `match_hero` columns |
| `data/solstice_clash/migrate.py` | **modify** - `SCHEMA_VERSION = 3`, `ADD_COLUMNS` entries, seed `screen` + `cell_registry` rows |
| `.../services/solstice/store.py` | **modify** - `record_audit`, `learn_transform`, `transform_for`, `_SOURCES` |
| `.../services/solstice/summary.py` | **new** - pure: summary frame in, `SummaryRead` out |
| `.../services/solstice/naming.py` | **new** - pure: OCR blocks in, hero slug out (fuzzy) |
| `.../services/solstice/tuning.py` | **new** - pure: margin-maximising crop/scale search |
| `.../mixins/solstice_clash.py` | **new** - navigation, loop, taps, retries, reset policy |
| `.../templates/event/solstice_clash/*.png` | **new** - cut navigation and result templates |
| `tests/.../solstice/test_schema_v3.py` | **new** |
| `tests/.../solstice/test_summary.py` | **new** |
| `tests/.../solstice/test_naming.py` | **new** |
| `tests/.../solstice/test_tuning.py` | **new** |
| `tests/.../solstice/data/*.png` | **new** - 5 fixture frames (~12MB, deliberate: geometry tests need native 1080x1920) |

Path prefixes, used throughout:
- `SVC` = `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice`
- `TST` = `src-tauri/src-python/tests/games/afk_journey/services/solstice`
- `AFKJ` = `src-tauri/src-python/adb_auto_player/games/afk_journey`

Run tests from `src-tauri/src-python` with `/mnt/docs/adbautoplayer/.venv/bin/python -m pytest`.

---

### Task 1: Schema v3 - screen registry, audit trail, gated transforms

**Files:**
- Modify: `data/solstice_clash/schema.sql`
- Modify: `data/solstice_clash/migrate.py`
- Test: `TST/test_schema_v3.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: tables `screen(id, slug, description, base_resolution, crop_half_w, crop_top, crop_bottom)`, `identification_audit(id, match_id, screen_id, side, slot, image_slug, image_art_ref, image_score, image_margin, ocr_slug, agreed, frame_path, created_at)`, `hero_screen_transform(id, screen_id, hero_slug, art_ref, scale, crop_half_w, crop_top, crop_bottom, score, margin, confirmed_by, audit_id, verified_at)`; `match_hero` gains `stat_sword, stat_heart, stat_shield, power, identified_by`; seeded `screen` slugs `solstice_summary`, `spectate_draft_picks`, `spectate_prematch`.

- [ ] **Step 1: Write the failing test**

Create `TST/test_schema_v3.py`:

```python
"""Schema v3: the confirmation gate must be enforced by the DATABASE, not by callers.

Every test here is a bypass attempt. If any of them succeeds, a learned transform could
be based on evidence that does not confirm it, which silently corrupts identification.
"""

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[7]
MIGRATE = REPO / "data" / "solstice_clash" / "migrate.py"
SHIPPED_DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """A migrated copy of the shipped database. Never migrate the shipped file itself."""
    target = tmp_path / "heroes.sqlite"
    shutil.copy(SHIPPED_DB, target)
    subprocess.run(
        [sys.executable, str(MIGRATE), str(target)], check=True, capture_output=True
    )
    return target


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def screen_id(con: sqlite3.Connection, slug: str) -> int:
    return int(con.execute("SELECT id FROM screen WHERE slug=?", (slug,)).fetchone()[0])


def test_screens_are_seeded(db: Path) -> None:
    con = connect(db)
    slugs = {r[0] for r in con.execute("SELECT slug FROM screen")}
    assert {"solstice_summary", "spectate_draft_picks", "spectate_prematch"} <= slugs


def test_match_hero_has_stat_columns(db: Path) -> None:
    con = connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(match_hero)")}
    assert {
        "stat_sword",
        "stat_heart",
        "stat_shield",
        "power",
        "identified_by",
    } <= cols


def test_migration_is_idempotent(db: Path) -> None:
    """schema.sql is executed on every run, so every CREATE needs IF NOT EXISTS."""
    before = connect(db).execute("SELECT COUNT(*) FROM screen").fetchone()[0]
    subprocess.run(
        [sys.executable, str(MIGRATE), str(db)], check=True, capture_output=True
    )
    after = connect(db).execute("SELECT COUNT(*) FROM screen").fetchone()[0]
    assert before == after


def _audit(con: sqlite3.Connection, **kw) -> int:
    row = {
        "match_id": None,
        "screen_id": screen_id(con, "solstice_summary"),
        "side": "blue",
        "slot": 1,
        "image_slug": "atalanta",
        "image_art_ref": "Atalanta",
        "image_score": 0.87,
        "image_margin": 0.36,
        "ocr_slug": "atalanta",
        "agreed": 1,
        "frame_path": None,
        "created_at": "2026-07-26T00:00:00",
    }
    row.update(kw)
    cols = ",".join(row)
    marks = ",".join("?" * len(row))
    cur = con.execute(
        f"INSERT INTO identification_audit({cols}) VALUES({marks})", tuple(row.values())
    )
    return int(cur.lastrowid)


def _transform(con: sqlite3.Connection, audit_id, hero="atalanta", scr=None, cb="longpress_ocr"):
    con.execute(
        "INSERT INTO hero_screen_transform"
        "(screen_id,hero_slug,art_ref,scale,score,margin,confirmed_by,audit_id,verified_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            scr if scr is not None else screen_id(con, "solstice_summary"),
            hero, "Atalanta", 0.55, 0.87, 0.36, cb, audit_id, "2026-07-26T00:00:00",
        ),
    )


def test_agreed_cannot_contradict_the_slugs(db: Path) -> None:
    """agreed=1 must MEAN the two channels matched, or it launders a contradiction."""
    con = connect(db)
    with pytest.raises(sqlite3.IntegrityError):
        _audit(con, image_slug="igor", ocr_slug="thoran", agreed=1)


def test_transform_requires_confirming_evidence(db: Path) -> None:
    con = connect(db)
    good = _audit(con)
    _transform(con, good)  # must succeed

    disagreed = _audit(con, image_slug="igor", ocr_slug=None, agreed=0)
    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, disagreed, hero="igor")

    other_screen = _audit(con, screen_id=screen_id(con, "spectate_prematch"),
                          image_slug="pippa", ocr_slug="pippa")
    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, other_screen, hero="pippa")  # audit is for a different screen

    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, good, hero="thoran")  # audit names a different hero

    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, good, hero="lyca", cb="self")  # self-confirmation banned

    with pytest.raises(sqlite3.IntegrityError):
        _transform(con, None, hero="lyca")  # no evidence at all


def test_transform_update_also_requires_evidence(db: Path) -> None:
    """Re-tuning is an UPDATE; a BEFORE INSERT trigger alone would leak here."""
    con = connect(db)
    good = _audit(con)
    _transform(con, good)
    bad = _audit(con, image_slug="igor", ocr_slug=None, agreed=0)

    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE hero_screen_transform SET audit_id=?", (bad,))

    con.execute("UPDATE hero_screen_transform SET scale=0.62")  # legitimate re-tune
    assert con.execute("SELECT scale FROM hero_screen_transform").fetchone()[0] == 0.62


def test_deleting_a_match_does_not_break_transform_evidence(db: Path) -> None:
    """CASCADE here would abort the delete once a transform references the audit row."""
    con = connect(db)
    con.execute(
        "INSERT INTO match(source,captured_at) VALUES('spectate_summary','2026-07-26')"
    )
    match_id = int(con.execute("SELECT id FROM match").fetchone()[0])
    good = _audit(con, match_id=match_id)
    _transform(con, good)

    con.execute("DELETE FROM match WHERE id=?", (match_id,))

    assert con.execute("SELECT match_id FROM identification_audit").fetchone()[0] is None
    assert con.execute("SELECT COUNT(*) FROM hero_screen_transform").fetchone()[0] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_schema_v3.py -v`

Expected: FAIL - `no such table: screen`.

- [ ] **Step 3: Add the tables and triggers to `schema.sql`**

Append to `data/solstice_clash/schema.sql`. Order matters: `identification_audit` must precede `hero_screen_transform`, which has an FK to it.

```sql
-- ---------------------------------------------------------------------------
-- Schema v3: screen registry, identification audit trail, learned transforms.
-- ---------------------------------------------------------------------------

-- Named screens. cell_registry.cell_type stays as-is; this table adds the
-- screen-level crop defaults that a per-hero transform can override.
CREATE TABLE IF NOT EXISTS screen(
  id              INTEGER PRIMARY KEY,
  slug            TEXT NOT NULL UNIQUE,
  description     TEXT NOT NULL,
  base_resolution TEXT NOT NULL,
  crop_half_w     INTEGER,
  crop_top        INTEGER,
  crop_bottom     INTEGER
);

-- One row per identified cell, agreements included. Agreements are what make the
-- false-positive RATE computable: recording only misfires gives a numerator with no
-- denominator.
--
-- match_id is ON DELETE SET NULL, NOT CASCADE. hero_screen_transform.audit_id is
-- NOT NULL, so cascading a match delete into its audit rows makes SQLite abort the
-- delete with FOREIGN KEY constraint failed as soon as any transform has been learned
-- from that match. Audit rows are evidence about identification, not about the match,
-- so they outlive it.
CREATE TABLE IF NOT EXISTS identification_audit(
  id            INTEGER PRIMARY KEY,
  match_id      INTEGER REFERENCES match(id) ON DELETE SET NULL,
  screen_id     INTEGER NOT NULL REFERENCES screen(id),
  side          TEXT NOT NULL,
  slot          INTEGER NOT NULL,
  image_slug    TEXT,
  image_art_ref TEXT,
  image_score   REAL NOT NULL,
  image_margin  REAL NOT NULL,
  ocr_slug      TEXT,
  agreed        INTEGER NOT NULL,
  frame_path    TEXT,
  created_at    TEXT NOT NULL,
  CHECK(agreed IN (0, 1)),
  -- 'agreed' must MEAN what it says. Without this a row could claim agreed=1 while the
  -- two channels disagree, and the trigger below would accept it as confirmation.
  CHECK(agreed = 0 OR (image_slug IS NOT NULL
                       AND ocr_slug IS NOT NULL
                       AND image_slug = ocr_slug))
);

CREATE TABLE IF NOT EXISTS hero_screen_transform(
  id            INTEGER PRIMARY KEY,
  screen_id     INTEGER NOT NULL REFERENCES screen(id),
  hero_slug     TEXT NOT NULL REFERENCES hero(slug),
  art_ref       TEXT NOT NULL,
  scale         REAL NOT NULL,
  -- NULL means "use the screen default". Measured: the optimum crop differs per hero.
  crop_half_w   INTEGER,
  crop_top      INTEGER,
  crop_bottom   INTEGER,
  score         REAL NOT NULL,
  margin        REAL NOT NULL,
  confirmed_by  TEXT NOT NULL CHECK(confirmed_by IN ('longpress_ocr')),
  audit_id      INTEGER NOT NULL REFERENCES identification_audit(id),
  verified_at   TEXT NOT NULL,
  UNIQUE(screen_id, hero_slug, art_ref)
);

-- NOT NULL only proves an audit row exists. It cannot prove the row agrees, names this
-- hero, or came from this screen, and SQLite CHECK cannot reference another table.
CREATE TRIGGER IF NOT EXISTS hero_screen_transform_confirm_insert
BEFORE INSERT ON hero_screen_transform
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'transform requires an agreeing OCR-confirmed audit row for the same hero and screen')
  WHERE NOT EXISTS (
    SELECT 1 FROM identification_audit a
    WHERE a.id = NEW.audit_id AND a.agreed = 1
      AND a.ocr_slug = NEW.hero_slug AND a.screen_id = NEW.screen_id
  );
END;

-- Re-tuning is an UPDATE. A BEFORE INSERT trigger alone would hold on the first write
-- and leak on every subsequent one.
CREATE TRIGGER IF NOT EXISTS hero_screen_transform_confirm_update
BEFORE UPDATE ON hero_screen_transform
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'transform update requires an agreeing OCR-confirmed audit row for the same hero and screen')
  WHERE NOT EXISTS (
    SELECT 1 FROM identification_audit a
    WHERE a.id = NEW.audit_id AND a.agreed = 1
      AND a.ocr_slug = NEW.hero_slug AND a.screen_id = NEW.screen_id
  );
END;
```

Also add the five new columns to the `match_hero` CREATE (for fresh databases), inserting them immediately before `UNIQUE(match_id, side, slot)`:

```sql
  stat_sword      INTEGER,               -- summary column 1, sword icon
  stat_heart      INTEGER,               -- summary column 2, heart icon
  stat_shield     INTEGER,               -- summary column 3, shield icon
  power           INTEGER,               -- from the long-press popup only
  identified_by   TEXT,                  -- 'image' | 'longpress_ocr'
```

- [ ] **Step 4: Update `migrate.py`**

Set `SCHEMA_VERSION = 3`. Add to `ADD_COLUMNS` (existing databases; `match` needs nothing - it already has theme/players/ratings/ranks):

```python
    ("match_hero", "stat_sword", "INTEGER"),
    ("match_hero", "stat_heart", "INTEGER"),
    ("match_hero", "stat_shield", "INTEGER"),
    ("match_hero", "power", "INTEGER"),
    ("match_hero", "identified_by", "TEXT"),
```

Add a screen seed alongside `DEFAULT_CONFIG`, and insert it in the same place `DEFAULT_CONFIG` is applied:

```python
# Screens Mode A reads. crop_* are the screen-level defaults a per-hero transform may
# override. Summary values are measured; the two spectate screens are seeded without a
# crop until Task 6 measures them.
DEFAULT_SCREENS = [
    ("solstice_summary", "Post-match summary: both comps, winner, per-hero stats",
     "1080x1920", 26, 18, 30),
    ("spectate_draft_picks", "Spectate draft: the six pick slots in the top strip",
     "1080x1920", None, None, None),
    ("spectate_prematch", "Spectate prematch: six locked cards, three per side",
     "1080x1920", None, None, None),
]
```

applied with:

```python
    for slug, description, base_resolution, half_w, top, bottom in DEFAULT_SCREENS:
        con.execute(
            "INSERT OR IGNORE INTO screen"
            "(slug,description,base_resolution,crop_half_w,crop_top,crop_bottom)"
            " VALUES(?,?,?,?,?,?)",
            (slug, description, base_resolution, half_w, top, bottom),
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/ -v`

Expected: all pass, including the pre-existing 46.

- [ ] **Step 6: Migrate the shipped database and confirm it is non-destructive**

```bash
cd /mnt/docs/adbautoplayer
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/solstice_clash/heroes.sqlite')
print({t: c.execute(f'select count(*) from {t}').fetchone()[0]
       for t in ('hero','hero_skin','solstice_roster','cell_registry','art_transform')})"
/mnt/docs/adbautoplayer/.venv/bin/python data/solstice_clash/migrate.py
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/solstice_clash/heroes.sqlite')
print({t: c.execute(f'select count(*) from {t}').fetchone()[0]
       for t in ('hero','hero_skin','solstice_roster','cell_registry','art_transform','screen')})"
```

Expected: hero 153, hero_skin 173, solstice_roster 118, cell_registry 32, art_transform 24 **unchanged**, screen 3.

- [ ] **Step 7: Commit**

```bash
git add data/solstice_clash/schema.sql data/solstice_clash/migrate.py data/solstice_clash/heroes.sqlite src-tauri/src-python/tests/games/afk_journey/services/solstice/test_schema_v3.py
git commit -m "feat(solstice): schema v3 - screen registry, audit trail, gated transforms

The confirmation gate is enforced by the database rather than by callers:
a CHECK so agreed=1 cannot contradict the slugs, and insert+update triggers
so a transform can only ever be learned from an audit row that agrees, names
the same hero and comes from the same screen."
```

---

### Task 2: Store - audit rows and gated transform learning

**Files:**
- Modify: `SVC/store.py`
- Test: `TST/test_store.py` (extend)

**Interfaces:**
- Consumes: schema v3 from Task 1.
- Produces:
  - `HeroSlot` gains `stat_sword: int | None = None`, `stat_heart: int | None = None`, `stat_shield: int | None = None`, `power: int | None = None`, `identified_by: str | None = None`, and `_HERO_COLS` gains the same five names
  - `AuditRow` frozen dataclass: `screen_slug: str`, `side: str`, `slot: int`, `image_slug: str | None`, `image_art_ref: str | None`, `image_score: float`, `image_margin: float`, `ocr_slug: str | None`, `frame_path: str | None`, `match_id: int | None = None`
  - `MatchStore.record_audit(row: AuditRow) -> int` (returns audit id; computes `agreed` itself)
  - `MatchStore.learn_transform(audit_id: int, screen_slug: str, hero_slug: str, art_ref: str, scale: float, score: float, margin: float, crop: tuple[int, int, int] | None = None) -> None`
  - `MatchStore.transform_for(screen_slug: str, hero_slug: str) -> dict | None`
  - `MatchStore.audit_agreement_rate(screen_slug: str) -> tuple[int, int]` returning `(agreed, total)`
  - `_SOURCES` gains `spectate_summary`

- [ ] **Step 1: Write the failing tests**

Append to `TST/test_store.py`. Every one of these uses the existing `tmp_db` fixture
(`test_store.py:22-26`), which copies the database to a temp path. Using `db_path` would
write audit, transform, match and hero rows into the **shipped** `heroes.sqlite` on every
test run - the existing store tests avoid it for exactly that reason.

Tests:

```python
def test_record_audit_computes_agreement(tmp_db):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow, MatchStore,
    )
    store = MatchStore(tmp_db)
    same = store.record_audit(AuditRow(
        screen_slug="solstice_summary", side="blue", slot=1,
        image_slug="atalanta", image_art_ref="Atalanta",
        image_score=0.87, image_margin=0.36, ocr_slug="atalanta", frame_path=None,
    ))
    differ = store.record_audit(AuditRow(
        screen_slug="solstice_summary", side="red", slot=1,
        image_slug="igor", image_art_ref="Igor",
        image_score=0.72, image_margin=0.11, ocr_slug="thoran", frame_path="/x.png",
    ))
    assert same != differ
    agreed, total = store.audit_agreement_rate("solstice_summary")
    assert (agreed, total) == (1, 2)


def test_learn_transform_requires_agreement(tmp_db):
    """A disagreeing audit row must not be usable as confirmation."""
    import pytest
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow, MatchStore,
    )
    store = MatchStore(tmp_db)
    bad = store.record_audit(AuditRow(
        screen_slug="solstice_summary", side="blue", slot=1,
        image_slug="igor", image_art_ref="Igor",
        image_score=0.72, image_margin=0.11, ocr_slug="thoran", frame_path=None,
    ))
    with pytest.raises(ValueError):
        store.learn_transform(bad, "solstice_summary", "igor", "Igor", 0.55, 0.72, 0.11)


def test_learn_transform_roundtrip_and_retune(tmp_db):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow, MatchStore,
    )
    store = MatchStore(tmp_db)
    good = store.record_audit(AuditRow(
        screen_slug="solstice_summary", side="blue", slot=1,
        image_slug="atalanta", image_art_ref="Atalanta",
        image_score=0.87, image_margin=0.36, ocr_slug="atalanta", frame_path=None,
    ))
    store.learn_transform(good, "solstice_summary", "atalanta", "Atalanta",
                          0.55, 0.87, 0.36, crop=(22, 18, 26))
    got = store.transform_for("solstice_summary", "atalanta")
    assert got["scale"] == 0.55 and got["crop_half_w"] == 22

    store.learn_transform(good, "solstice_summary", "atalanta", "Atalanta",
                          0.58, 0.89, 0.40, crop=(24, 16, 28))
    got = store.transform_for("solstice_summary", "atalanta")
    assert got["scale"] == 0.58 and got["margin"] == 0.40


def test_unknown_source_is_rejected(tmp_db):
    import pytest
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        MatchRecord, MatchStore,
    )
    store = MatchStore(tmp_db)
    store.record_match(MatchRecord(source="spectate_summary", captured_at="2026-07-26"))
    with pytest.raises(ValueError):
        store.record_match(MatchRecord(source="history", captured_at="2026-07-26"))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_store.py -v -k "audit or transform or unknown_source"`

Expected: FAIL - `cannot import name 'AuditRow'`.

- [ ] **Step 3: Implement in `SVC/store.py`**

Add `spectate_summary` to `_SOURCES`:

```python
_SOURCES = frozenset({"compete", "spectate", "spectate_summary"})
```

Extend `HeroSlot` with the summary's per-hero numbers. Task 1 added the columns; without
this they would stay NULL forever and the mode would record matches with no performance
data, which is half its stated output:

```python
    # From the post-match summary. Named for the column ICONS (sword/heart/shield), not
    # for a guess at their meaning - the shield column's semantics are unconfirmed.
    stat_sword: int | None = None
    stat_heart: int | None = None
    stat_shield: int | None = None
    power: int | None = None        # long-press popup only
    identified_by: str | None = None  # 'image' | 'longpress_ocr'
```

and add the same five names to the end of `_HERO_COLS`, which is what builds the INSERT:

```python
        "stat_sword",
        "stat_heart",
        "stat_shield",
        "power",
        "identified_by",
```

Add a test proving they round-trip, since a column that is written but never read back is
indistinguishable from one that was silently dropped:

```python
def test_hero_stats_round_trip(tmp_db):
    from adb_auto_player.games.afk_journey.services.solstice.store import (
        HeroSlot, MatchRecord, MatchStore,
    )
    store = MatchStore(tmp_db)
    match_id = store.record_match(
        MatchRecord(source="spectate_summary", captured_at="2026-07-26")
    )
    store.record_heroes(match_id, [
        HeroSlot(side="blue", slot=1, hero_slug="atalanta", art_ref="Atalanta",
                 status="identified", score=0.87, margin=0.36,
                 stat_sword=699_000, stat_heart=0, stat_shield=2_924_000,
                 power=490_000, identified_by="longpress_ocr"),
    ])
    got = store.heroes_for(match_id)[0]
    assert got.stat_sword == 699_000
    assert got.stat_shield == 2_924_000
    assert got.identified_by == "longpress_ocr"
```

Then add the audit dataclass next to the other record types:

```python
@dataclass(frozen=True)
class AuditRow:
    """One identification, recorded whether or not the two channels agreed.

    Agreements are recorded too. Logging only misfires yields a numerator with no
    denominator: three errors means nothing without knowing if it was three in fifty or
    three in five thousand.
    """

    screen_slug: str
    side: str
    slot: int
    image_slug: str | None
    image_art_ref: str | None
    image_score: float
    image_margin: float
    ocr_slug: str | None
    frame_path: str | None
    match_id: int | None = None
```

Add the methods to `MatchStore`:

```python
    def _screen_id(self, con: sqlite3.Connection, slug: str) -> int:
        row = con.execute("SELECT id FROM screen WHERE slug=?", (slug,)).fetchone()
        if row is None:
            raise ValueError(f"unknown screen slug: {slug!r}")
        return int(row[0])

    def record_audit(self, row: AuditRow) -> int:
        """Persist one identification comparison and return its id."""
        self._check(row.side in _SIDES, f"invalid side: {row.side!r}")
        # agreed is DERIVED, never taken from the caller: the schema CHECK requires it to
        # be consistent with the slugs, and computing it here keeps that impossible to
        # get wrong at a call site.
        agreed = int(
            row.image_slug is not None
            and row.ocr_slug is not None
            and row.image_slug == row.ocr_slug
        )
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO identification_audit"
                "(match_id,screen_id,side,slot,image_slug,image_art_ref,image_score,"
                " image_margin,ocr_slug,agreed,frame_path,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.match_id, self._screen_id(con, row.screen_slug), row.side,
                    row.slot, row.image_slug, row.image_art_ref, row.image_score,
                    row.image_margin, row.ocr_slug, agreed, row.frame_path,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid or 0)

    def learn_transform(
        self,
        audit_id: int,
        screen_slug: str,
        hero_slug: str,
        art_ref: str,
        scale: float,
        score: float,
        margin: float,
        crop: tuple[int, int, int] | None = None,
    ) -> None:
        """Store tuned parameters, upserting on (screen, hero, art).

        The database triggers reject unconfirmed evidence; this raises ValueError rather
        than sqlite3.IntegrityError so callers get one exception type to handle.
        """
        half_w, top, bottom = crop if crop is not None else (None, None, None)
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO hero_screen_transform"
                    "(screen_id,hero_slug,art_ref,scale,crop_half_w,crop_top,"
                    " crop_bottom,score,margin,confirmed_by,audit_id,verified_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'longpress_ocr',?,?)"
                    " ON CONFLICT(screen_id,hero_slug,art_ref) DO UPDATE SET"
                    "  scale=excluded.scale, crop_half_w=excluded.crop_half_w,"
                    "  crop_top=excluded.crop_top, crop_bottom=excluded.crop_bottom,"
                    "  score=excluded.score, margin=excluded.margin,"
                    "  audit_id=excluded.audit_id, verified_at=excluded.verified_at",
                    (
                        self._screen_id(con, screen_slug), hero_slug, art_ref, scale,
                        half_w, top, bottom, score, margin, audit_id,
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"refusing to learn a transform for {hero_slug!r} on {screen_slug!r}: "
                f"audit {audit_id} does not confirm it ({exc})"
            ) from exc

    def transform_for(self, screen_slug: str, hero_slug: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT scale,crop_half_w,crop_top,crop_bottom,score,margin"
                " FROM hero_screen_transform t JOIN screen s ON s.id=t.screen_id"
                " WHERE s.slug=? AND t.hero_slug=? ORDER BY t.margin DESC LIMIT 1",
                (screen_slug, hero_slug),
            ).fetchone()
        if row is None:
            return None
        keys = ("scale", "crop_half_w", "crop_top", "crop_bottom", "score", "margin")
        return dict(zip(keys, row, strict=True))

    def audit_agreement_rate(self, screen_slug: str) -> tuple[int, int]:
        """(agreed, total) for one screen - the false-positive rate's two halves."""
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(a.agreed),0), COUNT(*)"
                " FROM identification_audit a JOIN screen s ON s.id=a.screen_id"
                " WHERE s.slug=?",
                (screen_slug,),
            ).fetchone()
        return int(row[0]), int(row[1])
```

Add `from datetime import UTC, datetime` to the imports if not present.

- [ ] **Step 4: Run to verify pass**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/ -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/store.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_store.py
git commit -m "feat(solstice): audit rows and gated transform learning in the store

record_audit derives 'agreed' itself rather than trusting a call site, and
learn_transform converts the database trigger's rejection into a ValueError
so callers have one exception type."
```

---

### Task 3: Fixture frames

**Files:**
- Create: `TST/data/summary_01.png`, `TST/data/summary_02.png`, `TST/data/longpress_ally1.png`, `TST/data/spectate_draft.png`, `TST/data/spectate_prematch.png`

**Interfaces:**
- Consumes: nothing.
- Produces: fixture frames used by Tasks 4, 5 and 6.

- [ ] **Step 1: Copy the frames**

These are native 1080x1920 device captures. Geometry and match-score tests are meaningless at any other resolution, so they are committed at full size (~12MB total). That is deliberate.

```bash
cd /mnt/docs/adbautoplayer
D=src-tauri/src-python/tests/games/afk_journey/services/solstice/data
cp /mnt/vault/solstice/summary/summary_01.png        $D/summary_01.png
cp /mnt/vault/solstice/summary/summary_02.png        $D/summary_02.png
cp /mnt/vault/solstice/summary/longpress_ally1.png   $D/longpress_ally1.png
cp /mnt/vault/solstice/live/match01/raw/000039317.png $D/spectate_draft.png
cp /mnt/vault/solstice/live/match01/raw/000104002.png $D/spectate_prematch.png
```

- [ ] **Step 2: Verify every frame is 1080x1920**

```bash
cd /mnt/docs/adbautoplayer/src-tauri/src-python
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import cv2, glob
for f in sorted(glob.glob('tests/games/afk_journey/services/solstice/data/*.png')):
    print(cv2.imread(f).shape, f)"
```

Expected: every line reads `(1920, 1080, 3)`.

- [ ] **Step 3: Commit**

```bash
git add src-tauri/src-python/tests/games/afk_journey/services/solstice/data/
git commit -m "test(solstice): fixture frames for summary, long-press and spectate screens

Committed at native 1080x1920 - geometry and match-score assertions are
meaningless at any other resolution."
```

---

### Task 4: Naming - resolve a hero from OCR text

**Files:**
- Create: `SVC/naming.py`
- Test: `TST/test_naming.py` (create)

**Interfaces:**
- Consumes: `SolsticeConfig.heroes() -> dict[str, HeroRow]` (each `HeroRow` has `.slug`, `.name`).
- Produces: `resolve_hero_name(texts: list[str], cfg: SolsticeConfig, threshold: float = 0.80) -> str | None` returning a hero slug.

- [ ] **Step 1: Write the failing test**

Create `TST/test_naming.py`:

```python
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


def test_reads_the_name_from_a_real_longpress_frame(cfg, ocr_backend, frames):
    from adb_auto_player.models import ConfidenceValue

    frame = cv2.imread(str(frames["longpress_ally1"]))
    blocks = ocr_backend.detect_text_blocks(frame, ConfidenceValue(0.5))
    assert resolve_hero_name([b.text for b in blocks], cfg) == "atalanta"
```

Add to `TST/conftest.py`:

```python
@pytest.fixture(scope="session")
def cfg(db_path):
    from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig

    return SolsticeConfig.load(db_path)


@pytest.fixture(scope="session")
def ocr_backend():
    """RapidOCR loads ONNX models on first use, so build it once per session."""
    from adb_auto_player.ocr import RapidOCRBackend

    return RapidOCRBackend()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_naming.py -v`

Expected: FAIL - `No module named ...naming`.

- [ ] **Step 3: Implement `SVC/naming.py`**

```python
"""Resolve a hero slug from OCR text.

This is the ground-truth channel for the whole mode: the long-press popup names the hero
outright, and that name is what confirms or refutes image matching. A wrong answer here is
worse than no answer, because it would be recorded as truth - so anything ambiguous
resolves to None.
"""

from __future__ import annotations

from adb_auto_player.models import ConfidenceValue
from adb_auto_player.util import StringHelper

from .config import SolsticeConfig

# Below this similarity a block is not considered a name at all.
DEFAULT_THRESHOLD = 0.80
# A name must be at least this long to be matched, so that a short slug cannot match
# inside an unrelated word.
MIN_NAME_LENGTH = 4


def resolve_hero_name(
    texts: list[str],
    cfg: SolsticeConfig,
    threshold: float = DEFAULT_THRESHOLD,
) -> str | None:
    """Return the hero slug named in `texts`, or None if that is not unambiguous.

    Args:
        texts: OCR block strings from the popup region ONLY. fuzzy_substring_match is a
            SUBSTRING match, so passing whole-frame OCR would let a short hero name match
            inside unrelated text.
        cfg: loaded config, providing the hero name -> slug map.
        threshold: minimum similarity, as a ratio.

    Returns:
        The hero slug, or None when nothing matched or two heroes matched equally well.
    """
    heroes = cfg.heroes()
    confidence = ConfidenceValue(f"{int(round(threshold * 100))}%")

    hits: set[str] = set()
    for text in texts:
        cleaned = text.strip()
        if len(cleaned) < MIN_NAME_LENGTH:
            continue
        for slug, row in heroes.items():
            if len(row.name) < MIN_NAME_LENGTH:
                continue
            if StringHelper.fuzzy_substring_match(cleaned, row.name, confidence):
                hits.add(slug)

    # fuzzy_substring_match returns a bool, so it cannot rank. Two candidates means the
    # text was too degraded to be ground truth, and ground truth is the entire point.
    if len(hits) != 1:
        return None
    return hits.pop()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_naming.py -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/naming.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_naming.py src-tauri/src-python/tests/games/afk_journey/services/solstice/conftest.py
git commit -m "feat(solstice): resolve hero slugs from OCR text with fuzzy matching

Ambiguity returns None rather than guessing: this is the ground-truth
channel, so a wrong answer would be recorded as truth."
```

---

### Task 5: Summary parser

**Files:**
- Create: `SVC/summary.py`
- Modify: `SVC/config.py` (generalise `scale_chain` to a per-cell-type key)
- Modify: `data/solstice_clash/migrate.py` (seed `summary_hero` cells and `scale_summary_hero`)
- Test: `TST/test_summary.py` (create), `TST/test_config.py` (extend)

**Interfaces:**
- Consumes: `vision.identify_cell`, `IconLibrary`, `SolsticeConfig.cells("summary_hero")`, `resolve_hero_name`.
- Produces:
  - `HeroStats` frozen dataclass: `sword: int | None`, `heart: int | None`, `shield: int | None`
  - `SummaryHero` frozen dataclass: `side: str`, `slot: int`, `slug: str | None`, `art_ref: str | None`, `score: float`, `margin: float`, `stats: HeroStats`
  - `SummaryRead` frozen dataclass: `winner: str | None`, `blue_player: str | None`, `red_player: str | None`, `heroes: list[SummaryHero]`
  - `read_summary(frame, cfg, library, ocr) -> SummaryRead`
  - `parse_stat_number(text: str) -> int | None`

- [ ] **Step 1: Seed the summary cell geometry**

Add to `migrate.py` beside `DEFAULT_SCREENS`, and apply it the same way:

```python
# Measured on summary_01.png at 1080x1920. Card centres: x=90, ally y=476/566/656,
# enemy y=1123/1215/1307. Bounds below are centre +-52, which crops the card cleanly
# with margin; the tuned art crop sits inside this and comes from screen.crop_*.
DEFAULT_SUMMARY_CELLS = [
    ("solstice_summary", "summary_blue_1", "summary_hero", 38, 424, 142, 528, "blue", 1),
    ("solstice_summary", "summary_blue_2", "summary_hero", 38, 514, 142, 618, "blue", 2),
    ("solstice_summary", "summary_blue_3", "summary_hero", 38, 604, 142, 708, "blue", 3),
    ("solstice_summary", "summary_red_1", "summary_hero", 38, 1071, 142, 1175, "red", 1),
    ("solstice_summary", "summary_red_2", "summary_hero", 38, 1163, 142, 1267, "red", 2),
    ("solstice_summary", "summary_red_3", "summary_hero", 38, 1255, 142, 1359, "red", 3),
]
```

applied with:

```python
    for screen, name, cell_type, x0, y0, x1, y1, side, slot in DEFAULT_SUMMARY_CELLS:
        con.execute(
            "INSERT OR IGNORE INTO cell_registry"
            "(screen,cell_name,cell_type,x0,y0,x1,y1,side,slot,base_resolution,verified_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,'1080x1920',datetime('now'))",
            (screen, name, cell_type, x0, y0, x1, y1, side, slot),
        )
```

Summary cards are ~104px, far smaller than draft cards, so they need their own scale chain. But `SolsticeConfig.scale_chain` currently hardcodes which key it reads (`config.py:101`):

```python
key = "scale_draft_card" if cell_type == "draft_card" else "scale_chain"
```

A `scale_summary_hero` key would therefore be seeded and then never read - the summary tests would run against the draft-sized chain and fail. Generalise the lookup first, which also stops the next cell type needing another edit here:

```python
    def scale_chain(self, cell_type: str) -> tuple[float, ...]:
        """Scales to try, in order.

        Fix the SCALE and let matchTemplate find the offset; fixing the offset
        instead dropped one hero from 0.978 to 0.408.

        Looks for a per-cell-type key first, falling back to the shared chain. Cards
        differ enormously between screens - a summary card is ~104px against a draft
        card's ~200px - so one global chain cannot serve both.
        """
        key = f"scale_{cell_type}"
        if key not in self._tunables:
            key = "scale_chain"
        return tuple(float(x) for x in self._tunables[key].split(","))
```

`scale_draft_card` already follows that naming, so existing behaviour is preserved exactly.

Note the stored format: the value is an **explicit comma-separated list of scales**, not a
range specification. `scale_chain` is `1.01,0.95,1.08` and `scale_draft_card` is
`1.19,1.10,1.30`, parsed by `tuple(float(x) for x in value.split(","))`. Writing
`"0.30,0.90,0.02"` would mean three scales - 0.30, 0.90 and 0.02 - not a sweep.

The chain below is short because it was measured rather than guessed: on `summary_01.png`
all six cards peak at 0.47-0.48 (atalanta/igor/indris/baelran/pippa at 0.48, solise at
0.47). Five scales cover that with margin, and a shorter chain is proportionally faster -
6 cells x 173 icons x 5 scales instead of x 31.

Then seed the new chain in `DEFAULT_CONFIG`:

```python
    ("scale_summary_hero", "0.48,0.47,0.49,0.46,0.50",
     "measured: all six summary cards peak at 0.47-0.48"),
```

Add a test to `TST/test_config.py` proving both paths:

```python
def test_scale_chain_is_per_cell_type(cfg):
    assert cfg.scale_chain("draft_card") != cfg.scale_chain("summary_hero")
    # an unregistered cell type falls back rather than raising
    assert cfg.scale_chain("no_such_cell_type") == cfg.scale_chain("locked_pick")
```

- [ ] **Step 2: Write the failing test**

Create `TST/test_summary.py`:

```python
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
```

Add to `TST/conftest.py`:

```python
@pytest.fixture(scope="session")
def library(cfg):
    from pathlib import Path

    from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary

    icon_dir = Path("/mnt/vault/solstice/gamefiles/ui/icon")
    if not icon_dir.is_dir():
        pytest.skip(f"icon library not available at {icon_dir}")
    return IconLibrary.build(cfg, icon_dir)
```

- [ ] **Step 3: Apply the migration, then run to verify failure**

The seeds above only reach the database when `migrate.py` runs. The tests load the shipped
`heroes.sqlite` through `cfg`, which today has **zero** `summary_hero` cells and no
`scale_summary_hero`, so without this step `read_summary()` would iterate an empty cell
list and the tests could not pass no matter how correct the parser is.

```bash
cd /mnt/docs/adbautoplayer
/mnt/docs/adbautoplayer/.venv/bin/python data/solstice_clash/migrate.py
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/solstice_clash/heroes.sqlite')
print('summary cells:', c.execute(
    \"select count(*) from cell_registry where cell_type='summary_hero'\").fetchone()[0])
print('scale key:', c.execute(
    \"select value from library_config where key='scale_summary_hero'\").fetchone())"
```

Expected: `summary cells: 6` and the five-scale chain.

Then run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_summary.py -v`

Expected: FAIL - `No module named ...summary`.

- [ ] **Step 4: Implement `SVC/summary.py`**

```python
"""Reading the post-match summary screen.

This screen is the whole reason Mode A exists: it shows both comps, the winner and
per-hero stats, with no time pressure at all - it waits for input. Everything here is
pure, so it can be tested against saved frames with no device.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np

from adb_auto_player.models import ConfidenceValue
from adb_auto_player.ocr._backend import OCRBackend

from .config import SolsticeConfig
from .icons import IconLibrary
from .vision import extract_cell, identify_cell

CELL_TYPE = "summary_hero"
SCREEN_SLUG = "solstice_summary"

# The Defeat/Victory header. OCR returns it as ONE merged block ('DefeatVictory'), so the
# winner cannot be read from the string - it has to come from WHICH HALF says Victory.
_HEADER_BAND = (200, 320)
_HEADER_SPLIT_X = 540
_HEADER_LEFT = (60, 470)
_HEADER_RIGHT = (610, 1020)

# Stat columns, measured on summary_01.png. Rows share the hero cell's vertical centre.
_STAT_COLUMNS = {
    "sword": (120, 310),
    "heart": (330, 520),
    "shield": (540, 730),
}
_STAT_HALF_HEIGHT = 26

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
    winner: str | None  # 'blue' | 'red' | None when the header could not be read
    blue_player: str | None
    red_player: str | None
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


def _read_winner(frame: np.ndarray, ocr: OCRBackend) -> str | None:
    """Which half of the header carries 'Victory'.

    Full-frame OCR merges the two words into 'DefeatVictory', so each half is OCR'd
    separately. A naive substring test on the merged string would be a coin flip.
    """
    y0, y1 = _HEADER_BAND
    halves = {
        "blue": frame[y0:y1, _HEADER_LEFT[0] : _HEADER_LEFT[1]],
        "red": frame[y0:y1, _HEADER_RIGHT[0] : _HEADER_RIGHT[1]],
    }
    for side, crop in halves.items():
        text = " ".join(
            b.text for b in ocr.detect_text_blocks(crop, ConfidenceValue(0.4))
        ).lower()
        if "victory" in text:
            return side
        if "defeat" in text:
            return "red" if side == "blue" else "blue"
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
    blue_player, red_player = _read_players(frame, ocr)

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
    return SummaryRead(winner, blue_player, red_player, heroes)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_summary.py -v`

Expected: all pass. If a stat column or header band is off, adjust the constants against the fixture rather than loosening the assertions.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/summary.py \
        src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/config.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_summary.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_config.py \
        data/solstice_clash/migrate.py data/solstice_clash/heroes.sqlite
git commit -m "feat(solstice): summary parser - winner, both comps, per-hero stats

The winner is read positionally: OCR merges the header into 'DefeatVictory',
so which half says Victory is the signal, not the string. Stats are named
for their icons because the shield column's meaning is unconfirmed."
```

---

### Task 6: Tuning search

**Files:**
- Create: `SVC/tuning.py`
- Test: `TST/test_tuning.py` (create)

**Interfaces:**
- Consumes: `IconLibrary`, `vision._best_over_scales`.
- Produces:
  - `TuneResult` frozen dataclass: `scale: float`, `crop_half_w: int`, `crop_top: int`, `crop_bottom: int`, `score: float`, `margin: float`
  - `tune_cell(frame, centre, truth_slug, library, cfg, scales, crops) -> TuneResult | None`
  - `DEFAULT_CROPS: tuple[tuple[int, int, int], ...]`, `DEFAULT_SCALES: tuple[float, ...]`

- [ ] **Step 1: Write the failing test**

Create `TST/test_tuning.py`:

```python
"""Crop/scale tuning against a CONFIRMED identity.

Measured headroom on the three weakest summary cards, tuning crop alone:
  solise  0.781 -> 0.866 (margin 0.244) at hw=22 top=18 bot=26
  baelran 0.798 -> 0.844 (margin 0.323) at hw=24 top=14 bot=26
  indris  0.876 -> 0.905 (margin 0.189) at hw=22 top=14 bot=32
"""

import cv2

from adb_auto_player.games.afk_journey.services.solstice.tuning import tune_cell

SOLISE_CENTRE = (90, 1307)


def test_tuning_improves_the_weakest_card(cfg, library, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    result = tune_cell(gray, SOLISE_CENTRE, "solise", library, cfg)

    assert result is not None
    assert result.score >= 0.80, f"expected >=0.80, got {result.score}"
    assert result.margin >= 0.10


def test_tuning_returns_none_when_the_truth_never_wins(cfg, library, frames):
    """If the named hero is not what is on screen, tuning must refuse rather than
    force the wrong answer to score better."""
    frame = cv2.imread(str(frames["summary_01"]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    assert tune_cell(gray, SOLISE_CENTRE, "thoran", library, cfg) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_tuning.py -v`

Expected: FAIL - `No module named ...tuning`.

- [ ] **Step 3: Implement `SVC/tuning.py`**

```python
"""Searching for the crop and scale that identify a hero most confidently.

Only ever called with an identity that a long-press OCR confirmed. Tuning toward an
UNCONFIRMED identity would make a wrong answer score better, potentially pushing it past
the accept threshold and suppressing the very check that would have caught it - the
optimiser amplifies whatever it is pointed at, including an error.

It maximises MARGIN, not raw score: every wrong match observed in Phase 1 had a collapsed
margin of 0.01-0.04, and a hero at 0.78 with 0.20 margin is safer than one at 0.85 with
0.05.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import SolsticeConfig
from .icons import IconLibrary
from .vision import _best_over_scales

# matchTemplate searches x/y offsets internally for free, so the offset is NOT tuned -
# fixing it instead of the scale dropped one hero from 0.978 to 0.408 in Phase 1. What is
# worth tuning is which part of the CARD is cut, and the scale.
DEFAULT_CROPS: tuple[tuple[int, int, int], ...] = tuple(
    (half_w, top, bottom)
    for half_w in (22, 24, 26, 28)
    for top in (14, 16, 18, 20)
    for bottom in (26, 28, 30, 32)
)
DEFAULT_SCALES: tuple[float, ...] = tuple(
    round(0.30 + 0.01 * i, 3) for i in range(56)
)


@dataclass(frozen=True)
class TuneResult:
    scale: float
    crop_half_w: int
    crop_top: int
    crop_bottom: int
    score: float
    margin: float


def tune_cell(
    gray: np.ndarray,
    centre: tuple[int, int],
    truth_slug: str,
    library: IconLibrary,
    cfg: SolsticeConfig,
    scales: tuple[float, ...] = DEFAULT_SCALES,
    crops: tuple[tuple[int, int, int], ...] = DEFAULT_CROPS,
) -> TuneResult | None:
    """Find the crop and scale maximising the margin for `truth_slug`.

    Args:
        gray: the full frame, grayscale.
        centre: (x, y) centre of the card.
        truth_slug: the CONFIRMED hero. Never pass an unconfirmed guess.
        library: icon library.
        cfg: loaded config.
        scales: scale chain to search.
        crops: (half_w, top, bottom) insets to search.

    Returns:
        The best parameters where `truth_slug` actually wins, or None if it never does -
        which means the confirmation and the image disagree, and nothing should be learned.
    """
    cx, cy = centre
    entries = library.entries()
    best: TuneResult | None = None

    for half_w, top, bottom in crops:
        cell = gray[cy - top : cy + bottom, cx - half_w : cx + half_w]
        if cell.size == 0:
            continue

        per_slug: dict[str, float] = {}
        for entry in entries:
            score = _best_over_scales(entry.gray, cell, scales)
            if score > per_slug.get(entry.slug, -1.0):
                per_slug[entry.slug] = score

        ranked = sorted(((v, k) for k, v in per_slug.items()), reverse=True)
        if len(ranked) < 2 or ranked[0][1] != truth_slug:
            continue

        score = ranked[0][0]
        margin = score - ranked[1][0]
        if best is None or margin > best.margin:
            scale = _best_scale_for(
                next(e for e in entries if e.slug == truth_slug).gray, cell, scales
            )
            best = TuneResult(scale, half_w, top, bottom, score, margin)

    return best


def _best_scale_for(
    icon: np.ndarray, cell: np.ndarray, scales: tuple[float, ...]
) -> float:
    """The single scale at which this icon matches this cell best.

    Storing it collapses the scale chain from ~56 steps to 1 on later sightings.
    """
    import cv2

    best_score, best_scale = -1.0, scales[0]
    cell_h, cell_w = cell.shape
    for scale in scales:
        width, height = int(icon.shape[1] * scale), int(icon.shape[0] * scale)
        if width < cell_w or height < cell_h:
            continue
        resized = cv2.resize(icon, (width, height))
        score = float(cv2.matchTemplate(resized, cell, cv2.TM_CCOEFF_NORMED).max())
        if score > best_score:
            best_score, best_scale = score, scale
    return best_scale
```

- [ ] **Step 4: Run to verify pass**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_tuning.py -v`

Expected: both pass. This test is slow (a full crop x scale sweep); if it exceeds ~3 minutes, narrow `DEFAULT_CROPS` in the test call rather than weakening the assertion.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/tuning.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_tuning.py
git commit -m "feat(solstice): margin-maximising crop and scale tuning

Returns None when the confirmed hero never wins, so a disagreement can
never be tuned into looking correct."
```

---

### Task 7: Templates and the navigation chain

**Files:**
- Create: `AFKJ/templates/event/solstice_clash/{events_card,event_screen,fortune_picks,spectate_live,result_back,result_chart,summary_back,draft_anchor,prematch_anchor}.png`
- Create: `AFKJ/mixins/solstice_clash.py`
- Modify: `AFKJ/popup_message_handler.py` (register the teleport dialog)
- Test: manual, on device

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: `SolsticeClashMixin` with `_open_spectate() -> tuple[bool, str | None]` (opened, theme) and the registered GUI command `SolsticeClashCollect`.

- [ ] **Step 1: Cut the templates from captured frames**

Every source frame is already on disk. Crop tightly around each control - a template with background bleed matches poorly.

| template | source frame | region |
|---|---|---|
| `events_card.png` | events list frame | the "Solstice Clash" CARD in Ongoing Events - this is what gets TAPPED |
| `event_screen.png` | `/mnt/vault/solstice/live/teleflow/raw/` (Solstice Clash event screen) | the "Solstice Clash" title - only ever WAITED for, never tapped |
| `fortune_picks.png` | same | the Fortune Picks button, bottom left |
| `spectate_live.png` | `/mnt/vault/solstice/live/navflow/raw/` (NPC dialog) | the "Spectate Live" row |
| `result_back.png` | `/mnt/vault/solstice/live/navflow/raw/` (result screen) | the green Back button |
| `result_chart.png` | same | the chart/details icon |
| `summary_back.png` | `/mnt/vault/solstice/summary/summary_01.png` | the back arrow, bottom left |

Verify each cut by matching it back against its source frame at >= 0.95 confidence before moving on. A template that cannot find itself will never find anything.

- [ ] **Step 2: Register the teleport dialog as a PopupMessage**

In `AFKJ/popup_message_handler.py`, add to `misc_messages`:

```python
    PopupMessage(
        # Appears when the character is not near the event NPC. Confirming auto-paths there.
        # The default confirm_button_template ("navigation/confirm.png") is correct here:
        # measured 0.996 against the captured dialog at (799, 1223), which is the green
        # check. navigation/x.png likewise matches the X at 0.999. This dialog uses the
        # STANDARD buttons, so no new template is needed and the handler's preprocessing
        # (which only proceeds after finding navigation/confirm.png or
        # continue_top_right_corner.png - popup_message_handler.py:321) will find it.
        text="Teleport to the Waystone closest to the target",
        # MUST stay False. When True the handler taps the "Don't remind for 7 days"
        # checkbox, which permanently changes the user's game settings.
        has_dont_remind_me=False,
    ),
```

Because the dialog uses the standard confirm button, **no `teleport_confirm.png` template is required** - drop it from the Step 1 template list. Verify the match before relying on it:

```bash
cd /mnt/docs/adbautoplayer/src-tauri/src-python
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import cv2
t=cv2.imread('adb_auto_player/games/afk_journey/templates/navigation/confirm.png')
f=cv2.imread('/mnt/vault/solstice/summary/teleport_dialog.png')
print(cv2.matchTemplate(f,t,cv2.TM_CCOEFF_NORMED).max())"
```

Expected: >= 0.99.

- [ ] **Step 3: Write the mixin's navigation half**

Create `AFKJ/mixins/solstice_clash.py`:

```python
"""AFK Journey Solstice Clash Mixin - Mode A, training and recording.

Spectates matches in a loop and records each outcome from the post-match summary, using
that OCR-confirmed ground truth to measure and tune identification on the draft and
prematch screens for Modes B and C.
"""

import logging
from abc import ABC
from time import sleep

from adb_auto_player.decorators import register_command
from adb_auto_player.games.afk_journey.base import AFKJourneyBase
from adb_auto_player.games.afk_journey.gui_category import AFKJCategory
from adb_auto_player.models.decorators import GUIMetadata
from adb_auto_player.models.geometry import Point

# Measured on device 2026-07-26. The far branch (teleport plus auto-path) took ~12s, so
# 30s gives roughly 2.5x headroom on the only far position we have sampled.
NPC_DIALOG_TIMEOUT = 30.0
# Combat runs for minutes. wait_for_template defaults to template_timeout (10s), which
# would raise GameTimeoutError on every normal match, so this is always passed explicitly.
MATCH_TIMEOUT = 600.0
RESULT_POLL_DELAY = 2.0


class SolsticeClashMixin(AFKJourneyBase, ABC):
    """Solstice Clash data collection."""

    @register_command(
        name="SolsticeClashCollect",
        gui=GUIMetadata(
            label="Collect Solstice Clash Data",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Spectate Solstice Clash matches and record outcomes for analysis",
        ),
    )
    def collect_solstice_clash(self) -> None:
        """Spectate matches in a loop, recording each one."""
        # Screencap, not the H264 stream: streaming degrades OCR of stylised text and OCR
        # is this mode's ground truth.
        self.start_up(device_streaming=False)
        self.navigate_to_world()
        logging.info("Solstice Clash collection starting")

    def _open_spectate(self) -> tuple[bool, str | None]:
        """Navigate from the overworld to a live spectated match.

        Returns:
            (opened, theme). `theme` is read from the event screen on the way through -
            the only screen in the whole flow that shows it - and is None if unreadable.
        """
        # _navigate_menu_chain taps each template until it DISAPPEARS
        # (_tap_till_template_disappears, up to 3 attempts, then GameActionFailedError).
        # So every entry must be a tappable control that goes away when tapped. The last
        # entry is therefore the Solstice Clash CARD in the events list - NOT the event
        # screen's title, which stays on screen after arrival and would fail the chain.
        self._navigate_menu_chain(
            [
                "navigation/hamburger_menu",
                "dailies/hamburger/events",
                "event/solstice_clash/events_card",
            ]
        )
        # Arrival is confirmed by WAITING for a title that persists, never by tapping it.
        self.wait_for_template(template="event/solstice_clash/event_screen")
        self.wait_for_template(template="event/solstice_clash/fortune_picks")
        # Read the theme HERE, while the event screen is up. It shows "Current Theme:
        # <name>" and "Rotates in <n>"; no later screen in this flow shows either.
        theme = self._read_current_theme()
        self.tap(Point(121, 1606))  # Fortune Picks

        # Three branches converge here: adjacent to the NPC (immediate), a short walk
        # (~4s, NO popup at all), or far away (teleport popup, ~12s with auto-path).
        # The walk branch is why a fixed sleep is wrong - nothing signals it is happening.
        self.handle_popup_messages()
        result = self.wait_for_template(
            template="event/solstice_clash/spectate_live",
            delay=1.0,
            timeout=NPC_DIALOG_TIMEOUT,
            timeout_message="Royal City Show dialog did not appear",
        )
        self.tap(result)
        sleep(3)
        return True, theme
```

- [ ] **Step 4: Verify the mixin registers and the templates resolve**

```bash
cd /mnt/docs/adbautoplayer/src-tauri/src-python
/mnt/docs/adbautoplayer/.venv/bin/python -c "
from pathlib import Path
from adb_auto_player.file_loader import SettingsLoader
SettingsLoader.set_app_config_dir(Path('/mnt/docs/adbautoplayer/src-tauri'))
SettingsLoader.set_resource_dir(Path('adb_auto_player').resolve())
from adb_auto_player.games.afk_journey.mixins.solstice_clash import SolsticeClashMixin
print('mixin imports OK')
import glob
print(sorted(Path(p).name for p in glob.glob(
    'adb_auto_player/games/afk_journey/templates/event/solstice_clash/*.png')))"
```

Expected: imports cleanly, and all seven new templates are listed.

- [ ] **Step 5: Run the full suite**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest -q`

Expected: green, including `test_all_mixins_extended.py`.

- [ ] **Step 6: Commit**

```bash
git add -A src-tauri/src-python/adb_auto_player/games/afk_journey/
git commit -m "feat(solstice): Mode A navigation chain and templates

Three entry branches converge on the NPC dialog: adjacent, a short walk with
no popup, or the teleport prompt. Polling with a 30s ceiling covers all three;
the walk branch is why a fixed sleep would silently fall through."
```

---

### Task 8: The collection loop

**Files:**
- Modify: `AFKJ/mixins/solstice_clash.py`
- Test: manual, on device (Task 9)

**Interfaces:**
- Consumes: everything above.
- Produces: a loop that records matches and writes audit rows.

- [ ] **Step 1: Add the per-match cycle**

Add to `SolsticeClashMixin`:

```python
    def _run_one_match(self) -> bool:
        """Spectate one match and record it. Returns True if a match was recorded."""
        # _open_spectate reads the theme while it is ON the event screen and returns it.
        # The theme cannot be read before that call (we are on the overworld) and cannot
        # be read after it (the summary screen does not show it).
        opened, theme = self._open_spectate()
        if not opened:
            return False

        # Optional training material. If we entered mid-match there is no draft left to
        # capture - that is normal, not an error, and must never block recording.
        draft_frame = self._capture_training_frame(
            "event/solstice_clash/draft_anchor", late=True
        )
        prematch_frame = self._capture_training_frame(
            "event/solstice_clash/prematch_anchor", late=False
        )

        self.wait_for_template(
            template="event/solstice_clash/result_back",
            delay=RESULT_POLL_DELAY,
            timeout=MATCH_TIMEOUT,
            timeout_message="no result screen - abandoning this match",
        )
        chart = self.wait_for_template(template="event/solstice_clash/result_chart")
        self.tap(chart)
        sleep(2)

        self._record_summary(draft_frame, prematch_frame, theme)

        back = self.wait_for_template(template="event/solstice_clash/summary_back")
        self.tap(back)
        sleep(1)
        green_back = self.wait_for_template(template="event/solstice_clash/result_back")
        self.tap(green_back)
        sleep(3)
        return True
```

- [ ] **Step 2: Add the bounded reset policy**

```python
    def _collect_forever(self, max_restarts: int = 3) -> None:
        """Loop until the restart budget is exhausted.

        Recovery has to be bounded or an unattended run spends the night retrying. The
        counter resets on every recorded match, so one bad match cannot accumulate toward
        the limit across an otherwise healthy night. Three CONSECUTIVE failures means
        something structural changed - a moved button, the event ending, a wedged device -
        and continuing would produce only noise.
        """
        consecutive_failures = 0
        while consecutive_failures < max_restarts:
            try:
                if self._run_one_match():
                    consecutive_failures = 0
                    continue
                consecutive_failures += 1
                logging.warning(
                    f"no match recorded ({consecutive_failures}/{max_restarts})"
                )
            except Exception as exc:  # noqa: BLE001 - one bad match must not end the run
                consecutive_failures += 1
                logging.warning(
                    f"match failed ({consecutive_failures}/{max_restarts}): {exc}"
                )
            self.navigate_to_world()

        raise GameTimeoutError(
            f"stopping: {max_restarts} consecutive cycles recorded no match"
        )
```

Import `GameTimeoutError` from `adb_auto_player.exceptions`. Call `self._collect_forever()` at the end of `collect_solstice_clash`.

- [ ] **Step 3: Add summary recording**

```python
    def _record_summary(self, draft_frame, prematch_frame, theme: str | None) -> None:
        """Read the summary, record the match, and audit every identification.

        `theme` is read during navigation (the summary screen does NOT show it) and
        passed in, so a match cannot be recorded against the wrong balance epoch.
        """
        frame = self.get_screenshot()
        read = read_summary(frame, self._solstice_cfg, self._solstice_library, self._ocr)

        match_id = self._store.record_match(
            MatchRecord(
                source="spectate_summary",
                captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
                    theme=theme,
                outcome=read.winner,
                outcome_source="observed",
                blue_player=read.blue_player,
                red_player=read.red_player,
            )
        )

        slots: list[HeroSlot] = []
        for hero in read.heroes:
            confirmed = self._confirm_by_longpress(hero)
            slots.append(
                HeroSlot(
                    side=hero.side,
                    slot=hero.slot,
                    hero_slug=confirmed or hero.slug,
                    art_ref=hero.art_ref,
                    status="identified" if (confirmed or hero.slug) else "unknown",
                    score=hero.score,
                    margin=hero.margin,
                    cell_type="summary_hero",
                    stat_sword=hero.stats.sword,
                    stat_heart=hero.stats.heart,
                    stat_shield=hero.stats.shield,
                    identified_by="longpress_ocr" if confirmed else "image",
                )
            )
            self._store.record_audit(
                AuditRow(
                    screen_slug="solstice_summary",
                    side=hero.side,
                    slot=hero.slot,
                    image_slug=hero.slug,
                    image_art_ref=hero.art_ref,
                    image_score=hero.score,
                    image_margin=hero.margin,
                    ocr_slug=confirmed,
                    frame_path=None if confirmed == hero.slug else self._archive(frame),
                    match_id=match_id,
                )
            )
        self._store.record_heroes(match_id, slots)
```

- [ ] **Step 4: Add the supporting helpers**

These are referenced by Steps 1-3 and must exist before the module imports cleanly.

```python
    # --- lazily built, because IconLibrary decoding takes seconds and the GUI imports
    # --- this module at startup.
    @property
    def _solstice_cfg(self) -> SolsticeConfig:
        if getattr(self, "_cfg_cache", None) is None:
            self._cfg_cache = SolsticeConfig.load(SOLSTICE_DB)
        return self._cfg_cache

    @property
    def _solstice_library(self) -> IconLibrary:
        if getattr(self, "_lib_cache", None) is None:
            self._lib_cache = IconLibrary.build(self._solstice_cfg, SOLSTICE_ICON_DIR)
        return self._lib_cache

    @property
    def _ocr(self) -> RapidOCRBackend:
        if getattr(self, "_ocr_cache", None) is None:
            self._ocr_cache = RapidOCRBackend()
        return self._ocr_cache

    @property
    def _store(self) -> MatchStore:
        if getattr(self, "_store_cache", None) is None:
            self._store_cache = MatchStore(SOLSTICE_DB)
        return self._store_cache

    def _archive(self, frame: np.ndarray, kind: str = "frame") -> str:
        """Save a frame to the vault and return its path.

        Never /tmp - that is a 16GB tmpfs this project has already filled once. Never
        rmtree the directory either: training frames accumulate across runs by design.
        """
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        directory = TRAINING_ROOT / day
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%H%M%S_%f")
        path = directory / f"{kind}_{stamp}.png"
        cv2.imwrite(str(path), frame)
        return str(path)

    def _read_current_theme(self) -> str | None:
        """OCR the theme from the event screen.

        The theme is NOT on the summary, so it is read here, during navigation, and
        attached to the match recorded in this cycle. Without it, data from two balance
        epochs mixes silently.
        """
        frame = self.get_screenshot()
        blocks = self._ocr.detect_text_blocks(
            frame[THEME_BAND[0] : THEME_BAND[1], :], ConfidenceValue(0.4)
        )
        texts = [b.text.strip() for b in blocks if b.text.strip()]
        for i, text in enumerate(texts):
            if "theme" in text.lower() and i + 1 < len(texts):
                return texts[i + 1]
        return None

    def _capture_training_frame(self, anchor: str, late: bool) -> np.ndarray | None:
        """Grab one draft or prematch frame if that screen is currently up.

        Returns None when we entered mid-match, which is NORMAL: this is optional
        training material and must never prevent the outcome from being recorded.

        `late=True` waits for as many pick slots as possible to fill before capturing -
        the training targets are the pick slots, so an early frame with an empty strip is
        worthless.
        """
        if self.game_find_template_match(template=anchor) is None:
            return None
        if late:
            sleep(TRAINING_LATE_DELAY)
        frame = self.get_screenshot()
        self._archive(frame, kind=anchor.rsplit("/", maxsplit=1)[-1])
        return frame

    def _confirm_by_longpress(self, hero: SummaryHero) -> str | None:
        """Long-press a summary card and OCR the hero name from the popup.

        Returns the confirmed slug, or None if no popup could be read.
        """
        cell = next(
            c
            for c in self._solstice_cfg.cells("summary_hero")
            if c.side == hero.side and c.slot == hero.slot
        )
        point = Point((cell.x0 + cell.x1) // 2, (cell.y0 + cell.y1) // 2)

        for _ in range(LONGPRESS_ATTEMPTS):
            self.hold(point, duration=LONGPRESS_SECONDS)
            sleep(1.0)
            frame = self.get_screenshot()
            # The popup renders downward from blue cards and upward from red ones, so its
            # position is not fixed - it is detected by CONTENT, not geometry.
            blocks = self._ocr.detect_text_blocks(frame, ConfidenceValue(0.5))
            slug = resolve_hero_name([b.text for b in blocks], self._solstice_cfg)
            # Dismiss on EVERY path, including failure. A popup left open covers the
            # screen, so the next long-press and the navigation that follows would act on
            # the wrong UI state - and that failure would look like a matching problem.
            self.tap(Point(540, 1750))
            sleep(0.5)
            if slug is not None:
                return slug
        return None
```

with the module-level constants and imports:

```python
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from adb_auto_player.exceptions import GameTimeoutError
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.ocr import RapidOCRBackend

from ..services.solstice.config import SolsticeConfig
from ..services.solstice.icons import IconLibrary
from ..services.solstice.naming import resolve_hero_name
from ..services.solstice.store import AuditRow, HeroSlot, MatchRecord, MatchStore
from ..services.solstice.summary import SummaryHero, read_summary

SOLSTICE_DB = Path("/mnt/docs/adbautoplayer/data/solstice_clash/heroes.sqlite")
SOLSTICE_ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")
TRAINING_ROOT = Path("/mnt/vault/solstice/training")

# User-tested: short presses can fail to open the popup, over-long ones do no harm. The
# cost is asymmetric, so bias long rather than tuning for the minimum that worked once.
LONGPRESS_SECONDS = 3.0
LONGPRESS_ATTEMPTS = 3
# Wait before grabbing the draft frame so more pick slots have filled.
TRAINING_LATE_DELAY = 8.0
# 'Current Theme: <name>' sits just below the pick strip on the event screen.
THEME_BAND = (820, 1000)
```

`self.hold` is confirmed to exist: `game/_input_mixin.py:211`, signature
`hold(coordinates: Coordinates, duration: float = 3.0, blocking: bool = True, log: bool = True)`.
Its **default duration is already 3.0s**, which is exactly the value user testing settled
on, so `LONGPRESS_SECONDS = 3.0` matches the framework default rather than fighting it.
Do NOT shell out to `adb shell input swipe`.

- [ ] **Step 5: Run the suite**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest -q`

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/solstice_clash.py
git commit -m "feat(solstice): Mode A collection loop with a bounded reset policy

The failure counter resets on every recorded match, so one bad match cannot
accumulate across a healthy night; three consecutive failures stops the run
with an error rather than looping silently."
```

---

### Task 9: Learn transforms from confirmed evidence

**Files:**
- Modify: `AFKJ/mixins/solstice_clash.py`
- Test: `TST/test_tuning.py` (extend)

**Interfaces:**
- Consumes: `tune_cell` (Task 6), `MatchStore.learn_transform` / `record_audit` / `transform_for` (Task 2), `read_summary` (Task 5).
- Produces: `tuning.learn_if_improved(...) -> bool` (pure, keyword-only) and its call site in `_record_summary`.

Without this task the transform table, the store API and the tuner all exist and nothing
ever writes a transform - the "training" half of Mode A would be inert.

**Design note.** The decision logic ("is this worth tuning, did it improve, store it")
lives in `tuning.py` as a pure function, NOT in the mixin. A method on `SolsticeClashMixin`
cannot be tested without a device, so the previous shape of this task had a test that
exercised `tune_cell` and `learn_transform` directly and would still pass if the mixin
never called either - it could not fail for the reason it claimed to check.

- [ ] **Step 1: Write the failing test**

Append to `TST/test_tuning.py`:

```python
def test_learn_if_improved_stores_a_better_transform(cfg, library, frames, tmp_db):
    """The real wiring: confirmed identity in, stored+retrievable transform out."""
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import (
        AuditRow, MatchStore,
    )
    from adb_auto_player.games.afk_journey.services.solstice.tuning import learn_if_improved

    gray = cv2.cvtColor(cv2.imread(str(frames["summary_01"])), cv2.COLOR_BGR2GRAY)
    store = MatchStore(tmp_db)
    audit_id = store.record_audit(AuditRow(
        screen_slug="solstice_summary", side="red", slot=3,
        image_slug="solise", image_art_ref="Solise",
        image_score=0.781, image_margin=0.201, ocr_slug="solise", frame_path=None,
    ))

    stored = learn_if_improved(
        store=store, cfg=cfg, library=library, gray=gray, centre=(90, 1307),
        screen_slug="solstice_summary", image_slug="solise",
        confirmed_slug="solise", art_ref="Solise",
        current_score=0.781, current_margin=0.201, audit_id=audit_id,
    )

    assert stored is True
    got = store.transform_for("solstice_summary", "solise")
    assert got is not None
    assert got["margin"] > 0.201, "must only store an IMPROVEMENT"


def test_learn_if_improved_refuses_unconfirmed(cfg, library, frames, tmp_db):
    """No confirmation means nothing is stored, however good the tuning looks."""
    import cv2

    from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
    from adb_auto_player.games.afk_journey.services.solstice.tuning import learn_if_improved

    gray = cv2.cvtColor(cv2.imread(str(frames["summary_01"])), cv2.COLOR_BGR2GRAY)
    store = MatchStore(tmp_db)

    stored = learn_if_improved(
        store=store, cfg=cfg, library=library, gray=gray, centre=(90, 1307),
        screen_slug="solstice_summary", image_slug="solise",
        confirmed_slug=None, art_ref="Solise",
        current_score=0.781, current_margin=0.201, audit_id=None,
    )

    assert stored is False
    assert store.transform_for("solstice_summary", "solise") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_tuning.py::test_learn_if_improved_stores_a_better_transform -v`

Expected: FAIL until Task 2's `learn_transform` and Task 6's `tune_cell` are both present. If both are already implemented this test may pass immediately - that is fine, it is a regression guard for the wiring below.

- [ ] **Step 3: Wire learning into the loop**

Add to `SolsticeClashMixin`:

First add the pure function to `SVC/tuning.py`:

```python
# Only tune reads in this band. Above it there is nothing worth gaining; below it the read
# was rejected outright, so the identity is not trustworthy enough to tune toward.
TUNE_BAND = (0.70, 0.80)


def learn_if_improved(
    *,
    store,
    cfg: SolsticeConfig,
    library: IconLibrary,
    gray: np.ndarray,
    centre: tuple[int, int],
    screen_slug: str,
    image_slug: str | None,
    confirmed_slug: str | None,
    art_ref: str,
    current_score: float,
    current_margin: float,
    audit_id: int | None,
) -> bool:
    """Tune this cell and store the result, but ONLY from confirmed evidence.

    Tuning toward an UNCONFIRMED identity would make a wrong answer score better and could
    push it past the accept threshold, suppressing the very check that would have caught
    it - the optimiser amplifies whatever it is pointed at, including an error.

    Returns:
        True if a transform was stored.
    """
    # The audit row only counts as confirmation when BOTH channels named the same hero.
    # Checking merely that OCR produced a name is not enough: on a real false positive
    # record_audit() writes agreed=0, and learn_transform() would then raise out of the
    # caller mid-write, failing the whole cycle after some rows were already persisted.
    # A disagreement is an expected outcome here, not an error - it returns False.
    if confirmed_slug is None or audit_id is None or confirmed_slug != image_slug:
        return False
    low, high = TUNE_BAND
    if not (low <= current_score < high):
        return False

    tuned = tune_cell(gray, centre, confirmed_slug, library, cfg)
    if tuned is None or tuned.margin <= current_margin:
        # Never store a result that is not an improvement on what we already had.
        return False

    store.learn_transform(
        audit_id, screen_slug, confirmed_slug, art_ref,
        tuned.scale, tuned.score, tuned.margin,
        crop=(tuned.crop_half_w, tuned.crop_top, tuned.crop_bottom),
    )
    return True
```

Then call it from `_record_summary` in the mixin, right after the audit row is written:

```python
            audit_id = self._store.record_audit(AuditRow(...))
            cell = next(
                c
                for c in self._solstice_cfg.cells("summary_hero")
                if c.side == hero.side and c.slot == hero.slot
            )
            if learn_if_improved(
                store=self._store,
                cfg=self._solstice_cfg,
                library=self._solstice_library,
                gray=cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                centre=((cell.x0 + cell.x1) // 2, (cell.y0 + cell.y1) // 2),
                screen_slug="solstice_summary",
                image_slug=hero.slug,
                confirmed_slug=confirmed,
                art_ref=hero.art_ref or (confirmed or ""),
                current_score=hero.score,
                current_margin=hero.margin,
                audit_id=audit_id,
            ):
                logging.info(f"tuned {confirmed} on the summary screen")
```

Import `learn_if_improved` from `..services.solstice.tuning`.

- [ ] **Step 4: Run the suite**

Run: `cd src-tauri/src-python && /mnt/docs/adbautoplayer/.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/ -q`

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/tuning.py src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/solstice_clash.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_tuning.py
git commit -m "feat(solstice): learn per-hero transforms from confirmed evidence

Only tunes cards in the 0.70-0.80 band whose identity OCR confirmed, and
only stores a result that actually improves the margin."
```

---

### Task 10: Live test

**Files:** none - this task only runs and observes.

- [ ] **Step 1: Confirm the device is up**

```bash
adb devices
adb -s 192.168.240.112:5555 shell dumpsys window | grep mCurrentFocus
```

Expected: device listed, AFK Journey focused. **If Waydroid has crashed or the game is closed, STOP here and report** - do not restart either.

- [ ] **Step 2: Snapshot the database before the run**

```bash
cd /mnt/docs/adbautoplayer
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/solstice_clash/heroes.sqlite')
print({t: c.execute(f'select count(*) from {t}').fetchone()[0]
       for t in ('match','match_hero','identification_audit','hero_screen_transform')})"
```

- [ ] **Step 3: Run one match**

Run the mode with `max_restarts=1` so a failure stops promptly, and watch the log.

- [ ] **Step 4: Verify what was actually written**

```bash
cd /mnt/docs/adbautoplayer
/mnt/docs/adbautoplayer/.venv/bin/python -c "
import sqlite3
c=sqlite3.connect('data/solstice_clash/heroes.sqlite')
print('match:', c.execute('select id,source,outcome,blue_player,red_player from match order by id desc limit 1').fetchall())
print('heroes:', c.execute('select side,slot,hero_slug,status,score from match_hero where match_id=(select max(id) from match)').fetchall())
print('audit:', c.execute('select side,slot,image_slug,ocr_slug,agreed from identification_audit order by id desc limit 6').fetchall())"
```

Expected: one match row with an outcome and both player names; six hero rows; six audit rows. Diff the counts against Step 2 - do not just re-read the code.

- [ ] **Step 5: STOP**

Report the result. **Stop after this test whether it succeeded or failed** - this is an explicit instruction, not a suggestion.

---

## Self-Review

**Spec coverage:** every spec section maps to a task - schema/data model (1, 2), summary parsing incl. positional winner and icon-named stats (5), long-press OCR ground truth (4), verification mode and audit trail (2, 8), transform learning gated on confirmation (1, 2, 6), cross-screen training capture (8), navigation with all three entry branches (7), reset policy (8), reuse-before-building (Global Constraints), retention (8).

**Placeholder scan:** every helper referenced by Task 8 is now written out in Task 8 Step 4. `_current_theme` was a dangling name and is now the method `_read_current_theme()`; Task 8 Step 3 must call that and hold the result for the cycle.

**Type consistency:** `AuditRow`, `HeroSlot`, `MatchRecord`, `MatchStore` (Task 2) are used with those exact names in Task 8. `read_summary` / `SummaryHero` (Task 5) and `resolve_hero_name` (Task 4) likewise. `tune_cell` (Task 6) is wired into the loop by Task 9, which is what makes the training half of the mode actually run.

**Two things the implementer must derive rather than copy:**
- Two anchor templates, `draft_anchor` and `prematch_anchor` under `event/solstice_clash/`, are referenced by `_capture_training_frame` in Task 8 but not listed in Task 7's template table. Cut them in Task 7 from `TST/data/spectate_draft.png` and `TST/data/spectate_prematch.png`, and verify each matches its own source at >= 0.95 before use. Phase 1's existing `draft_selecting.png` is a COMPETE anchor and scored only 0.450 against a spectate frame - it will not work here.
- The `spectate_draft_picks` and `spectate_prematch` cell geometry is seeded without crops in Task 1. Measuring it means applying `tune_cell` to the two fixture frames using identities the summary confirmed. Starting values from the spec: slot centres x 120/260/400 and 678/822/965, cards spanning y 400-530 with the level badge covering the bottom ~30px. This is a follow-up pass after Task 9, not a blocker for it.
