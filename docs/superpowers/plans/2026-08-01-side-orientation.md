# Side Orientation / Canonical Trio Rework - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make match 1476's defect - two independent side labels that could disagree - structurally unrepresentable, by storing each match as two canonically-sorted TRIOS plus pointers, in both the local SQLite database and the server pool.

**Architecture:** A composition lives in exactly one place (`match_hero.trio`), assigned 1 or 2 by the same lexicographic sort `comps_key` already uses. `winning_trio` and `blue_trio` are pointers into it. Values that are measurements rather than compositions (ratings, ranks, predictions, betting pools) move to `trio_1_*` / `trio_2_*`. Local rows keep `blue_trio` so left/right stays fully recoverable; pooled rows have `blue_trio = NULL` because we never watched their draft. The reshape is one atomic, self-running migration that consumes the committed audit sidecar, so no human has to sequence a repair against a schema change.

**Tech Stack:** Python 3.12, SQLite (client, via `data/solstice_clash/migrate.py`), PostgreSQL + SQLAlchemy + Alembic (server, `gameretro-adb-api`), pytest, FastAPI.

**Source spec:** `docs/superpowers/specs/2026-08-01-side-orientation-design.md` (14 review rounds, green). Read it before starting. Where this plan and the spec disagree, the spec wins and the disagreement is a bug in this plan.

## Global Constraints

- **Two repos.** Client: `/home/toshe/Dev/webdevbar/adbautoplayer`. Server: `/home/toshe/Dev/webdevbar/gameretro-adb-api`. Every task names its repo.
- **The server (Phase A) must be deployed and live BEFORE the client (Phase B) reaches any user.** Production accepts only `schema_version` 4 today (`app/config.py:24`). A v5 client shipped first fails every push.
- **Everything in Phase B ships in ONE binary.** `_ensure_schema` runs inside `MatchStore.__init__` (`store.py:257`), so the database reshapes on first launch and every reader in that same binary must already speak the new shape. There is no release where the migration has run and the code has not.
- **Nothing may change what the draft path DOES.** Odds, overlay, auto-bet and the coloured live log all complete during the locked screen, before any summary exists, and were correct in the instance that exposed this bug. Retaining and releasing values is allowed; reading, computing or displaying anything differently is not.
- **Never use the Edit tool on code files** - it silently converts straight quotes to curly ones. Write a unified diff and apply it with `git apply --check` then `git apply`. See `~/.claude/rules/edit-mechanics-and-verification.md`.
- **Ruff must be given explicit paths.** A bare `uvx ruff check --fix` from the repo root reformatted 57 unrelated files during an earlier task. Always: `uvx ruff check --fix <the files you changed>` and `uvx ruff format <the files you changed>`, run from the repo root, never `uv run ruff`.
- **Client tests run from `src-tauri/`**: `uv run pytest tests/...`. Server tests run from the server repo root: `uv run pytest`.
- **Names are never load-bearing.** `left_player` / `right_player` stay as free-text provenance belonging to no trio. They are never repaired, never mapped, never used to decide a side.
- **We do not guess an orientation.** A row with no sidecar verdict gets NULL for every blue-relative value. 8% of audited rows were mirrored, so a guess is wrong about one row in twelve, silently.
- **Canonical sort** is `sorted(trio)` compared lexicographically as a list of hero slugs; trio 1 is the smaller. This is the same sort `comps_key` uses (`matchkey.py`). Never invent a second one.

## File Structure

**Client (`adbautoplayer`):**

| file | responsibility |
|---|---|
| `.../services/solstice/canon.py` | NEW. Pure canonical-trio logic: sort, assign, map a side-relative pair onto trio order. Shared by the store, the migration and the tests. |
| `.../services/solstice/orient.py` | EXISTS (commit `4512ffb4`, 11 tests, no callers). Gains callers in Task 8. |
| `.../services/solstice/store.py` | The validated write boundary and every query, rewritten onto trios. |
| `data/solstice_clash/schema.sql` | Canonical DDL for fresh databases. |
| `data/solstice_clash/migrate.py` | The atomic reshape, the sidecar consumption, and the `canonical_state` predicate. |
| `.../mixins/solstice_clash.py` | Recording path: carry draft trios, resolve orientation, save the summary frame, clear carried state. |
| `.../services/solstice/odds.py` | The fit: trio membership, intercept exclusion for pooled rows. |
| `.../services/solstice/sync.py` | `schema_version` 5 on push, versioned pull. |

**Server (`gameretro-adb-api`):**

| file | responsibility |
|---|---|
| `app/canon.py` | NEW. The same pure canonical-trio logic, server-side. |
| `migrations/versions/0007_canonical_trios.py` | NEW. The reshape plus the ~6-row correction. |
| `app/models.py` | `Match` / `MatchHero` on trios, with the constraint set. |
| `app/schemas.py` | Version 4 and version 5 request/response shapes. |
| `app/routers/matches.py` | Ingest canonicalisation, versioned pull. |
| `app/config.py` | `schema_versions_supported = {4, 5}`. |

---

# PHASE A - SERVER (deploy first)

### Task 1: Canonical trio helper (server)

**Repo:** `gameretro-adb-api`

**Files:**
- Create: `app/canon.py`
- Test: `tests/test_canon.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `canonical_trios(heroes: dict[str, list[str]]) -> tuple[list[str], list[str]]`, `trio_index_for(side: str, trio_1: list[str], by_side: dict[str, list[str]]) -> int`, `map_side_pair(left_value, right_value, left_is_trio: int) -> tuple`. Tasks 2, 3 and 4 all import from here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_canon.py
import pytest

from app.canon import canonical_trios, map_side_pair, trio_index_for


def test_canonical_trios_sorts_within_and_between():
    by_side = {"left": ["zandrok", "brutus", "hepler"], "right": ["mikola", "atalanta", "sonja"]}
    t1, t2 = canonical_trios(by_side)
    # within: alphabetical. between: the lexicographically smaller list is trio 1.
    assert t1 == ["atalanta", "mikola", "sonja"]
    assert t2 == ["brutus", "hepler", "zandrok"]


def test_canonical_trios_is_orientation_free():
    a = {"left": ["zandrok", "brutus", "hepler"], "right": ["mikola", "atalanta", "sonja"]}
    b = {"left": ["mikola", "atalanta", "sonja"], "right": ["zandrok", "brutus", "hepler"]}
    assert canonical_trios(a) == canonical_trios(b)


def test_trio_index_for_names_the_side():
    by_side = {"left": ["zandrok", "brutus", "hepler"], "right": ["mikola", "atalanta", "sonja"]}
    t1, _ = canonical_trios(by_side)
    assert trio_index_for("right", t1, by_side) == 1
    assert trio_index_for("left", t1, by_side) == 2


def test_map_side_pair_follows_the_index():
    # left is trio 2, so the left value belongs to trio 2.
    assert map_side_pair(10, 20, left_is_trio=2) == (20, 10)
    assert map_side_pair(10, 20, left_is_trio=1) == (10, 20)


def test_map_side_pair_passes_nulls_through():
    assert map_side_pair(None, None, left_is_trio=1) == (None, None)


def test_canonical_trios_rejects_a_duplicated_hero():
    by_side = {"left": ["a", "b", "c"], "right": ["c", "d", "e"]}
    with pytest.raises(ValueError, match="appears in both trios"):
        canonical_trios(by_side)


def test_canonical_trios_rejects_a_short_side():
    with pytest.raises(ValueError, match="exactly three"):
        canonical_trios({"left": ["a", "b"], "right": ["c", "d", "e"]})
```

- [ ] **Step 2: Run test to verify it fails**

Run from the server repo root: `uv run pytest tests/test_canon.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.canon'`

- [ ] **Step 3: Write the implementation**

```python
# app/canon.py
"""Canonical trio ordering - the one sort, used everywhere.

A Solstice Clash match is two trios of three heroes. WHICH trio is "1" must be a pure
function of the heroes themselves, never of where they appeared on screen, or the two
sides of the pool disagree about the same match. This is the same sort `comps_key` uses;
there must never be a second one.
"""

from __future__ import annotations

TRIO_SIZE = 3


def canonical_trios(by_side: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Return (trio_1, trio_2), each sorted, with trio_1 the lexicographically smaller.

    Raises:
        ValueError: a side is not exactly three heroes, or a hero appears in both.
    """
    left = sorted(by_side.get("left") or [])
    right = sorted(by_side.get("right") or [])
    for name, trio in (("left", left), ("right", right)):
        if len(trio) != TRIO_SIZE:
            raise ValueError(f"{name} is not exactly three heroes: {trio}")
    overlap = set(left) & set(right)
    if overlap:
        raise ValueError(f"hero appears in both trios: {sorted(overlap)}")
    return (left, right) if left < right else (right, left)


def trio_index_for(side: str, trio_1: list[str], by_side: dict[str, list[str]]) -> int:
    """Which canonical trio number the heroes on `side` form."""
    return 1 if sorted(by_side[side]) == trio_1 else 2


def map_side_pair(left_value, right_value, left_is_trio: int) -> tuple:
    """Reorder a (left, right) measurement pair onto canonical trio order."""
    if left_is_trio == 1:
        return (left_value, right_value)
    return (right_value, left_value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_canon.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uvx ruff check --fix app/canon.py tests/test_canon.py
uvx ruff format app/canon.py tests/test_canon.py
git add app/canon.py tests/test_canon.py
git commit -m "feat: canonical trio ordering helper

One sort, shared by the migration, the models and the ingest path. Trio 1 is the
lexicographically smaller sorted trio, so two contributors who saw the same match
with the sides swapped assign the same numbers."
```

---

### Task 2: Server models on trios

**Repo:** `gameretro-adb-api`

**Files:**
- Modify: `app/models.py:115-254` (`Match` and `MatchHero`)
- Test: `tests/test_models_constraints.py`

**Interfaces:**
- Consumes: `app.canon.canonical_trios` (Task 1).
- Produces: `Match.winning_trio`, `Match.trio_1_rating`, `Match.trio_2_rating`, `Match.trio_1_rank`, `Match.trio_2_rank`, `Match.predicted_trio_1`, `Match.trio_1_pool`, `Match.trio_2_pool`, `Match.trio_1_odds`, `Match.trio_2_odds`, `Match.canonical_state`; `MatchHero.trio`. Task 3's migration creates exactly these; Task 4's schemas serialise them.

Read `app/models.py:115-254` first. Keep every column not named below exactly as it is - `natural_key`, `comps_key`, `captured_at`, `captures_min_at`/`captures_max_at`, `theme_id`, `origin`, `contributor_uuid` and the supersession columns are all untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_constraints.py
"""The constraint set is the whole point: a contradictory row must not be storable.

A constraint nobody has tried to breach is a comment, so each of these inserts a
deliberate violation and asserts the database refuses it.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Match, MatchHero


def _match(session, contributor, theme, **kw):
    """Every NOT NULL column must be supplied or the insert fails for an unrelated
    reason and the constraint under test is never reached. The real model requires
    contributor_id, theme_id, theme_resolved_by and source, and captured_at is a
    datetime - not the string an earlier draft of this test passed."""
    kw.setdefault("canonical_state", "canonical")
    m = Match(
        natural_key=kw.pop("nk", "nk1"),
        comps_key=kw.pop("comps_key", "ck1"),
        captured_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        contributor_id=contributor.id,
        theme_id=theme.id,
        theme_resolved_by="window",
        source="compete",
        **kw,
    )
    session.add(m)
    session.flush()
    return m


def _hero(session, match, trio, slot, slug):
    h = MatchHero(match_id=match.id, trio=trio, slot=slot, hero_slug=slug, status="identified")
    session.add(h)
    return h


def test_trio_must_be_1_or_2(session, contributor, theme):
    m = _match(session, contributor, theme, winning_trio=1)
    _hero(session, m, 3, 1, "a")
    with pytest.raises(IntegrityError):
        session.flush()


def test_slot_must_be_1_2_or_3(session, contributor, theme):
    m = _match(session, contributor, theme, winning_trio=1)
    _hero(session, m, 1, 99, "a")
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_hero_appears_once_in_the_whole_match(session, contributor, theme):
    m = _match(session, contributor, theme, winning_trio=1)
    _hero(session, m, 1, 1, "a")
    _hero(session, m, 2, 1, "a")
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_slot_is_used_once_per_trio(session, contributor, theme):
    m = _match(session, contributor, theme, winning_trio=1)
    _hero(session, m, 1, 1, "a")
    _hero(session, m, 1, 1, "b")
    with pytest.raises(IntegrityError):
        session.flush()


def test_winning_trio_must_be_1_or_2(session, contributor, theme):
    with pytest.raises(IntegrityError):
        _match(session, contributor, theme, winning_trio=3)


def test_canonical_state_is_constrained(session, contributor, theme):
    with pytest.raises(IntegrityError):
        _match(session, contributor, theme, winning_trio=1, canonical_state="maybe")
```

The `contributor` and `theme` fixtures must exist in `tests/conftest.py`; check whether the
existing suite already provides them and add them if not. **Verify each test fails for the
RIGHT reason** - a test that errors on a missing NOT NULL column is not exercising the
constraint it names, and would keep passing after the constraint was removed.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models_constraints.py -v`
Expected: FAIL - `MatchHero` has no attribute `trio`.

- [ ] **Step 3: Rewrite the model columns**

In `app/models.py`, inside `Match`:

Delete `left_player`, `left_rating`, `left_rank`, `right_player`, `right_rating`, `right_rank`, `outcome`, `predicted_left`, `left_pool`, `right_pool`, `left_odds`, `right_odds`, and the `ck_match_outcome` CheckConstraint.

Add:

```python
    # WHICH TRIO won. Not which side - a side is a viewing accident, and two
    # contributors watching the same match may disagree about it. Trio numbers are a
    # pure function of the heroes (app/canon.py), so they cannot.
    winning_trio: Mapped[int | None] = mapped_column(Integer)

    # Measurements attached to a composition, reordered onto canonical trio order on
    # the way in. NOT derivable from match_hero, so dropping them loses the signal.
    trio_1_rating: Mapped[int | None] = mapped_column(Integer)
    trio_2_rating: Mapped[int | None] = mapped_column(Integer)
    trio_1_rank: Mapped[int | None] = mapped_column(Integer)
    trio_2_rank: Mapped[int | None] = mapped_column(Integer)

    # P(trio 1 wins). Valid for every row whatever its origin, which "P(blue wins)"
    # was not: a pooled row has no blue.
    predicted_trio_1: Mapped[float | None] = mapped_column(Float)

    # BACKWARD-COMPATIBILITY PROVENANCE ONLY. Which trio held side='left' in this row
    # as it stood after 0006, so a version-4 client still in the rollout window receives
    # exactly the orientation it would have received before the migration. Those clients
    # train their intercept on every pulled row, and today a pooled row's left IS the
    # pushing contributor's blue - a real first-pick signal that trio order would replace
    # with alphabetical noise. Read by to_v4_wire and by nothing else; delete it when
    # version 4 support is dropped.
    wire_left_trio: Mapped[int | None] = mapped_column(Integer)

    trio_1_pool: Mapped[int | None] = mapped_column(Integer)
    trio_2_pool: Mapped[int | None] = mapped_column(Integer)
    trio_1_odds: Mapped[float | None] = mapped_column(Float)
    trio_2_odds: Mapped[float | None] = mapped_column(Float)

    # 'canonical' or 'unrepresentable'. A row that cannot form two complete trios
    # (a five-hero read) is not a constraint violation and not a pending job - it is
    # explicitly terminal, which is what stops the migration re-running forever.
    canonical_state: Mapped[str | None] = mapped_column(String(16))
```

`predicted_source` and `predicted_locked` stay exactly as they are.

Replace the `__table_args__` outcome check with:

```python
        CheckConstraint("winning_trio IN (1,2)", name="ck_match_winning_trio"),
        CheckConstraint(
            "canonical_state IN ('canonical','unrepresentable')",
            name="ck_match_canonical_state",
        ),
```

In `MatchHero`, replace the `side` column and its constraints:

```python
    trio: Mapped[int] = mapped_column(Integer)
```

```python
        UniqueConstraint("match_id", "trio", "slot"),
        UniqueConstraint("match_id", "hero_slug"),
        CheckConstraint("trio IN (1,2)", name="ck_match_hero_trio"),
        CheckConstraint("slot IN (1,2,3)", name="ck_match_hero_slot"),
```

`UNIQUE(match_id, hero_slug)` is the round-4 finding: the weaker per-trio form permits the same hero in both trios and therefore two identical trios, at which point the canonical sort cannot tell them apart and both pointers stop naming a composition.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models_constraints.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
uvx ruff check --fix app/models.py tests/test_models_constraints.py
uvx ruff format app/models.py tests/test_models_constraints.py
git add app/models.py tests/test_models_constraints.py
git commit -m "feat: server match shape is two canonical trios

Sides are gone. A composition lives once, in match_hero.trio, and winning_trio
points at one of them. Measurements move to trio_1_*/trio_2_*. Player names are
dropped entirely - they are excluded from identity, OCR-fragile, and keeping a
field nobody may trust invites its use.

UNIQUE(match_id, hero_slug) is deliberately match-wide: the per-trio form allows
the same hero in both trios and therefore two identical trios, which makes both
pointers meaningless."
```

---

### Task 3: Migration `0007` - reshape and correct

**Repo:** `gameretro-adb-api`

**Files:**
- Create: `migrations/versions/0007_canonical_trios.py`
- Test: `tests/test_migration_0007.py`

**Interfaces:**
- Consumes: `app.canon.canonical_trios`, `app.canon.trio_index_for`, `app.canon.map_side_pair` (Task 1); `migrations/data/side-audit-by-comps-key.json`, generated in Step 1 from the client's audit plus the operator's database - a migration may not read another repo, and the raw natural-key-keyed sidecar does not join to anything post-`0006`.
- Produces: the deployed schema Task 4 serialises.

**The correction rule, which rounds 11 and 12 settled and which is the easiest thing in this plan to get backwards:**

`0006` swapped only heroes and outcome, leaving predictions, ratings, ranks, pools and odds alone because those are draft-relative. So for the ~6 rows `0006` never reached, **heroes and outcome still agree with each other** and only the draft-relative group disagrees with them.

- Do **NOT** invert the winner. Heroes and outcome come from the same panel pass, so the winner is orientation-free - inverting it breaks a correct fact.
- Do **NOT** swap heroes.
- **DO** bind the draft-relative group (`predicted_left`, ratings, ranks, pools, odds) to the OTHER trio than a naive left-to-trio mapping gives.

The ~70 rows `0006` already swapped map naively; these ~6 map inverted; both reach the same canonical truth.

- [ ] **Step 1: Build a `comps_key`-keyed sidecar - the raw one CANNOT be used**

The committed sidecar is keyed by `natural_key`, and `0006` rewrote every surviving
server row's `natural_key` to `"<comps_key>:<occurrence>"` (`0006_backfill_identity.py:17`).
A lookup by the CURRENT server key therefore misses every entry, silently treating all ~929
audited rows as unaudited and nulling their ratings, predictions, pools and odds. This is
also what the spec means by "recompute the target set against the POST-0006 pool, not
against the client sidecar".

`comps_key` is orientation-free and `0006` did not change it, so it is the join that
survives. Generate the keyed file from the CLIENT database, which holds both keys:

**Use the OPERATOR'S database, not the checked-in one.** `data/solstice_clash/heroes.sqlite`
is a seed: zero matches, and its `match` table has no `comps_key` column at all, so the
command below fails instantly against it. The real database lives at
`~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite` (1548 matches, 1495 with a
`comps_key`, 1544 with a `natural_key` as of 2026-08-01). Work on a COPY.

```bash
cd ~/Dev/webdevbar/adbautoplayer
cp ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite /tmp/heroes-for-sidecar.sqlite
python3 - <<'EOF' > /tmp/side-audit-by-comps-key.json
import json, sqlite3
audit = json.load(open("docs/solstice-clash/side-audit-2026-08-01.json"))
con = sqlite3.connect("/tmp/heroes-for-sidecar.sqlite")
by_nk = {nk: (ck, at) for nk, ck, at in con.execute(
    "SELECT natural_key, comps_key, captured_at FROM match"
    " WHERE natural_key IS NOT NULL AND comps_key IS NOT NULL")}
out, dropped = {}, 0
for e in audit:
    row = by_nk.get(e["natural_key"])
    if row is None:
        dropped += 1
        continue
    ck, captured_at = row
    # A LIST per key, never a scalar. comps_key omits occurrence and time by design, so
    # a genuine rematch shares it - ids 1 and 45 are 31.6 hours apart under one key. A
    # dict keyed on comps_key alone silently keeps the last verdict and applies it to
    # every occurrence, which can bind a rematch's prediction and ratings to the wrong
    # trio on the strength of a different match's audit.
    out.setdefault(ck, []).append({"captured_at": captured_at, "verdict": e["verdict"]})
collisions = {k: v for k, v in out.items() if len(v) > 1}
print(json.dumps({"dropped_without_comps_key": dropped,
                  "keys_with_multiple_occurrences": len(collisions),
                  "verdicts": out}, indent=1))
EOF
cp /tmp/side-audit-by-comps-key.json ~/Dev/webdevbar/gameretro-adb-api/migrations/data/
```

**The filename is `migrations/data/side-audit-by-comps-key.json` everywhere** - in this
step, in Step 6, and in the task's Interfaces block. The natural-key-keyed
`side-audit-2026-08-01.json` is NEVER read by the server; it stays in the client repo as the
provenance the re-keyed file was derived from.

**The envelope is exactly:**

```json
{"dropped_without_comps_key": 12,
 "keys_with_multiple_occurrences": 3,
 "verdicts": {"<comps_key>": [{"captured_at": "...", "verdict": "agree|mirrored|partial|unreadable|incomplete"}]}}
```

The migration reads `["verdicts"]`, not the top level. **Each value is a LIST**, because a
`comps_key` can legitimately cover several occurrences.

**Matching a server row to a verdict needs the time as well as the key - and the time to
test is the row's CAPTURE INTERVAL, not its `captured_at`.** `0006` leaves the survivor's own
`captured_at` untouched while pooling occurrences into it and widening
`captures_min_at`/`captures_max_at` around them, and its clustering is transitive: a bridging
capture can merge occurrences whose endpoints are minutes apart. So an audited member can
legitimately belong to the survivor's occurrence while sitting well outside 120 seconds of
the survivor's own timestamp, and testing `captured_at` would throw that evidence away and
null the row's draft-relative values.

Take the entries under the row's `comps_key` and keep those whose `captured_at` falls within

```
[captures_min_at - 120s, captures_max_at + 120s]
```

falling back to `captured_at` for both bounds when the row has none. That is the same
proximity semantics Part 7 uses, applied to the interval the pool actually recorded. Then:

- **exactly one match** - use its verdict.
- **zero, or more than one** - NO verdict. Ambiguity is not a tie to break; it is exactly
  the case where a wrong guess binds one match's ratings to another's trios.

Log the count of rows resolved this way and the count refused. `keys_with_multiple_occurrences`
tells you in advance how much of this there is; if it is large, stop, because it means the
key is doing less work than assumed. Read `dropped_without_comps_key`
before continuing: with 1544 keyed rows against 929 audit entries the drop count should be
small. A large one means the join is wrong, not that those rows are unauditable - stop and
diagnose rather than proceeding with a sidecar that mostly missed. Assert in the migration
that the loaded verdict map is non-empty, so a missing or mis-shaped file fails loudly
instead of silently nulling the pool.

**A `mirrored` verdict alone does NOT mean invert.** `0006` already swapped the rows it
reached, and a swapped row's heroes now match the draft orientation, so it maps NAIVELY.
Only a row that is mirrored AND was never corrected needs the inversion. `0006` recorded
exactly this: `match_merge_log.orientation_verdict` is `'CORRECTED'` for every survivor it
swapped. **Both cases are covered, and review round 6 wrongly flagged this as a gap** - so
the second branch is worth naming explicitly:

- a group with superseded members logs one row per member, inside the loop, carrying the
  group's verdict (`0006_backfill_identity.py:441-449`);
- a **singleton** correction - nothing to supersede - logs its own row at
  `0006_backfill_identity.py:453-464`, whose comment says exactly why: *"A correction with
  nothing to supersede still has to leave a record, or the only rows this migration silently
  rewrote would be the ones with no log line at all."*

So there is no corrected survivor without a log line, and the lookup below is sound. Confirm
it on the restored dump anyway - count `orientation_verdict = 'CORRECTED'` rows and check it
against the number of swaps `0006` should have made.

So:

```
invert = (verdict == "mirrored") and survivor was NOT 'CORRECTED' in match_merge_log
```

That is the derivation the spec asks for, and it is why the target set comes out at roughly
six rows rather than seventy-six.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_migration_0007.py
"""0007 is destructive and runs once against production. Test it against fixtures
that reproduce all three populations it must handle differently."""
from migrations.versions import _0007_helpers as h


def test_naive_row_maps_left_to_its_own_trio():
    by_side = {"left": ["m", "n", "o"], "right": ["a", "b", "c"]}
    # left sorts second, so left is trio 2.
    out = h.canonicalise_row(
        by_side=by_side, outcome="left", left_rating=100, right_rating=200,
        predicted_left=0.8, mirrored=False,
    )
    assert out["winning_trio"] == 2
    assert out["trio_2_rating"] == 100
    assert out["trio_1_rating"] == 200
    assert out["predicted_trio_1"] == 0.2  # left is trio 2, so P(trio 1) = 1 - 0.8


def test_mirrored_row_keeps_its_winner_and_inverts_only_the_draft_group():
    by_side = {"left": ["m", "n", "o"], "right": ["a", "b", "c"]}
    out = h.canonicalise_row(
        by_side=by_side, outcome="left", left_rating=100, right_rating=200,
        predicted_left=0.8, mirrored=True,
    )
    # The winner is NOT touched: heroes and outcome came from the same panel pass.
    assert out["winning_trio"] == 2
    # The draft-relative group binds to the OTHER trio than naive mapping gives.
    assert out["trio_1_rating"] == 100
    assert out["trio_2_rating"] == 200
    assert out["predicted_trio_1"] == 0.8


def test_row_with_no_verdict_keeps_outcome_and_nulls_the_draft_group():
    by_side = {"left": ["m", "n", "o"], "right": ["a", "b", "c"]}
    out = h.canonicalise_row(
        by_side=by_side, outcome="left", left_rating=100, right_rating=200,
        predicted_left=0.8, mirrored=None,
    )
    assert out["winning_trio"] == 2
    assert out["trio_1_rating"] is None
    assert out["trio_2_rating"] is None
    assert out["predicted_trio_1"] is None


def test_a_shared_comps_key_outside_the_window_gets_no_verdict():
    """Ids 1 and 45 share a comps_key and are 31.6 hours apart. Applying one
    occurrence's verdict to the other would bind its ratings to the wrong trio."""
    entries = [{"captured_at": "2026-08-01T10:00:00Z", "verdict": "mirrored"}]
    assert h.verdict_for(entries, min_at="2026-08-02T17:36:00Z",
                         max_at="2026-08-02T17:36:00Z") is None


def test_a_shared_comps_key_inside_the_window_resolves():
    entries = [{"captured_at": "2026-08-01T10:00:00Z", "verdict": "mirrored"}]
    assert h.verdict_for(entries, min_at="2026-08-01T10:01:00Z",
                         max_at="2026-08-01T10:01:00Z") == "mirrored"


def test_a_widened_capture_interval_still_matches_its_own_member():
    """0006 pooled occurrences into the survivor and widened its bounds without
    touching captured_at, and its clustering is transitive - so a member can sit
    minutes from the survivor's own timestamp and still belong to it."""
    entries = [{"captured_at": "2026-08-01T10:05:00Z", "verdict": "mirrored"}]
    assert h.verdict_for(entries, min_at="2026-08-01T10:00:00Z",
                         max_at="2026-08-01T10:09:00Z") == "mirrored"


def test_two_entries_inside_the_window_refuse_rather_than_pick():
    entries = [{"captured_at": "2026-08-01T10:00:00Z", "verdict": "mirrored"},
               {"captured_at": "2026-08-01T10:00:30Z", "verdict": "agree"}]
    assert h.verdict_for(entries, min_at="2026-08-01T10:00:10Z",
                         max_at="2026-08-01T10:00:10Z") is None


def test_incomplete_row_is_unrepresentable():
    out = h.canonicalise_row(
        by_side={"left": ["m", "n", "o"], "right": ["a", "b"]}, outcome="left",
        left_rating=None, right_rating=None, predicted_left=0.4, mirrored=None,
    )
    assert out["canonical_state"] == "unrepresentable"
    assert out["winning_trio"] is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_migration_0007.py -v`
Expected: FAIL - no module `_0007_helpers`.

- [ ] **Step 4: Write the helper module**

```python
# migrations/versions/_0007_helpers.py
"""Pure row-level logic for 0007, extracted so it can be tested without a database."""

from __future__ import annotations

from app.canon import canonical_trios, map_side_pair, trio_index_for


def canonicalise_row(
    *,
    by_side: dict[str, list[str]],
    outcome: str | None,
    left_rating: int | None,
    right_rating: int | None,
    predicted_left: float | None,
    mirrored: bool | None,
    left_rank: int | None = None,
    right_rank: int | None = None,
    left_pool: int | None = None,
    right_pool: int | None = None,
    left_odds: float | None = None,
    right_odds: float | None = None,
) -> dict:
    """One legacy row -> its canonical form.

    `mirrored` is the sidecar verdict: True, False, or None for "never audited".
    """
    try:
        trio_1, _trio_2 = canonical_trios(by_side)
    except ValueError:
        # Cannot form two complete trios. Terminal, not pending - this is what stops
        # the migration predicate looping on it forever.
        return {"canonical_state": "unrepresentable", "winning_trio": None}

    left_is = trio_index_for("left", trio_1, by_side)
    right_is = 2 if left_is == 1 else 1

    # The winner needs NO verdict. Heroes and outcome come from the same panel pass,
    # so "the trio in this panel won" survives a swap intact. Inverting it for a
    # mirrored row would break a fact that is already correct.
    winning_trio = None
    if outcome == "left":
        winning_trio = left_is
    elif outcome == "right":
        winning_trio = right_is

    out = {"canonical_state": "canonical", "winning_trio": winning_trio}

    if mirrored is None:
        # No orientation evidence. We do not guess: 8% of audited rows were mirrored.
        for key in ("trio_1_rating", "trio_2_rating", "trio_1_rank", "trio_2_rank",
                    "trio_1_pool", "trio_2_pool", "trio_1_odds", "trio_2_odds",
                    "predicted_trio_1"):
            out[key] = None
        return out

    # The draft-relative group. For a mirrored row the header/draft frame disagrees
    # with the panels, so its values belong to the OTHER trio than naive mapping gives.
    draft_left_is = (right_is if mirrored else left_is)

    out["trio_1_rating"], out["trio_2_rating"] = map_side_pair(
        left_rating, right_rating, draft_left_is)
    out["trio_1_rank"], out["trio_2_rank"] = map_side_pair(
        left_rank, right_rank, draft_left_is)
    out["trio_1_pool"], out["trio_2_pool"] = map_side_pair(
        left_pool, right_pool, draft_left_is)
    out["trio_1_odds"], out["trio_2_odds"] = map_side_pair(
        left_odds, right_odds, draft_left_is)

    if predicted_left is None:
        out["predicted_trio_1"] = None
    elif draft_left_is == 1:
        out["predicted_trio_1"] = predicted_left
    else:
        out["predicted_trio_1"] = 1.0 - predicted_left
    return out
```

`verdict_for(entries, *, min_at, max_at)` lives in `_0007_helpers.py` beside
`canonicalise_row`. Callers pass the row's `captures_min_at`/`captures_max_at`, falling back
to `captured_at` for both when they are NULL.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_migration_0007.py -v`
Expected: 8 passed

- [ ] **Step 6: Write the Alembic migration**

`migrations/versions/0007_canonical_trios.py`, `down_revision = "0006"`. Structure, in one transaction:

1. `op.add_column` every new column from Task 2, all nullable - including `wire_left_trio`.
2. Load `migrations/data/side-audit-by-comps-key.json` and read its `["verdicts"]` map. Assert it is non-empty. Resolve each server row to at most one entry by `comps_key` AND its capture interval widened by 120 seconds - `captures_min_at`/`captures_max_at`, NOT `captured_at` - refusing on zero or multiple matches as described in Step 1. Treat only `"mirrored"` as a mirror verdict, only `"agree"` as confirmed, and everything else (`partial`, `unreadable`, `incomplete`, unmatched) as no verdict. Then apply the `CORRECTED` rule from Step 1: `invert` is true only when the verdict is `mirrored` AND `match_merge_log.orientation_verdict` for that survivor is not `'CORRECTED'`.
3. Stream `match` rows joined to `match_hero`, group heroes by `side`, call `canonicalise_row`, and `UPDATE` each row with the result.
4. Populate `wire_left_trio` from the current `side` values BEFORE they are touched: it is whichever canonical trio the `side='left'` heroes form. This is the only chance to capture it. Then `ALTER TABLE match_hero RENAME COLUMN side TO trio` is NOT usable - the values change from text to int. Add `trio`, populate it from the canonicalisation, drop `side`, then add the new uniques and checks.
5. Assert no row has `canonical_state IS NULL`. Raise and roll back if any does - a partially classified pool is worse than an unmigrated one.
6. Assert canonical ordering in bulk: no canonical match may have its `trio=1` heroes sorting after its `trio=2` heroes. Raise on any violation. **This must come after step 4**, not before - the column it reads does not exist until then. Both assertions stay inside the transaction and before any destructive drop.
7. Drop `left_player`, `left_rating`, `left_rank`, `right_player`, `right_rating`, `right_rank`, `outcome`, `predicted_left`, `left_pool`, `right_pool`, `left_odds`, `right_odds`.

`downgrade()` raises `NotImplementedError` with the message `"0007 is irreversible: restore from the pg_dump taken before it ran."` Reconstructing a side from a trio is exactly the information this migration removes on purpose.

- [ ] **Step 7: Verify against a restored copy of production, not against an empty database**

```bash
# on the server, per docs/DEPLOY.md
docker exec gameretro-adb-o8cxcd-db-1 pg_dump -U <user> <db> > /tmp/pre-0007-$(date +%H%M%S).sql
```

Restore that dump into a scratch database locally, run `alembic upgrade head` against it, and check:

```sql
SELECT canonical_state, count(*) FROM match GROUP BY 1;          -- no NULLs
SELECT count(*) FROM match WHERE winning_trio IS NULL
  AND canonical_state = 'canonical';                              -- draws only
SELECT count(*) FROM match WHERE predicted_trio_1 IS NULL
  AND canonical_state = 'canonical';                              -- the unaudited rows
```

Record the three counts in the commit message. If `canonical_state` has any NULL, stop - the classification contract is broken and Step 6.4 should have caught it.

- [ ] **Step 8: Commit**

```bash
uvx ruff check --fix migrations/versions/0007_canonical_trios.py migrations/versions/_0007_helpers.py tests/test_migration_0007.py
uvx ruff format migrations/versions/0007_canonical_trios.py migrations/versions/_0007_helpers.py tests/test_migration_0007.py
git add migrations/ tests/test_migration_0007.py
git commit -m "feat: 0007 canonicalises the pool onto trios

Classifies every legacy match atomically as canonical or unrepresentable, so the
predicate can never loop. The ~6 rows 0006 never reached keep their winner and
their heroes untouched - those already agree with each other - and only their
draft-relative values bind to the other trio.

Verified against a restore of production: <counts from step 7>.
Irreversible by design; downgrade points at the pg_dump."
```

---

### Task 4: Dual-version API (4 and 5)

**Repo:** `gameretro-adb-api`

**Files:**
- Modify: `app/config.py:24`, `app/schemas.py`, `app/routers/matches.py`
- Test: `tests/test_api_dual_version.py`

**Interfaces:**
- Consumes: Task 1's canon helpers, Task 2's models.
- Produces: the wire contract Task 11 (client `sync.py`) speaks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_dual_version.py
"""One release must serve both. A v4 client is the contributor's current build and
breaking it mid-rollout strands their captures."""

V4_MATCH = {
    "index": 0, "natural_key": "nk-v4", "comps_key": "ck-v4",
    "captured_at": "2026-08-01T10:00:00Z", "outcome": "left",
    "left_rating": 100, "right_rating": 200, "predicted_left": 0.8,
    "heroes": [
        {"side": "left", "slot": 1, "hero_slug": "m"},
        {"side": "left", "slot": 2, "hero_slug": "n"},
        {"side": "left", "slot": 3, "hero_slug": "o"},
        {"side": "right", "slot": 1, "hero_slug": "a"},
        {"side": "right", "slot": 2, "hero_slug": "b"},
        {"side": "right", "slot": 3, "hero_slug": "c"},
    ],
}


def test_v4_push_is_canonicalised_on_the_way_in(client, auth, session):
    r = client.post("/v1/matches", json={"schema_version": 4, "matches": [V4_MATCH]}, headers=auth)
    assert r.status_code == 200
    from app.models import Match
    m = session.query(Match).filter_by(natural_key="nk-v4").one()
    # left = (m,n,o) sorts after (a,b,c), so left is trio 2 and it won.
    assert m.winning_trio == 2
    assert m.trio_2_rating == 100
    assert m.predicted_trio_1 == 0.2
    assert m.canonical_state == "canonical"


def test_v5_push_sends_trios_directly(client, auth, session):
    payload = {"schema_version": 5, "matches": [{
        "index": 0, "natural_key": "nk-v5", "comps_key": "ck-v5",
        "captured_at": "2026-08-01T11:00:00Z", "winning_trio": 2,
        "trio_1_rating": 200, "trio_2_rating": 100, "predicted_trio_1": 0.2,
        "heroes": [
            {"trio": 1, "slot": 1, "hero_slug": "a"},
            {"trio": 1, "slot": 2, "hero_slug": "b"},
            {"trio": 1, "slot": 3, "hero_slug": "c"},
            {"trio": 2, "slot": 1, "hero_slug": "m"},
            {"trio": 2, "slot": 2, "hero_slug": "n"},
            {"trio": 2, "slot": 3, "hero_slug": "o"},
        ]}]}
    r = client.post("/v1/matches", json=payload, headers=auth)
    assert r.status_code == 200
    from app.models import Match
    assert session.query(Match).filter_by(natural_key="nk-v5").one().winning_trio == 2


def test_v5_push_rejects_a_non_canonical_trio_order(client, auth):
    """trio 1 must be the lexicographically smaller composition. A client that gets
    this backwards makes every pointer mean the opposite trio, silently."""
    payload = {"schema_version": 5, "matches": [{
        "index": 0, "natural_key": "nk-bad", "comps_key": "ck-bad",
        "captured_at": "2026-08-01T12:00:00Z", "winning_trio": 1,
        "heroes": [
            {"trio": 1, "slot": 1, "hero_slug": "m"},
            {"trio": 1, "slot": 2, "hero_slug": "n"},
            {"trio": 1, "slot": 3, "hero_slug": "o"},
            {"trio": 2, "slot": 1, "hero_slug": "a"},
            {"trio": 2, "slot": 2, "hero_slug": "b"},
            {"trio": 2, "slot": 3, "hero_slug": "c"},
        ]}]}
    r = client.post("/v1/matches", json=payload, headers=auth)
    assert r.json()["results"][0]["status"] == "rejected"


def test_pull_without_a_version_returns_the_v4_shape(client, auth):
    r = client.get("/v1/matches?since=0&limit=10", headers=auth)
    assert r.status_code == 200
    rows = r.json()["matches"]
    if rows:
        assert "outcome" in rows[0] and "winning_trio" not in rows[0]


def test_the_v4_wire_preserves_the_original_orientation(client, auth, session, a_match):
    """A v4 client in the rollout window still trains its intercept on every pulled
    row, so the wire orientation must be the one it would have received before 0007 -
    not trio order, which would replace a real first-pick signal with alphabetical noise.
    """
    a_match.wire_left_trio = 2          # trio 2 held side='left' before the migration
    a_match.winning_trio = 2
    a_match.trio_1_rating, a_match.trio_2_rating = 200, 100
    session.flush()
    row = client.get("/v1/matches?since=0&limit=10", headers=auth).json()["matches"][0]
    assert row["outcome"] == "left"     # trio 2 won and trio 2 is on the left
    assert row["left_rating"] == 100
    assert {h["hero_slug"] for h in row["heroes"] if h["side"] == "left"} == {"m", "n", "o"}


def test_the_v5_shape_never_exposes_wire_left_trio(client, auth):
    """It is backward-compatibility provenance. A v5 consumer that read it would be
    treating an arbitrary wire choice as if it meant something."""
    rows = client.get("/v1/matches?since=0&limit=10&schema_version=5",
                      headers=auth).json()["matches"]
    if rows:
        assert "wire_left_trio" not in rows[0]


def test_pull_with_version_5_returns_trios(client, auth):
    r = client.get("/v1/matches?since=0&limit=10&schema_version=5", headers=auth)
    rows = r.json()["matches"]
    if rows:
        assert "winning_trio" in rows[0] and "outcome" not in rows[0]


def test_an_unknown_version_is_still_rejected(client, auth):
    r = client.post("/v1/matches", json={"schema_version": 9, "matches": []}, headers=auth)
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api_dual_version.py -v`
Expected: FAIL - version 5 rejected, no `schema_version` query parameter.

- [ ] **Step 3: Implement**

- `app/config.py:24`: `schema_versions_supported: set[int] = {4, 5}`.
- `app/schemas.py`: keep the existing v4 request models untouched; add `MatchInV5` / `MatchHeroInV5` carrying `trio`, `winning_trio`, `trio_N_*`, `predicted_trio_1`, and no `side`/`outcome`/player names. Add `MatchOutV5` for pull.
- `app/routers/matches.py`:
  - Ingest branches on `schema_version`. A v4 payload is converted with `canonicalise_row` (import from `migrations.versions._0007_helpers`, or move that function to `app/canon.py` and import from there - prefer the move, so a migration file is not a runtime dependency) with `mirrored=None`, since a live v4 client is reporting its own draft-anchored orientation and **is** trustworthy: pass `mirrored=False` for live ingest, not `None`. A v4 client's `left` IS its blue.
  - A v5 payload is validated: recompute `canonical_trios` from the submitted heroes and reject the row if the client's `trio` assignment disagrees. Never trust the client's numbering.
  - **Pull needs a real v4 ADAPTER, not just a different response model.** After `0007` the
    row has no `outcome`, no `side`, no `left_rating` and no player names, so there is
    nothing for the existing v4 schema to serialise. Write `to_v4_wire(match) -> dict` that
    maps the winner, the heroes, the prediction, the ratings, the ranks, the pools and the
    odds together in one place, so the emitted row is internally consistent. Player names
    emit as `null`.

  - **`to_v4_wire` must emit the row's ORIGINAL wire orientation, and an earlier draft of
    this plan got that wrong.** It said trio 1 is always `left` and called the arbitrariness
    safe "because pulled rows do not touch the intercept" - which is only true AFTER client
    Task 10. The rollout deliberately puts the server first, so during the window there are
    still v4 clients whose `matches_for_fit` does not filter `origin` and whose `odds.py`
    gives every loaded match an intercept of `1.0`.

    Those clients would then train the first-pick intercept on trio order. That is a real
    regression rather than a continuation of today's behaviour: TODAY a pooled row's `left`
    is the pushing contributor's own blue, so it carries a genuine first-pick signal, and
    always-trio-1-left would replace that signal with alphabetical noise.

    So `0007` also records **`wire_left_trio`** (1 or 2): which trio held `side='left'` in
    the row as it stood after `0006`, captured before the columns are dropped. `to_v4_wire`
    orients on it, so a v4 client receives byte-for-byte what it would have received before
    the migration.

    `wire_left_trio` is **backward-compatibility provenance, not truth**. Nothing else reads
    it: not v5 pull, not ingest, not the fit, not `blue_trio` - which stays NULL on pulled
    rows precisely because the server does not know whose blue it was. Delete the column when
    version 4 support is dropped. Add a test asserting no v5 code path references it.

  - Pull reads `schema_version` from the query string, defaulting to 4, and serialises via
    `to_v4_wire` or the v5 model accordingly.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -v`
Expected: all pass, including the pre-existing suite.

- [ ] **Step 5: Commit**

```bash
uvx ruff check --fix app/config.py app/schemas.py app/routers/matches.py tests/test_api_dual_version.py
uvx ruff format app/config.py app/schemas.py app/routers/matches.py tests/test_api_dual_version.py
git add app/ tests/test_api_dual_version.py
git commit -m "feat: accept and serve schema versions 4 and 5

A v4 payload is canonicalised on the way in - the same transform 0007 applies to
existing rows - and its left IS its blue, because a live client reports its own
draft-anchored orientation. A v5 payload sends trios directly and is re-derived
and rejected if the client's numbering disagrees; the server never trusts a
client's canonical ordering.

Pull takes schema_version as a query parameter, defaulting to 4, so already
installed clients keep getting the shape they can parse."
```

---

### Task 5: Deploy the server

**Repo:** `gameretro-adb-api`

- [ ] **Step 1: Confirm with the operator before pushing.** A push to `main` autodeploys via Dokploy. Migrations are manual and `0007` mutates production data.

- [ ] **Step 2: Take and VERIFY a backup**

```bash
docker exec gameretro-adb-o8cxcd-db-1 pg_dump -U <user> <db> > ~/pre-0007-$(date +%Y%m%d-%H%M%S).sql
ls -lh ~/pre-0007-*.sql
```

The automated backup sidecar has never worked (a 376-byte file, `/scripts` empty - recorded as a known issue in `docs/DEPLOY.md`). Check the byte count is plausible, in the megabytes. Do not proceed on a small file.

- [ ] **Step 3: Close the incompatibility window BEFORE pushing**

Push-then-migrate and migrate-then-push are both broken, and this is the trap the plan hit
on its first review. Dokploy autodeploys on push to `main`, so:

- **Push first** and the new code serves against the pre-`0007` database, issuing SQL for
  `winning_trio` and `trio` columns that do not exist yet.
- **Migrate first** and `0007` drops `outcome`, `side` and the rest out from under the
  version still running.

There is no ordering of those two steps that works, because the schema and the application
must change together.

**Stopping the container is not enough either.** Dokploy provides no way to create a
replacement container without starting it, so "deploy then immediately `docker stop`" always
leaves an interval where the new code serves against the old schema - which is the thing
being ruled out, not a smaller version of it.

**Disable at the ROUTE, before the push.** `docs/DEPLOY.md` section 3 puts routing in
Dokploy's Domains UI, through Traefik, so removing the domain makes the API unreachable at
the edge no matter which container is running or when it starts:

1. Dokploy → Domains → remove the `gameretro.net` domain from the `api` service. Note the
   exact settings first: host, path, service name `api`, port, and both certificate toggles
   OFF (`traefik/scripts/dokploy-ssl-automation.sh` owns certificates and skips any domain
   already configured elsewhere).
2. **Verify unreachable** before going further - do not take the UI's word for it:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' https://gameretro.net/adb/v1/matches
   ```
   **The path is `/adb`, with Strip Path ON** (`docs/DEPLOY.md` section 3): the app serves
   `/v1/...` and Traefik strips the prefix. Probing `https://gameretro.net/v1/matches`
   returns 404 whether or not the route exists, so it would wave the destructive migration
   through with production still live.

   Expect 404 or 503. A 200 or a 401 means the route is still up and the migration must
   not start.
3. `git push origin main` and let Dokploy build and deploy normally. Nothing can reach it.
4. `docker exec gameretro-adb-o8cxcd-api-1 alembic upgrade head`
5. Re-add the domain with the settings from step 1. Section 3 notes a routing change needs a
   redeploy, so allow for one.
6. Verify with a real authenticated request before declaring the window closed.

Clients see a few minutes of failures and retry. A failed push is not a rejection - the rows
keep `pushed_at IS NULL` and stay pushable - so no capture is lost. That is strictly better
than either version of the application writing against a shape it does not match.

Removing and re-adding a production route is operator-visible and gated: it needs the
explicit go-ahead from Step 1, and `docs/DEPLOY.md` is the authority on the exact settings.

Note: `alembic upgrade head`, not `uv run alembic` - the container has alembic on PATH.

- [ ] **Step 4: Verify the pool**

```bash
docker exec gameretro-adb-o8cxcd-db-1 psql -U <user> -d <db> -c \
  "SELECT canonical_state, count(*) FROM match GROUP BY 1;"
```

Expected: no NULL bucket. Also confirm a v4 pull still works, because the contributor's build is still v4:

```bash
curl -s -H "Authorization: Bearer <token>" \
  "https://<host>/v1/matches?since=0&limit=1" | head -c 400
```

---

# PHASE B - CLIENT (one binary, ships after Phase A is live)

### Task 6: Canonical trio helper and the validated write boundary (client)

**Repo:** `adbautoplayer`

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/canon.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_canon.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `canonical_trios`, `trio_index_for`, `map_side_pair` (same three signatures as Task 1 - copy the file, the two repos do not share code), plus `assert_canonical(rows: list[dict]) -> None` used by Task 8's write boundary.

- [ ] **Step 1: Write the failing test**

```python
# tests/games/afk_journey/services/solstice/test_canon.py
import pytest

from adb_auto_player.games.afk_journey.services.solstice.canon import (
    assert_canonical, canonical_trios, map_side_pair, trio_index_for,
)


def _rows(pairs):
    return [{"trio": t, "slot": s, "hero_slug": g, "status": "identified"} for t, s, g in pairs]


def test_canonical_trios_matches_the_comps_key_sort():
    by_side = {"left": ["zandrok", "brutus", "hepler"], "right": ["mikola", "atalanta", "sonja"]}
    t1, t2 = canonical_trios(by_side)
    assert t1 == ["atalanta", "mikola", "sonja"]
    assert t2 == ["brutus", "hepler", "zandrok"]


def test_trio_index_for_and_map_side_pair_agree():
    by_side = {"left": ["m", "n", "o"], "right": ["a", "b", "c"]}
    t1, _ = canonical_trios(by_side)
    assert trio_index_for("left", t1, by_side) == 2
    assert map_side_pair("L", "R", 2) == ("R", "L")


def test_assert_canonical_accepts_a_good_match():
    assert_canonical(_rows([(1, 1, "a"), (1, 2, "b"), (1, 3, "c"),
                            (2, 1, "m"), (2, 2, "n"), (2, 3, "o")]))


def test_assert_canonical_rejects_inverted_numbering():
    """The round-7 hole: every other constraint passes while trio 1 holds the
    lexicographically LARGER composition, so every pointer means the other trio."""
    with pytest.raises(ValueError, match="not canonically ordered"):
        assert_canonical(_rows([(1, 1, "m"), (1, 2, "n"), (1, 3, "o"),
                                (2, 1, "a"), (2, 2, "b"), (2, 3, "c")]))


def test_assert_canonical_rejects_a_hero_in_both_trios():
    with pytest.raises(ValueError, match="both trios"):
        assert_canonical(_rows([(1, 1, "a"), (1, 2, "b"), (1, 3, "c"),
                                (2, 1, "a"), (2, 2, "n"), (2, 3, "o")]))


def test_assert_canonical_rejects_a_bad_slot():
    with pytest.raises(ValueError, match="slot"):
        assert_canonical(_rows([(1, 1, "a"), (1, 2, "b"), (1, 99, "c"),
                                (2, 1, "m"), (2, 2, "n"), (2, 3, "o")]))


def test_assert_canonical_rejects_a_duplicate_slot():
    with pytest.raises(ValueError, match="slot"):
        assert_canonical(_rows([(1, 1, "a"), (1, 1, "b"), (1, 3, "c"),
                                (2, 1, "m"), (2, 2, "n"), (2, 3, "o")]))


def test_assert_canonical_rejects_a_bad_trio_number():
    with pytest.raises(ValueError, match="trio"):
        assert_canonical(_rows([(3, 1, "a"), (1, 2, "b"), (1, 3, "c"),
                                (2, 1, "m"), (2, 2, "n"), (2, 3, "o")]))


def test_assert_canonical_allows_an_incomplete_unidentified_read():
    """An in-progress match is not a violation - only a COMPLETE one is checked."""
    assert_canonical([{"trio": 1, "slot": 1, "hero_slug": None, "status": "unknown"}])
```

- [ ] **Step 2: Run to verify it fails**

Run from `src-tauri/`: `uv run pytest tests/games/afk_journey/services/solstice/test_canon.py -v`
Expected: FAIL - no module `canon`.

- [ ] **Step 3: Implement**

Copy `app/canon.py` from Task 1 verbatim (the three functions), then add:

```python
def assert_canonical(rows: list[dict]) -> None:
    """Reject any hero set that could not have come off a real screen.

    Every write of match_hero goes through this. A constraint nobody has tried to
    breach is a comment, so each rule here has a test that breaches it.

    Raises:
        ValueError: on any violation, naming the rule.
    """
    identified = [r for r in rows if r.get("hero_slug")]
    for row in rows:
        if row.get("trio") not in (1, 2):
            raise ValueError(f"trio must be 1 or 2, got {row.get('trio')!r}")
        if row.get("slot") not in (1, 2, 3):
            raise ValueError(f"slot must be 1, 2 or 3, got {row.get('slot')!r}")

    seen_slots: set[tuple[int, int]] = set()
    for row in rows:
        key = (row["trio"], row["slot"])
        if key in seen_slots:
            raise ValueError(f"duplicate trio/slot {key}")
        seen_slots.add(key)

    slugs = [r["hero_slug"] for r in identified]
    if len(slugs) != len(set(slugs)):
        raise ValueError("a hero appears in both trios")

    grouped = {1: sorted(r["hero_slug"] for r in identified if r["trio"] == 1),
               2: sorted(r["hero_slug"] for r in identified if r["trio"] == 2)}
    # Only a COMPLETE match is checked for ordering. A partial read is a match still
    # in progress, not a contradiction.
    if len(grouped[1]) == TRIO_SIZE and len(grouped[2]) == TRIO_SIZE:
        if grouped[1] > grouped[2]:
            raise ValueError(
                f"trios are not canonically ordered: {grouped[1]} should not follow "
                f"{grouped[2]} - trio 1 is the lexicographically smaller composition"
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/games/afk_journey/services/solstice/test_canon.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
cd ~/Dev/webdevbar/adbautoplayer
uvx ruff check --fix src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/canon.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_canon.py
uvx ruff format src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/canon.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_canon.py
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/canon.py src-tauri/src-python/tests/games/afk_journey/services/solstice/test_canon.py
git commit -m "feat: canonical trio helper and validated write boundary

assert_canonical is the single gate every match_hero write passes. Canonical
ordering is enforced here because SQLite cannot express a cross-row comparison
as a CHECK - without it, a writer can store the lexicographically larger trio as
trio 1 while every other constraint passes, and every pointer silently means the
other composition."
```

---

### Task 7: The local reshape migration

**Repo:** `adbautoplayer`

**Files:**
- Modify: `data/solstice_clash/schema.sql:145-200` (`match`), `:264-292` (`match_hero`), `:312-320` (`match_odds`)
- Modify: `data/solstice_clash/migrate.py:30-90` (`ADD_COLUMNS`), `:193-215` (`apply`), `:280-335` (`_apply`)
- Modify: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/store.py:311` (`_schema_is_current`)
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_reshape_migration.py`

**Interfaces:**
- Consumes: `canon.canonical_trios`, `canon.trio_index_for`, `canon.map_side_pair` (Task 6); the sidecar at `docs/solstice-clash/side-audit-2026-08-01.json`, which must be added to the packaged resources so the shipped binary can read it.
- Produces: the schema Task 8 reads and writes.

**This is the highest-risk task in the plan.** It runs unattended on the contributor's Windows machine, it drops columns, and it is not reversible. The correction rule is identical to Task 3's - re-read it there.

- [ ] **Step 1: Package the sidecar**

The migration runs inside the shipped binary and cannot read `docs/`. Copy the sidecar to `data/solstice_clash/side-audit-2026-08-01.json` and confirm it is included by the Tauri bundle the same way `schema.sql` is (check `resource_file` resolution and the bundle's resource globs in `src-tauri/tauri.conf.json`). A migration that silently cannot find its sidecar would NULL every `blue_trio` - which is safe, but wrong, and would be invisible.

- [ ] **Step 2: Write the failing test**

```python
# tests/games/afk_journey/services/solstice/test_reshape_migration.py
"""The reshape runs unattended on a contributor's machine and is irreversible.

Each test builds a legacy-shaped database, runs the real migrate.apply(), and asserts
on the result - never on the code that produced it.
"""
import sqlite3

import pytest


def _legacy_db(tmp_path, rows):
    """rows: list of (natural_key, outcome, predicted_left, left_rating, right_rating,
    [(side, slot, slug), ...])"""
    db = tmp_path / "heroes.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE match(id INTEGER PRIMARY KEY, natural_key TEXT UNIQUE, source TEXT NOT NULL,
          captured_at TEXT NOT NULL, comps_key TEXT, outcome TEXT, predicted_left REAL,
          left_player TEXT, left_rating INTEGER, left_rank INTEGER,
          right_player TEXT, right_rating INTEGER, right_rank INTEGER,
          origin TEXT NOT NULL DEFAULT 'local', superseded_by INTEGER);
        CREATE TABLE match_hero(id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL,
          side TEXT NOT NULL, slot INTEGER NOT NULL, hero_slug TEXT, status TEXT NOT NULL,
          UNIQUE(match_id, side, slot));
        CREATE TABLE match_odds(id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL,
          sampled_at TEXT NOT NULL, left_pool INTEGER, right_pool INTEGER,
          left_odds REAL, right_odds REAL, spectators INTEGER);
    """)
    for nk, outcome, pred, lr, rr, heroes in rows:
        cur = con.execute(
            "INSERT INTO match(natural_key, source, captured_at, outcome, predicted_left,"
            " left_rating, right_rating) VALUES(?,'compete','2026-08-01T00:00:00Z',?,?,?,?)",
            (nk, outcome, pred, lr, rr))
        for side, slot, slug in heroes:
            con.execute(
                "INSERT INTO match_hero(match_id, side, slot, hero_slug, status)"
                " VALUES(?,?,?,?,?)",
                (cur.lastrowid, side, slot, slug, "identified" if slug else "unknown"))
    con.commit()
    con.close()
    return db


COMPLETE = [("left", 1, "m"), ("left", 2, "n"), ("left", 3, "o"),
            ("right", 1, "a"), ("right", 2, "b"), ("right", 3, "c")]
FIVE_HEROES = [("left", 1, "m"), ("left", 2, "n"), ("left", 3, "o"),
               ("right", 1, "a"), ("right", 2, "b")]


def test_an_agree_row_maps_left_to_its_own_trio(tmp_path, migrate, sidecar):
    sidecar({"nk-agree": "agree"})
    db = _legacy_db(tmp_path, [("nk-agree", "left", 0.8, 100, 200, COMPLETE)])
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT winning_trio, blue_trio, predicted_trio_1, trio_1_rating, canonical_state"
        " FROM match").fetchone()
    assert row == (2, 2, 0.2, 200, "canonical")


def test_a_mirrored_row_keeps_its_winner_and_flips_only_the_draft_group(tmp_path, migrate, sidecar):
    sidecar({"nk-mir": "mirrored"})
    db = _legacy_db(tmp_path, [("nk-mir", "left", 0.8, 100, 200, COMPLETE)])
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    winning, blue, pred = con.execute(
        "SELECT winning_trio, blue_trio, predicted_trio_1 FROM match").fetchone()
    assert winning == 2          # NOT inverted - heroes and outcome agree already
    assert blue == 1             # legacy left is red, so blue is the other trio
    assert pred == 0.8           # the draft group binds to the other trio


def test_an_unaudited_row_keeps_its_winner_and_nulls_the_rest(tmp_path, migrate, sidecar):
    sidecar({})
    db = _legacy_db(tmp_path, [("nk-none", "left", 0.8, 100, 200, COMPLETE)])
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    winning, blue, pred, r1 = con.execute(
        "SELECT winning_trio, blue_trio, predicted_trio_1, trio_1_rating FROM match").fetchone()
    assert winning == 2
    assert (blue, pred, r1) == (None, None, None)


def test_a_five_hero_row_is_unrepresentable_not_pending(tmp_path, migrate, sidecar):
    """Row 625's case. It must be TERMINAL, or the migration re-runs forever."""
    sidecar({})
    db = _legacy_db(tmp_path, [("nk-625", "left", 0.478, None, None, FIVE_HEROES)])
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    state, winning = con.execute("SELECT canonical_state, winning_trio FROM match").fetchone()
    assert state == "unrepresentable"
    assert winning is None


def test_the_migration_is_idempotent_and_terminates(tmp_path, migrate, sidecar):
    """The predicate reads only what SURVIVES the reshape. A predicate phrased in
    terms of the dropped columns fails on the second launch with a missing column."""
    sidecar({"nk-a": "agree"})
    db = _legacy_db(tmp_path, [("nk-a", "left", 0.8, 100, 200, COMPLETE),
                               ("nk-625", "left", 0.4, None, None, FIVE_HEROES)])
    migrate.apply(str(db), quiet=True)
    first = sqlite3.connect(db).execute("SELECT * FROM match").fetchall()
    migrate.apply(str(db), quiet=True)   # must not raise
    second = sqlite3.connect(db).execute("SELECT * FROM match").fetchall()
    assert first == second


def test_nothing_is_destroyed(tmp_path, migrate, sidecar):
    sidecar({})
    db = _legacy_db(tmp_path, [("nk-x", "left", 0.8, 100, 200, COMPLETE)])
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    snap = con.execute(
        "SELECT outcome, predicted_left, left_rating, right_rating FROM legacy_side_snapshot"
    ).fetchone()
    assert snap == ("left", 0.8, 100, 200)


def test_the_dropped_columns_do_not_come_back(tmp_path, migrate, sidecar):
    """ADD_COLUMNS still declaring predicted_left would re-add it EMPTY on the next
    launch, leaving a schema that looks current with the value gone."""
    sidecar({})
    db = _legacy_db(tmp_path, [("nk-y", "left", 0.8, 100, 200, COMPLETE)])
    migrate.apply(str(db), quiet=True)
    migrate.apply(str(db), quiet=True)
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(match)")}
    assert not (cols & {"outcome", "predicted_left", "left_rating", "right_rating",
                        "left_rank", "right_rank"})
    hero_cols = {r[1] for r in con.execute("PRAGMA table_info(match_hero)")}
    assert "side" not in hero_cols and "trio" in hero_cols


def test_a_fresh_database_gets_the_new_shape_directly(tmp_path, migrate, sidecar):
    """A brand-new install runs schema.sql, which now has trio and winning_trio and no
    side or outcome. _backfill_comps_key references both of the removed columns
    unconditionally, so without a shape check _apply raises before the reshape runs."""
    sidecar({})
    db = tmp_path / "fresh.sqlite"
    migrate.apply(str(db), quiet=True)   # must not raise
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(match_hero)")}
    assert "trio" in cols and "side" not in cols


def test_a_database_missing_predicted_left_entirely_still_migrates(tmp_path, migrate, sidecar):
    """The collaborator's database was old enough to lack the column."""
    sidecar({})
    db = tmp_path / "old.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE match(id INTEGER PRIMARY KEY, natural_key TEXT UNIQUE, source TEXT NOT NULL,
          captured_at TEXT NOT NULL, outcome TEXT);
        CREATE TABLE match_hero(id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL,
          side TEXT NOT NULL, slot INTEGER NOT NULL, hero_slug TEXT, status TEXT NOT NULL);
    """)
    con.commit(); con.close()
    migrate.apply(str(db), quiet=True)   # must not raise
```

Add a `conftest.py` beside it providing the `migrate` fixture (imports `data/solstice_clash/migrate.py` by path) and a `sidecar` fixture that monkeypatches the migration's sidecar loader to return the given `{natural_key: verdict}` map.

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/games/afk_journey/services/solstice/test_reshape_migration.py -v`
Expected: FAIL on every test - `no such column: winning_trio`.

- [ ] **Step 4: Update `schema.sql`**

In `match`: delete `left_rating`, `left_rank`, `right_rating`, `right_rank`, `outcome`, and (from `ADD_COLUMNS`) `predicted_left`. **`left_player` and `right_player` STAY** - they are free-text provenance belonging to no trio, and the spec keeps them deliberately. Do not drop them. Add:

```sql
  -- WHICH trio won, and which one was ours. Not a side: a side is a label that can
  -- contradict the heroes, and match 1476 is what that costs. A pointer into
  -- match_hero cannot contradict it, because there is only one composition to point at.
  winning_trio   INTEGER CHECK(winning_trio IN (1,2)),
  -- 1, 2, or NULL when we did not watch the draft (a pulled row). NULL IS the flag.
  blue_trio      INTEGER CHECK(blue_trio IN (1,2)),
  trio_1_rating  INTEGER, trio_2_rating INTEGER,
  trio_1_rank    INTEGER, trio_2_rank   INTEGER,
  -- P(trio 1 wins). Blue-relative is not expressible for a pulled row.
  predicted_trio_1 REAL,
  -- 'canonical' | 'unrepresentable'. NULL means the reshape has not run.
  canonical_state TEXT CHECK(canonical_state IN ('canonical','unrepresentable')),
```

In `match_hero`: `side TEXT NOT NULL` becomes `trio INTEGER NOT NULL CHECK(trio IN (1,2))`; add `CHECK(slot IN (1,2,3))`; `UNIQUE(match_id, side, slot)` becomes `UNIQUE(match_id, trio, slot)`; add `UNIQUE(match_id, hero_slug)`.

In `match_odds`: `left_pool`/`right_pool`/`left_odds`/`right_odds` become `trio_1_pool`/`trio_2_pool`/`trio_1_odds`/`trio_2_odds`.

**`data/solstice_clash/views.sql` must be rewritten in this same step, and its execution
resequenced.** `hero_matchup` references `m.outcome` and `h.side` throughout (`views.sql:44-68`),
and `_apply` executes `views.sql` at `migrate.py:285` - BEFORE the backfill at :331 and
before the reshape. So a fresh database on the new schema fails there immediately, and an
upgraded one fails on its next launch when the view is recreated against columns that no
longer exist. Neither failure is subtle, but both happen before anything this plan adds gets
a chance to run.

Rewrite the view onto `winning_trio` and `trio`. The translation is direct, because the view
is ALREADY orientation-free in spirit - it asks whether the side holding the
lexicographically smaller hero won:

```sql
SUM(c.winning_trio IS NOT NULL AND (c.winning_trio = 1) =  (l.hero_slug < r.hero_slug)) AS a_wins,
```

`winning_trio = 1` IS "the smaller composition won", so the comparison it was expressing by
hand now falls out of the schema. Replace the completeness subqueries' `h.side = 'left'` /
`'right'` with `h.trio = 1` / `2`, the joins likewise, and `m.outcome IN ('left','right','draw')`
with a `winning_trio IS NOT NULL OR <draw marker>` test - carry draws however the reshape
represents them, and state that choice in the view's own comment.

Then move the `con.executescript(open(VIEWS).read())` call at `migrate.py:285` to AFTER
`_reshape_to_trios(con)`, so views are only ever built against the finished shape.

Replace `idx_match_hero_side` with `idx_match_hero_trio ON match_hero(match_id, trio, hero_slug)` and `idx_match_outcome` with `idx_match_winning_trio ON match(winning_trio)`. Bump `SCHEMA_VERSION` to 6.

Add:

```sql
-- Append-only. Written before the reshape drops anything, and never read again by
-- the app. It exists so a destructive migration is auditable after the fact, and so
-- the ~157 rows with no orientation evidence can be resolved later if better
-- evidence appears rather than being gone.
CREATE TABLE IF NOT EXISTS legacy_side_snapshot(
  match_id     INTEGER PRIMARY KEY,
  sides_json   TEXT,      -- [{"side","slot","hero_slug"}, ...] as it stood
  outcome      TEXT,
  predicted_left REAL,
  left_rating  INTEGER, right_rating INTEGER,
  left_rank    INTEGER, right_rank   INTEGER,
  odds_json    TEXT,      -- the side-relative match_odds rows verbatim
  captured_at  TEXT
);
```

- [ ] **Step 5: Update `ADD_COLUMNS` and write the reshape**

In `migrate.py`:

- **Delete** `("match", "predicted_left", "REAL")` from `ADD_COLUMNS`. This is the round-9 finding: the list exists to upgrade databases predating a column, so leaving it there re-adds `predicted_left` empty on the launch after the drop.
- Add the new `match` columns to `ADD_COLUMNS` so old databases acquire them.
- **Make `_backfill_comps_key` shape-aware FIRST.** Its query at `migrate.py:216-250`
  references `match_hero.side` and `m.outcome` unconditionally. Once `schema.sql` creates
  fresh databases with `trio` and `winning_trio` instead, `_apply` raises a missing-column
  error before the reshape is ever reached - so a brand-new install breaks. Give it two
  query forms selected by a `PRAGMA table_info` check, or skip it entirely when `side` is
  absent (a fresh database has no rows to backfill). Cover this with the
  `test_a_fresh_database_gets_the_new_shape_directly` case below.
- Add `_reshape_to_trios(con)`, called from `_apply` after `_backfill_comps_key(con)`, in ONE transaction:
  1. If `canonical_state` exists and no `match` row has it NULL, return immediately - the reshape is done.
  2. Create `legacy_side_snapshot` and populate it from every match that still has legacy columns. Guard each column with a `PRAGMA table_info` presence check, so a database predating `predicted_left` does not raise.
  3. Add the new columns.
  4. Load the sidecar into `{natural_key: verdict}`. Only `"mirrored"` is `True`, only `"agree"` is `False`, everything else and every absence is `None`.
  5. Per match: group `match_hero` by `side`, call the same `canonicalise_row` logic as Task 3 (put it in `canon.py` and import it in both places rather than writing it twice), and additionally set `blue_trio` - which is `left_is` for `agree`, the other trio for `mirrored`, and `NULL` for no verdict.
  6. Rewrite `match_hero.side` into `trio` and `match_odds` into trio order via `op`-free SQLite table rebuild (`CREATE TABLE ..._new`, `INSERT SELECT`, `DROP`, `RENAME`) - SQLite cannot add constraints to an existing table.
  7. Assert no `match` row has `canonical_state IS NULL`, and assert canonical ordering in bulk. Raise and roll back on either.
  8. Drop the legacy `match` columns via the same rebuild.
- In `store.py:311` `_schema_is_current`, **replace the existing `unkeyed` query at :345-352
  before adding anything.** It reads `m.outcome` and `match_hero.side` - both dropped - so
  after the reshape it raises `no such column`, the bare `except sqlite3.Error` swallows it,
  and the function returns `False` on EVERY launch. The migration would then run forever and
  the new predicate would never even be reached. Rewrite it as:

  ```python
  unkeyed = con.execute(
      "SELECT 1 FROM match m WHERE m.comps_key IS NULL"
      " AND m.winning_trio IS NOT NULL"
      " AND (SELECT COUNT(*) FROM match_hero WHERE match_id=m.id"
      "      AND trio=1 AND hero_slug IS NOT NULL) = 3"
      " AND (SELECT COUNT(*) FROM match_hero WHERE match_id=m.id"
      "      AND trio=2 AND hero_slug IS NOT NULL) = 3"
      " LIMIT 1"
  ).fetchone()
  ```

  Then add the reshape predicate: return `False` when `canonical_state` is absent from
  `match`, or when any row has it NULL. That predicate is deliberately phrased only in terms
  of what survives the reshape - both earlier attempts read columns the migration removes and
  failed on the second launch.

- **Add a test that the migration runs ONCE.** `test_the_migration_is_idempotent_and_terminates`
  checks `migrate.apply` twice; add one that constructs a real `MatchStore` twice against a
  migrated database and asserts `_schema_is_current` returns `True` the second time. A
  swallowed `sqlite3.Error` is invisible to the apply-level test.

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/games/afk_journey/services/solstice/test_reshape_migration.py -v`
Expected: 9 passed

- [ ] **Step 7: Verify against a COPY of the real database**

```bash
cp ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite /tmp/heroes-copy.sqlite
python3 data/solstice_clash/migrate.py /tmp/heroes-copy.sqlite
sqlite3 /tmp/heroes-copy.sqlite \
  "SELECT canonical_state, count(*) FROM match GROUP BY 1;
   SELECT count(*) FROM match WHERE blue_trio IS NULL AND canonical_state='canonical';
   SELECT count(*) FROM legacy_side_snapshot;"
```

Expected shape, from the spec: no NULL `canonical_state`; roughly 157 canonical rows with `blue_trio IS NULL`; a snapshot row per legacy match. Never run this against the live database - always a copy.

- [ ] **Step 8: Commit**

```bash
uvx ruff check --fix data/solstice_clash/migrate.py <the test file>
uvx ruff format data/solstice_clash/migrate.py <the test file>
git add data/solstice_clash/ src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/store.py <the test file>
git commit -m "feat: reshape the local database onto canonical trios

One atomic migration does the repair AND the shape change, because they cannot be
sequenced by asking - the contributor's build reshapes on launch, and a reshape
that ran first would derive blue_trio from a legacy left that is wrong for exactly
the 76 rows in question.

The completion predicate reads canonical_state and nothing else. Both earlier
attempts were phrased in terms of the dropped columns: one looped forever on the
five-hero row, the other failed on the second launch with a missing column.

predicted_left is removed from ADD_COLUMNS - left there it would re-add itself
empty after the drop, leaving a schema that looks current with the value gone.

Verified against a copy of the real database: <counts from step 7>."
```

---

### Task 8: `store.py` onto trios

**Repo:** `adbautoplayer`

**Files:**
- Modify: `.../solstice/store.py` - `HeroSlot` (:52), `MatchRecord` (:40-49), `_HERO_COLS` (:196), `record_match` (:374), `record_heroes` (:609), `heroes_for` (:630), `finalise_identity` (:507), `set_outcome` (:602), `record_odds` (:690), `odds_for` (:706), `record_prediction` (:748), `scored_predictions` (:769), `matches_for_fit` (:787), `pushable_matches` (:852), `upsert_synced` (:1089)
- Test: `.../tests/games/afk_journey/services/solstice/test_store_trios.py`

**Interfaces:**
- Consumes: `canon.assert_canonical`, `canon.canonical_trios` (Task 6); the schema from Task 7.
- Produces: `HeroSlot(trio: int, slot: int, ...)` - Task 9 constructs these. `matches_for_fit()` returning `(match_id, winning_trio, theme_id, event_id, trio_1_rating, trio_2_rating, blue_trio, hero_trio, hero_slug)` - Task 10 unpacks positionally, so a new column goes on the END.

- [ ] **Step 1: Write the failing test**

```python
# tests/games/afk_journey/services/solstice/test_store_trios.py
import pytest

from adb_auto_player.games.afk_journey.services.solstice.store import (
    HeroSlot, MatchRecord, MatchStore,
)


def _slots(pairs):
    return [HeroSlot(trio=t, slot=s, hero_slug=g, art_ref=None, status="identified")
            for t, s, g in pairs]


def _a_match():
    """MatchRecord carries NO trio-relative field - the row is inserted before the
    heroes are read, so there is nothing to number yet. finalise_summary supplies them."""
    return MatchRecord(source="compete_summary", captured_at="2026-08-01T00:00:00Z",
                       theme=None, event_id=None, theme_id=None)


def test_record_heroes_rejects_inverted_canonical_order(store):
    mid = store.record_match(_a_match())
    with pytest.raises(ValueError, match="not canonically ordered"):
        store.record_heroes(mid, _slots([(1, 1, "m"), (1, 2, "n"), (1, 3, "o"),
                                         (2, 1, "a"), (2, 2, "b"), (2, 3, "c")]))


def test_record_heroes_rejects_a_hero_in_both_trios(store):
    mid = store.record_match(_a_match())
    with pytest.raises(ValueError, match="both trios"):
        store.record_heroes(mid, _slots([(1, 1, "a"), (1, 2, "b"), (1, 3, "c"),
                                         (2, 1, "a"), (2, 2, "n"), (2, 3, "o")]))


def test_scored_predictions_needs_no_side(store):
    """It scores predicted_trio_1 against winning_trio, so a pulled row scores too."""
    mid = store.record_match(_a_match())
    store.record_heroes(mid, _slots([(1, 1, "a"), (1, 2, "b"), (1, 3, "c"),
                                     (2, 1, "m"), (2, 2, "n"), (2, 3, "o")]))
    store.record_prediction(mid, predicted_trio_1=0.9, source="model", locked=6)
    store.finalise_summary(mid, winning_trio=1, blue_trio=1, outcome_source="observed")
    rows = store.scored_predictions()
    assert rows == [(0.9, 1, "model", 6)]


def test_matches_for_fit_returns_trio_membership(store):
    mid = store.record_match(_a_match())
    store.record_heroes(mid, _slots([(1, 1, "a"), (1, 2, "b"), (1, 3, "c"),
                                     (2, 1, "m"), (2, 2, "n"), (2, 3, "o")]))
    store.finalise_summary(mid, winning_trio=2, blue_trio=1, outcome_source="observed")
    rows = store.matches_for_fit()
    assert {r[1] for r in rows} == {2}
    assert {r[-1] for r in rows} == {"a", "b", "c", "m", "n", "o"}


def test_an_unrepresentable_row_is_excluded_from_the_fit(store):
    mid = store.record_match(_a_match())
    store.mark_unrepresentable(mid)
    assert store.matches_for_fit() == []


def test_upsert_synced_stores_no_blue_trio(store):
    store.upsert_synced({
        "natural_key": "nk-pulled", "source": "spectate",
        "captured_at": "2026-08-01T00:00:00Z", "winning_trio": 1,
        "heroes": [{"trio": 1, "slot": i, "hero_slug": s} for i, s in enumerate("abc", 1)]
                + [{"trio": 2, "slot": i, "hero_slug": s} for i, s in enumerate("mno", 1)],
    })
    assert store.blue_trio_for("nk-pulled") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/games/afk_journey/services/solstice/test_store_trios.py -v`
Expected: FAIL - `HeroSlot` has no field `trio`.

- [ ] **Step 3: Implement**

- `HeroSlot.side: str` becomes `trio: int`. `_HERO_COLS[0]` becomes `"trio"`. The `ON CONFLICT(match_id,side,slot)` in `record_heroes` becomes `ON CONFLICT(match_id,trio,slot)`.
- `record_heroes` calls `assert_canonical([...])` on the full incoming set BEFORE opening the connection. This is the validated write boundary; it is the only place the ordering rule is enforced, because SQLite cannot express it.
- **`MatchRecord` loses `outcome`, `left_rating`, `right_rating`, `left_rank` and
  `right_rank`, and gains NOTHING in their place.** This is deliberate and it is forced by
  the existing order: `_record_summary` inserts the match row BEFORE the heroes are read
  (`solstice_clash.py:590-611`), so at insert time no trio exists to number, and any
  trio-relative field on `MatchRecord` would have to be written blind. `left_player` and
  `right_player` stay on it - they belong to no trio and need no ordering.

- **One new call carries everything trio-relative, after the heroes are known:**

  ```python
  def finalise_summary(
      self,
      match_id: int,
      *,
      winning_trio: int | None,
      blue_trio: int | None,
      trio_1_rating: int | None = None,
      trio_2_rating: int | None = None,
      trio_1_rank: int | None = None,
      trio_2_rank: int | None = None,
      predicted_trio_1: float | None = None,
      outcome_source: str | None = None,
  ) -> None:
      """Write every trio-relative value at once, and close the row.

      One call rather than five setters, because these values are only jointly
      meaningful: a winner without the trios it points at, or a rating attached to a
      trio number that a later call disagrees with, is defect 1476 rebuilt from
      correct-looking parts. It also sets canonical_state, so a row this method has
      not touched is visibly unfinished rather than silently NULL.
      """
  ```

  It asserts `match_hero` for this match already satisfies `assert_canonical`, that
  `winning_trio` and `blue_trio` each name a trio that exists, and then sets
  `canonical_state='canonical'` in the same statement. `set_outcome` (:602) is replaced by
  it, not kept alongside. **Grep for every other caller of `set_outcome` and of the removed
  `MatchRecord` fields before finishing this task** - a dangling caller fails at runtime, not
  at import.

- `mark_unrepresentable(match_id)` sets `canonical_state='unrepresentable'` for a read that
  could not form two complete trios. **Every recording path must end in one of these two
  calls**, or new rows land with `canonical_state IS NULL`, which the Task 7 predicate reads
  as "the reshape has not run" - re-running the migration on every launch forever.
- `record_prediction` takes `predicted_trio_1` instead of `predicted_left`.
- `scored_predictions` becomes `SELECT predicted_trio_1, winning_trio, predicted_source, predicted_locked FROM match WHERE predicted_trio_1 IS NOT NULL AND winning_trio IS NOT NULL AND superseded_by IS NULL AND canonical_state='canonical'`. Note the deliberate behaviour change named in the spec: synced rows now score too, because a pooled prediction is another contributor's call and scoring it is meaningful.
- `matches_for_fit` selects `m.id, m.winning_trio, m.theme_id, m.event_id, m.trio_1_rating, m.trio_2_rating, m.blue_trio, h.trio, h.hero_slug`, filtered on `m.winning_trio IS NOT NULL AND m.canonical_state='canonical' AND h.hero_slug IS NOT NULL AND m.superseded_by IS NULL`, ordered `m.id, h.trio, h.slot`. `blue_trio` is carried because Task 10 needs it and nothing else does.
- `finalise_identity` (:507) currently builds `sides = {"left": [], "right": []}` and calls `comps_key(event_slug, sides["left"], sides["right"])`. It becomes trio-grouped; `comps_key` still takes two lists and is orientation-free, so passing `(trio_1, trio_2)` produces the identical key. Verify that on a real row rather than assuming it.
- `record_odds` / `odds_for` move to `trio_1_pool` etc.
- `pushable_matches` emits the v5 shape: `winning_trio`, `trio_N_*`, `predicted_trio_1`, heroes with `trio`. No `side`, no `outcome`, no player names.
- `upsert_synced` (:1089) reads the v5 pull shape, writes `blue_trio = NULL`, and sets `comps_key` from the trios (keep the existing SC-41 backstop logic - it is why 50 pulled rows were keyless).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/games/afk_journey/services/solstice/ -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uvx ruff check --fix <changed files>
uvx ruff format <changed files>
git add <changed files>
git commit -m "feat: store reads and writes trios

record_heroes is the validated write boundary and calls assert_canonical before
any connection is opened. scored_predictions now scores pooled rows too - a
deliberate behaviour change: a pooled prediction is another contributor's call
and scoring it is meaningful.

matches_for_fit carries blue_trio, which only the intercept exclusion needs."
```

---

### Task 9: Wire the resolver into recording (spec Parts 1, 2, 3, 4)

**Repo:** `adbautoplayer`

**Files:**
- Modify: `.../mixins/solstice_clash.py` - `merge_screens` call site (:866), `_last_draft_reads` clear (:912), the abandoned-locked path (:814), `_record_summary` (:1187-1196, :1275-1276, :1344), the `record_heroes` call at :603
- Test: `.../tests/games/afk_journey/mixins/test_solstice_orientation.py`

**Interfaces:**
- Consumes: `orient.resolve`, `orient.Orientation` (committed, `4512ffb4`); `canon.canonical_trios`; `store.record_heroes` taking `HeroSlot(trio=...)` (Task 8).
- Produces: nothing later tasks depend on.

**The hard constraint applies here more than anywhere:** this task touches the draft path's neighbours. Capturing the merged trios inside the locked-read block and clearing carried state on exit paths are both behaviour-neutral - they retain and release values without reading, computing or displaying anything differently. If a change would alter what the odds, overlay, coloured log or auto-bet do, it is out of scope and the plan is wrong.

- [ ] **Step 1: Write the failing test**

```python
# tests/games/afk_journey/mixins/test_solstice_orientation.py
"""Recording resolves orientation from the trios themselves, never from a banner,
a tint, a name or a panel position."""
import pytest

from adb_auto_player.games.afk_journey.services.solstice.orient import Orientation, resolve


def test_direct_orientation_scores_five_of_five():
    r = resolve(panel_top={"a", "b", "c"}, panel_bottom={"m", "n"},
                draft_blue={"a", "b", "c"}, draft_red={"m", "n"})
    assert r.orientation is Orientation.DIRECT
    assert r.margin >= 2


def test_one_misread_still_resolves():
    r = resolve(panel_top={"a", "b", "X"}, panel_bottom={"m", "n"},
                draft_blue={"a", "b", "c"}, draft_red={"m", "n"})
    assert r.orientation is Orientation.DIRECT


def test_contradictory_evidence_refuses():
    r = resolve(panel_top={"a", "b", "m"}, panel_bottom={"c", "n"},
                draft_blue={"a", "b", "c"}, draft_red={"m", "n"})
    assert r.orientation is Orientation.UNRESOLVED


def test_an_unresolved_read_records_no_blue_trio(bot, store):
    bot._pending_draft_trios = ({"a", "b", "c"}, {"m", "n"})
    bot._record_summary(_summary(top=["a", "b", "m"], bottom=["c", "n", "o"], winner="top"))
    assert store.last_match()["blue_trio"] is None


def test_an_unresolved_read_still_records_the_winner(bot, store):
    """Refusing an orientation must not throw away the outcome - the trios and the
    winner are the only rock-solid facts on that screen, and they need no side."""
    bot._pending_draft_trios = ({"a", "b", "c"}, {"m", "n"})
    bot._record_summary(_summary(top=["a", "b", "m"], bottom=["c", "n", "o"], winner="top"))
    assert store.last_match()["winning_trio"] is not None


def test_a_mid_match_join_after_a_draw_does_not_inherit_the_last_prediction(bot, store):
    """The stale-carryover bug. _pending_prediction survived SC-10 and SC-03 because
    it was cleared only inside a successful _record_summary."""
    bot._pending_prediction = 0.8
    bot._draft_ratings = (100, 200)
    bot._pending_draft_trios = ({"a", "b", "c"}, {"m", "n"})
    bot._abandon_match(reason="SC-10")
    assert bot._pending_prediction is None
    assert bot._draft_ratings is None
    assert bot._pending_draft_trios is None


def test_panels_matching_neither_carried_trio_refuse_the_carried_prediction(bot, store):
    bot._pending_prediction = 0.8
    bot._pending_draft_trios = ({"a", "b", "c"}, {"m", "n"})
    bot._record_summary(_summary(top=["x", "y", "z"], bottom=["p", "q", "r"], winner="top"))
    assert store.last_match()["predicted_trio_1"] is None


def test_the_summary_frame_is_saved_for_every_match(bot, tmp_path, frame_capture_on):
    bot._record_summary(_summary(top=["a", "b", "c"], bottom=["m", "n", "o"], winner="top"))
    assert list(tmp_path.glob("summary-*.png"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/games/afk_journey/mixins/test_solstice_orientation.py -v`
Expected: FAIL - `_pending_draft_trios` does not exist.

- [ ] **Step 3: Implement Part 2 - carry the merged trios**

At `solstice_clash.py:866`, `merge_screens(draft_reads, locked)` already builds the complete draft-oriented trios `predicted_left` was computed from. Store them on `self._pending_draft_trios` the same way `_pending_prediction` is carried, immediately after the merge and before the `_last_draft_reads` clear at :912.

Anchoring on the merged set rather than a re-read frame makes the scoring self-consistent by construction: the same six heroes the prediction used. When the locked read was abandoned (`_last_draft_reads = []` at :814), fall back to re-reading the saved draft frame - five heroes still resolve at 5-vs-0.

- [ ] **Step 4: Implement Part 1 - resolve at record time**

In `_record_summary`, before writing heroes: call `resolve(panel_top, panel_bottom, draft_blue, draft_red)`. On `DIRECT`/`SWAPPED`, compute `blue_trio` from which canonical trio the draft-blue set forms. On `UNRESOLVED`, pass `blue_trio=None` and omit the carried prediction and ratings - but still pass `winning_trio`, which needs no orientation.

Persist all of it through the single `finalise_summary(...)` call Task 8 defines, after
`record_heroes`. There is no separate setter for `blue_trio`, deliberately: the winner, the
pointer and the measurements are only jointly meaningful, and `finalise_summary` is also
what sets `canonical_state`. A recording path that computes `blue_trio` without calling it
leaves the row unfinished and re-triggers the migration on the next launch.

A read that cannot form two complete trios calls `mark_unrepresentable(match_id)` instead.
**There are TWO recording paths, and the plan named only one.** Besides `_record_summary`,
the `compete_summary` path at `solstice_clash.py:590-620` builds a `MatchRecord` with
`outcome` and `outcome_source`, writes `HeroSlot(side=...)`, and after `record_heroes` calls
only `finalise_identity`. Left alone it fails on the removed signatures - or worse, if it
happens to construct successfully, leaves `canonical_state IS NULL`, which the Task 7
predicate reads as "the reshape has not run" and re-runs the migration on every launch.

Convert it in this task: `HeroSlot(trio=...)`, no `outcome` on the `MatchRecord`, and a
`finalise_summary(...)` call after `record_heroes`. This path has no draft to anchor against
- it is a compete result read cold - so it passes `blue_trio=None` unless a carried draft
trio set is present for that match.

**Then walk every exit path of BOTH paths** - including the early returns - and confirm each
ends in `finalise_summary` or `mark_unrepresentable`. Grep for `MatchRecord(` and
`HeroSlot(` across the whole client to be sure there is no third.

When the panel tint is unreadable, do NOT fall through to `_winner_by_colour` or header OCR. Those read the banner, whose geometry is the thing on trial. Record unresolved instead.

- [ ] **Step 5: Implement Part 3 - save the summary frame**

The frame is currently discarded at :1187-1196. Gate on the existing frame-capture setting, reuse the frame already in memory, write `summary-pending-<timestamp>.png` and rename to `summary-<match_id>.png` once `record_match` returns - the same claim pattern the draft frames use. Save EVERY summary frame, not only unresolved ones: 1476 looked successfully resolved to the code that recorded it. Roughly 2.4 MB each.

Log the raw read at `[SC-75]`: both trios, the winner, the resolution and its margin.

- [ ] **Step 6: Implement Part 4 - the stale-carryover fix**

`_pending_prediction` and `_draft_ratings` are cleared only inside `_record_summary` at :1275-1276, so after a draw (`SC-10`) or an `SC-03` timeout they survive and a following mid-match join records the PREVIOUS match's prediction against a new match. Clear `_pending_prediction`, `_draft_ratings` and `_pending_draft_trios` on EVERY exit path from a match. The trio anchor doubles as the guard: when the panels match neither carried trio, refuse the carried prediction and ratings as well as the orientation.

- [ ] **Step 7: Run the full AFK Journey suite**

Run: `uv run pytest tests/games/afk_journey/ -v 2>&1 | tail -30`
Expected: all pass. Two known traps from earlier work in this area: changing `format_pick` without updating `test_draftlog.py`, and adding a required parameter that broke 13 friendly-fire tests. If either suite fails, fix it in this task - a failing test is never noted for later.

- [ ] **Step 8: Commit**

```bash
uvx ruff check --fix <changed files>
uvx ruff format <changed files>
git add <changed files>
git commit -m "feat: resolve orientation from the trios at record time

The resolver scores BOTH panels jointly against the merged draft trios, so a
single misread gives 4-vs-1 rather than a 1-vs-1 tie. On refusal we record the
trios and the winner - which need no orientation - and decline only blue_trio and
the carried prediction.

Also fixes the stale carryover: _pending_prediction and _draft_ratings were
cleared only on the success path, so a draw or a timeout left them to be recorded
against the NEXT match.

Every summary frame is now saved. 1476 looked successfully resolved to the code
that recorded it, so we cannot know in advance which frames will matter."
```

---

### Task 10: The fit - trio membership and the intercept exclusion (spec Part 6)

**Repo:** `adbautoplayer`

**Files:**
- Modify: `.../solstice/odds.py:249-262` (feature construction), `load_matches`
- Test: `.../tests/games/afk_journey/services/solstice/test_odds_intercept.py`

**Interfaces:**
- Consumes: `matches_for_fit()` from Task 8, whose tuple now ends `(..., blue_trio, hero_trio, hero_slug)`.
- Produces: nothing later tasks depend on.

**This is the one part of the plan that touches the draft-time fit** (`store.py:787` -> `odds.py:818` -> `solstice_clash.py:1901`). The change is confined to how pooled rows contribute to a single column. Local rows are untouched, so a fit with no pooled data must be bit-identical to today's - and that is a test, not a hope.

- [ ] **Step 1: Write the failing test**

The real interface is `design(matches, theme_id=None, siblings=()) -> (x, y, w, heroes,
players)` - five values, at `odds.py:210`. There is no `build_design`, and the third value
is the WEIGHT vector, not coefficients. The `Match` dataclass at `odds.py:138` has no
`blue_trio`, so Step 4 must add one.

```python
# tests/games/afk_journey/services/solstice/test_odds_intercept.py
"""The encoding is ANTISYMMETRIC, so orientation is free for hero terms and the
rating gap: flip a row and every term negates while y becomes 1-y, and since
sigma(-x.b) = 1 - sigma(x.b) the likelihood contribution is identical.

The INTERCEPT is the sole exception - its column is 1.0 regardless of orientation,
which is exactly why it can learn the 56.0% first-pick advantage, and exactly why a
pooled row with an arbitrary orientation would corrupt it.
"""
import numpy as np

from adb_auto_player.games.afk_journey.services.solstice.odds import Match, design, fit


def _row(blue_trio, left_won=True):
    return Match(left=("a", "b", "c"), right=("m", "n", "o"), left_won=left_won,
                 theme_id=1, left_rating=100, right_rating=200, blue_trio=blue_trio)


def _flipped(m):
    return Match(left=m.right, right=m.left, left_won=not m.left_won, theme_id=m.theme_id,
                 left_rating=m.right_rating, right_rating=m.left_rating,
                 blue_trio=m.blue_trio)


def test_a_pooled_row_has_a_zero_intercept():
    x, _y, _w, _h, _p = design([_row(blue_trio=None)], theme_id=1)
    assert x[0][0] == 0.0


def test_a_local_row_has_a_one_intercept():
    x, _y, _w, _h, _p = design([_row(blue_trio=1)], theme_id=1)
    assert x[0][0] == 1.0


def test_hero_terms_are_identical_for_a_pooled_row():
    """Only the intercept differs. Every pooled comp still trains the hero strengths,
    which is the entire point of pooling."""
    local = design([_row(blue_trio=1)], theme_id=1)[0][0]
    pooled = design([_row(blue_trio=None)], theme_id=1)[0][0]
    assert list(local[1:]) == list(pooled[1:])


def test_flipping_a_row_leaves_the_likelihood_unchanged():
    """Antisymmetry, measured rather than asserted. Uses a pooled row so the intercept
    is zero for both and cannot mask the result."""
    m = _row(blue_trio=None)
    x1, y1, _w, _h, _p = design([m], theme_id=1)
    x2, y2, _w, _h, _p = design([_flipped(m)], theme_id=1)
    beta = np.arange(1, x1.shape[1] + 1) * 0.1
    ll = lambda x, y: (y * np.log(1 / (1 + np.exp(-x @ beta)))
                       + (1 - y) * np.log(1 - 1 / (1 + np.exp(-x @ beta))))
    assert abs(ll(x1[0], y1[0]) - ll(x2[0], y2[0])) < 1e-12


def test_a_fit_with_no_pooled_rows_is_unchanged(golden_local_matches, golden_coefficients):
    """The hard requirement: this must not move the model for a user with no pool."""
    result = fit(golden_local_matches, theme_id=golden_local_matches[0].theme_id)
    np.testing.assert_allclose(result.beta, golden_coefficients, rtol=1e-10)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/games/afk_journey/services/solstice/test_odds_intercept.py -v`
Expected: FAIL - the intercept column is unconditionally 1.0.

- [ ] **Step 3: Capture the golden baseline BEFORE changing anything**

Fit the current model on the current local matches and save BOTH the `Match` inputs and
the resulting `Fit.beta` to `tests/.../data/golden_fit.json`, exposed as the
`golden_local_matches` and `golden_coefficients` fixtures. Capture it with the CURRENT code,
before touching `odds.py` - after the edit the baseline is unrecoverable, and without it the
last test cannot prove the change is inert for local-only data, which is the whole reason
the change is acceptable.

- [ ] **Step 4: Implement**

- `load_matches` groups by `h.trio` instead of `h.side`; `y = (winning_trio == 1)`; heroes in trio 1 get `+1` and trio 2 get `-1`; the rating gap becomes `trio_1_rating - trio_2_rating`.
- Add `blue_trio: int | None = None` to the `Match` dataclass at `odds.py:138`, and populate it in `load_matches` from the column Task 8 added to `matches_for_fit`. Without this the next bullet references an undefined name.
- `odds.py:249`, the intercept column, becomes `1.0 if match.blue_trio is not None else 0.0`. `blue_trio IS NOT NULL` is the single condition for contributing to the intercept - the Part 6 distinction expressed as data rather than as branching logic.
- Nothing else changes. Hero terms and the rating gap are correct as they stand for a pooled row, because the encoding is antisymmetric.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/games/afk_journey/services/solstice/ -v`
Expected: all pass, including the golden-coefficient test.

- [ ] **Step 6: Commit**

```bash
uvx ruff check --fix <changed files>
uvx ruff format <changed files>
git add <changed files>
git commit -m "feat: pooled rows train the heroes but not the intercept

The encoding is antisymmetric, so a pooled row's arbitrary orientation is free
for hero terms and the rating gap. The intercept is the sole exception - its
column is 1.0 whatever the orientation, which is how it learned the 56.0%
first-pick advantage and why a pooled row would corrupt it.

blue_trio IS NOT NULL is the single condition, so the distinction is data rather
than a branch. A golden-coefficient test proves a local-only fit is unchanged."
```

---

### Task 11: Reconcile the pairs already on disk (spec Part 7)

**Repo:** `adbautoplayer`

**Files:**
- Modify: `data/solstice_clash/migrate.py` (a one-off pass beside `_backfill_comps_key`)
- Test: `.../tests/games/afk_journey/services/solstice/test_reconcile_pairs.py`

**Interfaces:**
- Consumes: the schema from Task 7.
- Produces: nothing later tasks depend on.

Four local/synced pairs currently share a `comps_key` with neither marked superseded, so three are counted twice in the fit. They arose from pulling a match another contributor pushed and then spectating it ourselves.

- [ ] **Step 1: Write the failing test**

```python
# tests/games/afk_journey/services/solstice/test_reconcile_pairs.py

def test_a_local_and_synced_pair_inside_the_window_is_reconciled(db, reconcile):
    _pair(db, key="ck1", local_at="2026-08-01T10:00:00Z", synced_at="2026-08-01T10:01:00Z")
    reconcile(db)
    assert _superseded(db, origin="synced") == 1
    assert _superseded(db, origin="local") == 0   # the local row is draft-anchored; it wins


def test_a_genuine_rematch_is_left_alone(db, reconcile):
    """Ids 1 and 45 share a comps_key and are 31.6 hours apart. A reconciliation that
    ignored the window would destroy a real rematch."""
    _pair(db, key="ck2", local_at="2026-08-01T10:00:00Z", synced_at="2026-08-02T17:36:00Z")
    reconcile(db)
    assert _superseded(db) == 0


def test_the_window_boundary_is_two_minutes(db, reconcile):
    _pair(db, key="ck3", local_at="2026-08-01T10:00:00Z", synced_at="2026-08-01T10:02:01Z")
    reconcile(db)
    assert _superseded(db) == 0


def test_reconciliation_is_idempotent(db, reconcile):
    _pair(db, key="ck4", local_at="2026-08-01T10:00:00Z", synced_at="2026-08-01T10:00:30Z")
    reconcile(db)
    first = _rows(db)
    reconcile(db)
    assert _rows(db) == first
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL - no reconcile pass exists.

- [ ] **Step 3: Implement**

For each `comps_key` held by more than one ROW IN THE LOCAL DATABASE - not "more than one local row", which is zero groups, since every real pair is one `local` plus one `synced` - group rows whose `captured_at` fall within ±120 seconds. Within a group keep the `origin='local'` row and set `superseded_by` on the synced copies. Rows outside the window are different matches and are left alone.

- [ ] **Step 4: Run to verify it passes, then verify against a copy of the real database**

```bash
cp ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite /tmp/heroes-recon.sqlite
python3 data/solstice_clash/migrate.py /tmp/heroes-recon.sqlite
sqlite3 /tmp/heroes-recon.sqlite \
  "SELECT count(*) FROM match WHERE superseded_by IS NOT NULL;
   SELECT id, captured_at FROM match WHERE id IN (1, 45);"
```

Expected: three newly superseded rows; ids 1 and 45 both still un-superseded.

- [ ] **Step 5: Commit**

```bash
git add <changed files>
git commit -m "feat: reconcile the local/synced pairs already on disk

Three of four pairs were counted twice in the fit. The SC-41 backstop prevents new
ones but nothing reconciled those that exist. The +/-2 minute window is the test,
not comps_key alone - ids 1 and 45 share a key and are 31.6 hours apart, a genuine
rematch that a key-only reconciliation would have destroyed."
```

---

### Task 12: Client sync speaks version 5

**Repo:** `adbautoplayer`

**Files:**
- Modify: `.../solstice/sync.py:206` (push `schema_version`), `:264` (the pull GET)
- Test: `.../tests/games/afk_journey/services/solstice/test_sync_v5.py`

**Interfaces:**
- Consumes: `pushable_matches` / `upsert_synced` in the v5 shape (Task 8); the server from Phase A.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Write the failing test**

```python
def test_push_declares_version_5(sync, captured_requests):
    sync.push()
    assert captured_requests[-1].json["schema_version"] == 5


def test_pull_asks_for_version_5(sync, captured_requests):
    sync.pull()
    assert "schema_version=5" in captured_requests[-1].url


def test_a_pulled_row_lands_with_no_blue_trio(sync, store, fake_pull_response):
    sync.pull()
    assert store.blue_trio_for("nk-from-pool") is None
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL - version 4 is sent, and the GET carries no `schema_version`.

- [ ] **Step 3: Implement**

`"schema_version": 5` in the push payload. Append `&schema_version=5` to the pull URL at :264 - it currently sends none at all, which is why the server has to treat an absent version as 4.

- [ ] **Step 4: Run to verify it passes, then verify against the live server**

Phase A must already be deployed. Push one real match and confirm it is accepted, then pull and confirm the rows land with `blue_trio IS NULL`.

- [ ] **Step 5: Commit**

```bash
git add <changed files>
git commit -m "feat: client speaks schema version 5

Push declares 5; pull asks for it explicitly. The GET previously sent no version
at all, which is why an absent version has to mean 4 on the server."
```

---

### Task 13: Scripts, docs and the changelog

**Repo:** `adbautoplayer`

**Files:**
- Modify: `src-tauri/src-python/scripts/solstice_side_audit.py:65`, `solstice_crowd_agreement.py:74`, `solstice_frame_side_audit.py:260` and its `--apply` path and lines 722-724, `solstice_walkforward.py`, `dry_run_draft_log.py`
- Modify: `CHANGELOG.md` (repo root), `docs/solstice-clash/` schema notes
- Test: run each script against a copy of the migrated database

These do not ship to a user, so they cannot break a contributor's install - but they are how every question in this design was answered, and a reshape that silently breaks all five leaves no way to check its own work.

- [ ] **Step 1: `solstice_frame_side_audit.py` - remove `--apply`, keep classify**

The `--apply` path mutated `match_hero.side` and `match.outcome` (:713). Those columns no longer exist and the repair now happens inside the migration, so `--apply` is deleted rather than ported. Its read at :260 must come from `legacy_side_snapshot`, which is where the pre-migration sides now live. Also delete the documented assumption at lines 722-724 that summary-header names are "side-correct" - 1476 contradicts it.

- [ ] **Step 2: Rewrite the other four onto the new columns**

`solstice_side_audit.py` and `solstice_walkforward.py` onto `trio` / `winning_trio`; `solstice_crowd_agreement.py` onto `predicted_trio_1` / `winning_trio`. Check `dry_run_draft_log.py` - it is draft-side only and is likely unaffected, but confirm rather than assume.

- [ ] **Step 3: Run each against a migrated copy**

```bash
cp ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite /tmp/heroes-scripts.sqlite
python3 data/solstice_clash/migrate.py /tmp/heroes-scripts.sqlite
for s in solstice_side_audit solstice_crowd_agreement solstice_walkforward; do
  uv run python src-tauri/src-python/scripts/$s.py --db /tmp/heroes-scripts.sqlite \
    > /tmp/script-$s-$(date +%H%M%S).log 2>&1 || echo "FAILED: $s"
done
```

Every one must exit 0 and produce plausible output. A script that runs but prints zero rows is a failure, not a pass.

- [ ] **Step 4: Update the docs and the changelog**

`CHANGELOG.md` at the repo root, in lockstep. Update `docs/solstice-clash/` wherever it describes `side` or `outcome`. Bump the app version - it is the deployment-propagation marker the operator reads to confirm the new code actually landed.

- [ ] **Step 5: Full suite, then commit**

```bash
cd src-tauri && uv run pytest 2>&1 | tail -20
```

Ask the operator before running the full suite if it takes more than a couple of minutes.

```bash
git add <changed files> CHANGELOG.md
git commit -m "chore: scripts, docs and changelog onto trios

solstice_frame_side_audit loses --apply entirely - the repair happens inside the
migration now, and the columns it mutated are gone. Its classify path reads
legacy_side_snapshot, which is where the pre-migration sides live. Its documented
assumption that summary-header names are side-correct is deleted; 1476 is the
counterexample."
```

---

### Task 14: Build, verify, ship

**Repo:** `adbautoplayer`

- [ ] **Step 1: Confirm Phase A is live** before building anything. A v5 client against a v4-only server fails every push.

- [ ] **Step 2: Build the RPM locally and install it**

Follow the existing release procedure. `gh` in this repo targets the FORK only because `gh repo set-default WebDevBar/AdbAutoPlayer` has been run - confirm with `gh repo set-default --view` before any release command, and never believe a `gh` "not found" in a fork without checking the repo first.

- [ ] **Step 3: Verify on a real match, in order**

1. Launch and confirm `[SC-93]` reports the schema upgrade, once, and not again on the second launch.
2. Spectate one match end to end. The coloured live log must show BLUE on the left and RED on the right, picks 1-6 in order, with the 6th announced as `RED 6:` - unchanged from today. This is the hard constraint; if anything about the live path looks different, stop.
3. Confirm the bet went on the side the log named.
4. After the summary, check the recorded row: `winning_trio`, `blue_trio`, and `predicted_trio_1` all present and mutually consistent.
5. Confirm `summary-<match_id>.png` was written.
6. Push and pull, and confirm both are accepted.

- [ ] **Step 4: Report the counts to the operator** - matches migrated, `blue_trio IS NULL` count, superseded pairs, and the model's accuracy before and after. Then stop and wait; the Windows build for the contributor is a separate decision.

---

## Self-Review

**Spec coverage:**

| spec part | task |
|---|---|
| 1 - orientation from the trios | 9 (`orient.py` already committed) |
| 2 - carry draft trios to record time | 9 |
| 3 - save the summary frame | 9 |
| 4 - stale carryover | 9 |
| 4b - one shape, both databases | 6, 7, 8 |
| 4b - constraint set | 6 (client boundary), 2 (server schema) |
| 4b - every side-relative column mapped | 3 (server), 7 (client) |
| 4b - `legacy_side_snapshot` | 7 |
| 4b - self-migrating, `canonical_state` predicate | 7 |
| 4b - dropped columns leave `ADD_COLUMNS` | 7 |
| 5 - server drops sides (`0007`) | 2, 3 |
| 5 - dual-version API | 4, 12 |
| 6 - pulled rows have no sides | 8, 10 |
| 7 - reconciling existing pairs | 11 |
| 8 - the pool's ~6 rows | 3 (inside `0007`) |
| ordering - server before client | 5 before 6; Task 14 Step 1 |
| ordering - one binary | Phase B ships as one release; Task 14 |
| script consumers | 13 |

**Known gaps the executing engineer must resolve rather than guess:**

1. **`canonicalise_row` is written twice** - once in `migrations/versions/_0007_helpers.py` (Task 3) and once for the client migration (Task 7). The repos do not share code, so duplication across them is unavoidable; duplication WITHIN a repo is not. In the server repo, move it to `app/canon.py` before Task 4 imports it, so a runtime path never depends on a migration file.
2. **Task 4's v4 ingest uses `mirrored=False`, not `None`.** A live v4 client reports its own draft-anchored orientation and its `left` IS its blue - unlike a historic row, whose stored side came off the summary panels. Getting this backwards would NULL every incoming prediction.
3. **The sidecar must be packaged** (Task 7 Step 1). If `resource_file` cannot find it, the migration NULLs every `blue_trio` - safe, but wrong and invisible. Assert the sidecar loaded and log its row count.
4. **The golden coefficients (Task 10 Step 3) must be captured before any `odds.py` edit.** After the edit the baseline is unrecoverable.
5. **The sidecar must be re-keyed to `comps_key` before `0007` can use it** (Task 3 Step 1). `0006` rewrote every surviving `natural_key`, so the raw sidecar joins to nothing and would silently null the whole pool's draft-relative values. Read the `dropped_without_comps_key` count before proceeding.
6. **A `mirrored` verdict does not by itself mean invert.** `0006` already swapped the rows it reached, and those now map naively. Invert only where the verdict is `mirrored` AND `match_merge_log.orientation_verdict` is not `'CORRECTED'`. This is what makes the target set six rows rather than seventy-six, and getting it wrong re-corrupts about seventy repaired rows.
7. **Task 5 Step 3 disables the ROUTE, not the container.** Schema and application must change together, and Dokploy cannot create a container without starting it - so stopping the container still leaves a window. Removing the domain closes it completely. Capture the exact domain settings before removing them; both certificate toggles stay OFF.
8. **The operator's database is `~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite`** - used on a copy, in Tasks 3, 7, 11 and 13. The checked-in `data/solstice_clash/heroes.sqlite` is a seed with zero matches and no `comps_key` column.
11. **`wire_left_trio` exists only so version-4 pull is byte-compatible.** It is captured in `0007` before the sides are dropped and read by `to_v4_wire` alone. Nothing in the v5 path, the ingest path or the fit may read it, and it is deleted when version 4 support is dropped.
10. **Every recording path must end in `finalise_summary` or `mark_unrepresentable`.** A row left with `canonical_state IS NULL` is read by the Task 7 predicate as "the reshape has not run", so the migration re-runs on every launch.
9. **`views.sql` is part of the schema change, not an afterthought.** `hero_matchup` reads `outcome` and `side`, and `_apply` builds it before the reshape - so both a fresh install and the second launch of an upgraded one fail at `migrate.py:285` unless the view is rewritten and its execution moved after `_reshape_to_trios`.
