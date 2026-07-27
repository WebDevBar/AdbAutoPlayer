# Passive Collection (Mode B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A mode that records Solstice Clash match data while the user plays competitive matches themselves, by watching for the post-match details screen and never touching the device.

**Architecture:** One new pure module (`details_screen.py`) holding a reusable screen predicate, one new command on the existing `SolsticeClashMixin`, and one added value in the store's allowed-source set. Everything else already exists.

**Spec:** `docs/superpowers/specs/2026-07-27-passive-collection-mode.md` (approved, 3 rounds plus an independent review)

## Global Constraints

- **The mode must never touch the device.** No tap, swipe, hold, key event, navigation, or popup dismissal. `get_screenshot()` is the only device call permitted. The user is playing a ranked match.
- **It must NOT call `start_up()`.** That calls `_set_device_resolution()` and can call `start_game()` - resizing the display and launching the app under a live match. Check the resolution on the FIRST POLLED frame and refuse instead - see Task 3, which explains why a separate screenshot for the check breaks the tests.
- **Exact string matching only**, never substring. `"ally" in text` accepts "Really" and "Rally". This project already replaced fuzzy hero matching after `SILVER` scored 0.833 against `SILVEN`.
- **Duplicates are worse than misses.** Each duplicate is another vote in the model; a miss costs one row.
- Source paths: `src-tauri/src-python/adb_auto_player/games/afk_journey/`. Tests: `src-tauri/src-python/tests/games/afk_journey/`.
- Log codes continue the existing `[SC-nn]` scheme: `SC-40` recorded (info), `SC-41` skipped (**debug** - it repeats every poll while a screen is up), `SC-42` wrong resolution, `SC-43` periodic heartbeat, `SC-44` details screen never appeared (Task 4), `SC-45` device connection lost, `SC-46` detector signals disagree. Existing codes are reused unchanged: `SC-30` sync failed, `SC-35` sync summary.
- **No test in this work may touch the real database, the network, or the device.** All three are reachable by default through property fallbacks (`_solstice_cfg` -> `solstice_db_path()`, which CREATES the user's live database) and through `SyncClient`, which has a baked-in key and would push synthetic fixture matches into the shared pool. Task 3's harness closes all three; do not weaken it.

---

### Task 0: Verify cross-observer key identity - BLOCKING, do this first

**No files. This is a measurement, and its answer can invalidate Task 3's dedupe story.**

`natural_key` keeps the two teams as an **ordered** `(left, right)` pair and the outcome as
`'left'`/`'right'` (`matchkey.py:29-33`). Mode B introduces a second class of observer for the
same physical match: today every row comes from spectating, but a compete row is recorded by one
of the two *players*.

`summary.py:131` documents that in spectate the Ally/Enemy panels "mean whichever side you bet on
and they flip between matches", and `_winner_by_panel_tint` maps the top panel to `'left'`. **If
panel order is observer-relative, then a compete recording and another contributor's spectate
recording of the same match produce mirrored sides, different keys, and the shared pool
double-counts that match** - one row saying left won, one saying right won, both counted as
independent evidence. That is worse than missing the match.

- [ ] **Step 1: Determine whether panel order is observer-relative.** Read the existing captures
      in `tests/games/afk_journey/services/solstice/data/` and, if they cannot settle it, capture
      the same match from both a player's and a spectator's view.

- [ ] **Step 2: Record the answer in the spec** either way, since the current text does not say.

- [ ] **Step 3: If order IS observer-relative, STOP and raise it.** The fix is to canonicalise
      `natural_key` over an unordered team pair, which changes the shared identity model, the
      server, and every already-adopted key. That is a decision, not an implementation detail, and
      it gets dramatically more expensive once compete rows exist in the pool. If order is
      absolute, note that and continue.

---

### Task 1: `is_details_screen()` - the reusable predicate

**Files:**
- Create: `.../services/solstice/details_screen.py`
- Test: `.../tests/games/afk_journey/services/solstice/test_details_screen.py`

**Interfaces:**
- Consumes: `TemplateMatcher.find_template_match` (a pure static method over numpy arrays) and an `OCRBackend`.
- Produces:
  - `is_details_screen(frame: np.ndarray, replay_template: np.ndarray, ocr: OCRBackend) -> bool`
  - `load_replay_template(template_dir: Path) -> np.ndarray`
  - constants `TAB_STRIP`, `TAB_LABELS`, `REPLAY_THRESHOLD`

**Takes a loaded template, not a `Game`.** `game_find_template_match` is a method that needs a
`Game` for `_load_image`, `template_dir` and `default_threshold`, so a predicate depending on it
cannot be tested without constructing one - and could in principle reach the device.
`TemplateMatcher.find_template_match(base_image, template_image, ...)` is a pure static over numpy
arrays, which is the same shape `vision.py` already uses for cell identification.

Deliberately **stateless and dedupe-free**: Mode A wants the predicate without the recording
policy.

- [ ] **Step 1: Write the failing test**

```python
"""The details-screen predicate.

Three candidates were measured and rejected before settling on these two signals;
see the spec. The rejected ones all looked correct on reasoning.
"""

import cv2
import pytest

from adb_auto_player.games.afk_journey.services.solstice.details_screen import (
    TAB_LABELS,
    is_details_screen,
)

DETAILS = ("summary_01.png", "summary_02.png", "longpress_ally1.png")
NOT_DETAILS = ("draft_selecting.png", "prematch_locked.png", "spectate.png",
               "spectate_draft.png", "spectate_prematch.png")


@pytest.mark.parametrize("name", DETAILS)
def test_accepts_every_details_screen(name, frames, read_frame, replay_template, ocr_backend):
    assert is_details_screen(read_frame(frames[name.removesuffix(".png")]),
                             replay_template, ocr_backend) is True


@pytest.mark.parametrize("name", NOT_DETAILS)
def test_rejects_every_other_screen(name, frames, read_frame, replay_template, ocr_backend):
    assert is_details_screen(read_frame(frames[name.removesuffix(".png")]),
                             replay_template, ocr_backend) is False


def test_a_popup_over_the_ally_tab_still_counts(frames, read_frame, replay_template, ocr_backend):
    """longpress_ally1 shows only 'Enemy' - it is still a details screen with a
    full set of data, so the label check is OR, not AND."""
    assert is_details_screen(read_frame(frames["longpress_ally1"]),
                             replay_template, ocr_backend) is True


def test_labels_are_matched_exactly_not_as_substrings():
    """'Really' and 'Rally' both pass a substring test. 'All In' is on the
    betting screen, two characters from 'Ally'."""
    for text in ("Really", "Rally", "Alliance", "All In", "AllIn"):
        assert text.strip().casefold() not in TAB_LABELS
    for text in ("Ally", " enemy ", "ENEMY"):
        assert text.strip().casefold() in TAB_LABELS


def test_it_never_touches_the_device():
    """The predicate takes a frame and callables. It has no device handle, so it
    cannot tap even by mistake."""
    import inspect

    params = inspect.signature(is_details_screen).parameters
    assert "self" not in params
    src = inspect.getsource(is_details_screen)
    for forbidden in ("tap(", "swipe(", "hold(", "press_back", "navigate"):
        assert forbidden not in src
```

Fixtures: `frames`, `read_frame` and `ocr_backend` already exist in the solstice `conftest.py`.
Add one more, `replay_template`, returning `load_replay_template(<templates dir>)` - no `Game`
instance is needed anywhere.

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_details_screen.py -v`
Expected: FAIL, `ModuleNotFoundError: ...solstice.details_screen`

- [ ] **Step 3: Implement**

```python
"""Is this frame the post-match details screen?

Pure, stateless, and free of any recording policy - Mode A wants this check
without Mode B's deduplication, and a predicate that could reach the device
would be unusable in a mode that must never touch it.

Two independent signals, because one template is a single point of failure: a
game update that restyles it would silently stop collection.
"""

from pathlib import Path

import numpy as np

from adb_auto_player.image_manipulation import IO
from adb_auto_player.models import ConfidenceValue
from adb_auto_player.ocr import OCRBackend
from adb_auto_player.template_matching import TemplateMatcher

REPLAY_TEMPLATE = Path("event/solstice_clash/details_replay.png")
# Measured 1.000 on all four details screens and <= 0.643 on fifteen others, so
# the default 90% is comfortably inside the gap.
REPLAY_THRESHOLD = ConfidenceValue("90%")

# The roster tab strip: both tabs, below the player-name header and left of the
# stat columns, so no other text is in frame. Absolute pixels on 1080x1920,
# matching how summary.py already addresses its OCR regions.
TAB_STRIP = (0, 350, 220, 1730)          # x0, y0, x1, y1
TAB_LABELS = frozenset({"ally", "enemy"})


def load_replay_template(template_dir: Path) -> np.ndarray:
    """Resolve the template path once, at mode start.

    IO.load_image caches globally, so this is not a performance measure - it is
    what keeps is_details_screen free of any path or directory knowledge, which
    is what makes it testable without a Game.
    """
    return IO.load_image(template_dir / REPLAY_TEMPLATE)


def is_details_screen(
    frame: np.ndarray, replay_template: np.ndarray, ocr: OCRBackend
) -> bool:
    if TemplateMatcher.find_template_match(
        base_image=frame,
        template_image=replay_template,
        threshold=REPLAY_THRESHOLD,
    ) is None:
        return False

    x0, y0, x1, y1 = TAB_STRIP
    blocks = ocr.detect_text_blocks(frame[y0:y1, x0:x1])
    # EXACT match on a whole block. A substring test accepts "Really" and
    # "Rally"; "All In" sits on the betting screen two characters away.
    return any(b.text.strip().casefold() in TAB_LABELS for b in blocks)
```

OCR rather than a template for the labels because the tabs are tinted by outcome - orange for the winning trio, blue for the losing one - so a template cut from an orange "Ally" would not match a blue one.

- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 2: Allow `compete_summary` as a source

**Files:**
- Modify: `.../services/solstice/store.py`
- Test: **append to the existing** `.../tests/games/afk_journey/services/solstice/test_store.py`

**Append, do not create a new file.** `tmp_db` is defined at `test_store.py:22`, not in the
solstice `conftest.py`, so pytest will not expose it to a sibling file - a new
`test_store_sources.py` would error on an unknown fixture. `pytest`, `MatchStore` and
`MatchRecord` are already imported at the top of `test_store.py` (lines 12-19), so appending
needs no new imports.

- [ ] **Step 1: Write the failing test**

```python
def test_compete_summary_is_an_allowed_source(tmp_db):
    """The store enforces _SOURCES deliberately, so an unlisted value fails
    before insert rather than persisting a typo."""
    store = MatchStore(tmp_db)
    mid = store.record_match(MatchRecord(
        source="compete_summary",
        captured_at="2026-07-25T12:00:00+00:00",
        outcome="left", outcome_source="observed",
    ))
    assert mid > 0


def test_an_unknown_source_is_still_rejected(tmp_db):
    with pytest.raises(ValueError):
        MatchStore(tmp_db).record_match(MatchRecord(
            source="comptee", captured_at="2026-07-25T12:00:00+00:00",
        ))
```

- [ ] **Step 2: Run to verify it fails** - `ValueError: invalid source: 'compete_summary'`

- [ ] **Step 3: Implement** - add `"compete_summary"` to `_SOURCES`, parallel to the existing `spectate_summary`.

- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Commit**

---

### Task 3: The mode

**Files:**
- Modify: `.../mixins/solstice_clash.py`
- Test: `.../tests/games/afk_journey/mixins/test_passive_collection.py`

**Interfaces:**
- Produces: command `SolsticeClashCollectCompete`, GUI label "Collect While Playing (Compete)".
- Consumes: `is_details_screen` / `load_replay_template` (Task 1), `source="compete_summary"` (Task 2).

**The test harness - build this first, the tests below all depend on it.** It follows the
existing `MockAFKJ` pattern in `tests/games/afk_journey/mixins/test_afkj_mixins.py`: subclass the
mixin, skip the real `__init__`, and stub only the device surface. `AFKJourneyBase.__init__`
opens configuration and a device, so it must not run.

```python
# tests/games/afk_journey/mixins/test_passive_collection.py
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from adb_auto_player.exceptions import GameActionFailedError
from adb_auto_player.games.afk_journey.mixins.solstice_clash import SolsticeClashMixin
from adb_auto_player.exceptions import GameTimeoutError
from adb_auto_player.games.afk_journey.services.solstice.details_screen import TAB_STRIP
from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
from adb_auto_player.games.afk_journey.services.solstice.summary import SummaryRead

# This file sits at tests/games/afk_journey/mixins/ - the same depth the solstice
# conftest documents, one directory across.
FRAMES = Path(__file__).parents[1] / "services" / "solstice" / "data"
REPO = Path(__file__).resolve().parents[6]   # parents[5] is src-tauri
SEED_DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"
TEMPLATES = (
    Path(__file__).resolve().parents[4]
    / "adb_auto_player" / "games" / "afk_journey" / "templates"
)


class FakeCompeteMode(SolsticeClashMixin):
    """Device-free. Every method that could reach ADB either serves a queued
    frame or appends to `device_actions`, so 'never touches the device' is a
    checkable assertion rather than a claim."""

    def __init__(self, db_path: Path, cfg, library, ocr):
        self._frames: list[np.ndarray] = []
        self.device_actions: list[str] = []
        self.start_up_calls = 0
        self._settings = MagicMock()
        self._db_path = db_path
        # ALL FOUR caches must be primed. These are read-only @property with no
        # setter (solstice_clash.py:634-656) - assigning self._store raises
        # AttributeError, so priming the *_cache attribute is the only seam.
        #
        # Priming _cfg_cache is not an optimisation, it is containment: the real
        # _solstice_cfg calls SolsticeConfig.load(solstice_db_path()) (:636), and
        # solstice_db_path() CREATES the user's live application database by
        # copying and scrubbing the seed if it is absent (paths.py:78-86). Leaving
        # it unprimed means the test suite reaches outside tmp_path and touches
        # ~/.local/share/AdbAutoPlayer no matter what db_path says. _lib_cache
        # must follow, because _solstice_library builds from _solstice_cfg.
        self._store_cache = MatchStore(db_path)
        self._cfg_cache = cfg
        self._lib_cache = library
        self._ocr_cache = ocr
        self._frame_size = (1920, 1080)     # numpy order: rows, cols

    # -- harness API ------------------------------------------------------
    def feed(self, *frames: np.ndarray) -> None:
        self._frames.extend(frames)

    def set_frame_size(self, width: int, height: int) -> None:
        self._frame_size = (height, width)

    # -- device surface, all stubbed --------------------------------------
    def get_screenshot(self) -> np.ndarray:
        frame = self._frames.pop(0) if self._frames else _blank()
        h, w = self._frame_size
        return frame if frame.shape[:2] == (h, w) else cv2.resize(frame, (w, h))

    def start_up(self, *a, **k) -> None:
        self.start_up_calls += 1

    def tap(self, *a, **k) -> None:
        self.device_actions.append("tap")

    def swipe(self, *a, **k) -> None:
        self.device_actions.append("swipe")

    def hold(self, *a, **k) -> None:
        self.device_actions.append("hold")

    def press_back(self, *a, **k) -> None:
        self.device_actions.append("press_back")

    @property
    def template_dir(self) -> Path:
        return TEMPLATES        # the real directory - templates are read-only


def _frame(name: str) -> np.ndarray:
    return cv2.imread(str(FRAMES / f"{name}.png"))


def _blank() -> np.ndarray:
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def details_frame(match: int = 1, heroes: int = 6) -> np.ndarray:
    """`match` selects between two committed details captures of DIFFERENT
    matches, so 'reopened the same one' and 'played a new one' are separable.
    `heroes=4` returns the mid-animation capture where two cells have not
    rendered - the partial-read case."""
    if heroes != 6:
        return _frame("summary_partial")
    return _frame("summary_01" if match == 1 else "summary_02")


def overworld_frame() -> np.ndarray:
    return _frame("overworld")


# There is deliberately no `broken_frame()` and no "frame with no winner" fixture.
# Both would be blank or junk images, and `is_details_screen` rejects those BEFORE
# `read_summary` is ever called - so the test would pass while exercising nothing.
# Those two cases are driven by patching `read_summary` instead; see below.


@pytest.fixture(autouse=True)
def _no_poll_sleep(monkeypatch):
    """POLL_SECONDS is 2.0 in production. Left alone, the tests below queue ~40
    polls between them and the file takes over a minute of pure sleeping."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    monkeypatch.setattr(mod, "POLL_SECONDS", 0.0)


# Session-scoped, mirroring the solstice conftest: IconLibrary.build decodes every
# hero icon and RapidOCR loads ONNX models, both of which take seconds. Rebuilding
# them per test would dominate the file's runtime. They are read-only here.
@pytest.fixture(scope="session")
def shared_cfg():
    from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig

    return SolsticeConfig.load(SEED_DB)


@pytest.fixture(scope="session")
def shared_library(shared_cfg):
    from adb_auto_player.games.afk_journey.mixins.solstice_clash import (
        SOLSTICE_ICON_DIR,
    )
    from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary

    # Same skip the solstice conftest's `library` fixture uses (conftest.py:96).
    # The icon set is extracted game data and is not in the repo, so on a machine
    # without it these tests must skip, not error.
    if not SOLSTICE_ICON_DIR.is_dir():
        pytest.skip(f"icon library not available at {SOLSTICE_ICON_DIR}")
    return IconLibrary.build(shared_cfg, SOLSTICE_ICON_DIR)


@pytest.fixture(scope="session")
def shared_ocr():
    from adb_auto_player.ocr import RapidOCRBackend

    return RapidOCRBackend()


@pytest.fixture
def mode(tmp_path, shared_cfg, shared_library, shared_ocr):
    import shutil

    db = tmp_path / "heroes.sqlite"
    shutil.copy(SEED_DB, db)   # a COPY: the tests write, and the seed is committed
    return FakeCompeteMode(db, shared_cfg, shared_library, shared_ocr)


@pytest.fixture
def db(mode) -> Path:
    """The database file, for asserting with SQL.

    `MatchStore` has no match_count / newest_match / audit_count - it exposes
    record_match, match_by_natural_key, heroes_for and the sync helpers, nothing
    that counts rows (store.py). Asserting in SQL is also the stronger test: it
    reads the table the feature must actually write, not a helper that could be
    wrong in the same direction as the code under test.
    """
    return mode._db_path


@pytest.fixture(autouse=True)
def sync(monkeypatch):
    """Counts pushes without a network or an `_sync` attribute.

    **autouse is a data-integrity requirement, not a convenience.** Every test
    here records matches built from fixture images and a synthetic partial frame.
    Without this patch each of them constructs a real SyncClient and pushes those
    rows to the shared pool at gameretro.net - the fork key is baked into the
    build, so credentials are present and the push would succeed. Synthetic
    matches would then be permanently in every contributor's training data. It
    also means no test touches the network.

    The mixin has no `_sync`: it constructs a local `SyncClient(self._store)` per
    run (solstice_clash.py:387) and calls `sync.push()` on that local (:450).
    Mode B follows the same pattern, so the seam is the CLASS, not an instance
    attribute. Adding a production `self._sync` purely to make a test observable
    would be the test dictating the design - and every real instance would then
    need it initialised or crash.
    """
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    counter = MagicMock()
    counter.push_calls = 0
    counter.pull_calls = 0

    class FakeSyncClient:
        enabled = True

        def __init__(self, store):
            self.store = store

        def push(self):
            counter.push_calls += 1

        def pull(self):
            counter.pull_calls += 1

    monkeypatch.setattr(mod, "SyncClient", FakeSyncClient)
    return counter


def count(db: Path, sql: str, *args) -> int:
    with sqlite3.connect(db) as con:
        return con.execute(sql, args).fetchone()[0]


def matches(db: Path) -> int:
    return count(db, "SELECT COUNT(*) FROM match WHERE source='compete_summary'")


def audits(db: Path) -> int:
    return count(db, "SELECT COUNT(*) FROM identification_audit")
```

`FakeCompeteMode.__init__` must therefore also set `self._db_path = db_path`.

**Fixture frames - do this first; three of the tests are meaningless without them.**
Directory: `tests/games/afk_journey/services/solstice/data/`.

| frame | status | action |
|---|---|---|
| `summary_01.png` | exists | - |
| `summary_02.png` | exists, and `test_winner_comes_from_the_header_not_the_panel_labels` already establishes it is a **different match** (its banner said LEFT LOSES) | add the guard test below |
| `overworld.png` | **does NOT exist** | capture one |
| `summary_partial.png` | **does NOT exist** | synthesise one |

- **`overworld.png`** - the AFK Journey overworld at 1080x1920. There is no existing fixture
  anywhere under `tests/` (checked). Capture with the device idle on the overworld.
- **`summary_partial.png`** - a details screen with fewer than six hero cells rendered. Capturing
  the real mid-animation frame is a timing lottery, so synthesise it: copy `summary_01.png` and
  fill two hero-cell rectangles (from `cfg.cells(CELL_TYPE)`) with black. Note in a comment that
  it is synthetic.

  **Verify the fixture before relying on it.** Blacking out a cell is assumed to fail the accept
  rule (score >= 0.70 and margin >= 0.10), but that is an assumption about `identify_cell`, not a
  fact. Confirm it, and black out more cells or add noise until it holds:

  ```bash
  cd src-tauri/src-python && uv run python -c "
  import cv2
  from adb_auto_player.games.afk_journey.services.solstice.summary import read_summary
  # ... load cfg, library, ocr as the conftest fixtures do ...
  read = read_summary(cv2.imread('<path to summary_partial.png>'), cfg, library, ocr)
  print(sum(1 for h in read.heroes if h.slug))   # must be < 6
  "
  ```

  If it prints 6, `test_a_partial_read_is_skipped_and_stays_armed` is asserting nothing.
- **Guard test, so the re-arm pair cannot silently degenerate.** It goes in
  **`tests/games/afk_journey/services/solstice/test_summary.py`**, NOT in the new mixins test
  file: `cfg`, `library`, `ocr_backend` and `frames` are defined in the solstice `conftest.py`,
  and pytest does not share a conftest with a sibling directory. `datetime`, `UTC`,
  `natural_key` and `read_summary` must be imported there if they are not already.

```python
def test_the_two_summary_fixtures_are_different_matches(cfg, library, ocr_backend, frames):
    """test_it_re_arms_after_the_screen_disappears asserts two rows appear and
    test_reopening_the_same_match_does_not_duplicate asserts one does. If these
    two frames ever collapsed to the same natural key both would pass for the
    wrong reason and the dedupe logic would be untested."""
    FIXED_TIME = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)

    def key_of(name: str) -> str:
        read = read_summary(cv2.imread(str(frames[name])), cfg, library, ocr_backend)
        return natural_key(
            outcome=read.winner,
            left_slugs=[h.slug for h in read.heroes if h.side == "left"],
            right_slugs=[h.slug for h in read.heroes if h.side == "right"],
            captured_at=FIXED_TIME,
        )

    assert key_of("summary_01") != key_of("summary_02")
```

`natural_key(outcome, left_slugs, right_slugs, captured_at)` takes the four facts, not a
`SummaryRead` (`matchkey.py:29`), and `captured_at` must be timezone-aware or it raises. Both
frames use the same `FIXED_TIME`, so the hour bucket cannot be what separates them - the heroes
or the outcome must.

- [ ] **Step 1: Write the failing test**

```python
def test_it_refuses_a_wrong_resolution(mode):
    """Every coordinate was measured on 1080x1920, and the mode may not resize
    the display - so it checks and refuses rather than acting."""
    mode.set_frame_size(720, 1280)
    with pytest.raises(GameActionFailedError, match=r"\\[SC-42\\]"):
        mode.collect_while_playing(max_polls=1)


def test_it_never_calls_start_up(mode):
    """start_up() resizes the display and can launch the game - under a live
    ranked match."""
    mode.collect_while_playing(max_polls=1)
    assert mode.start_up_calls == 0


def test_it_never_touches_the_device(mode):
    mode.feed(details_frame(), details_frame(), overworld_frame())
    mode.collect_while_playing(max_polls=3)
    assert mode.device_actions == []   # taps, swipes, holds, key events


def test_one_details_screen_records_once_across_many_polls(mode, db):
    """The screen stays up for tens of seconds; recording it twenty times would
    corrupt the model far more effectively than missing it."""
    mode.feed(*[details_frame()] * 10)
    before = matches(db)
    mode.collect_while_playing(max_polls=10)
    assert matches(db) == before + 1


def test_it_re_arms_after_the_screen_disappears(mode, db):
    mode.feed(details_frame(match=1), overworld_frame(), details_frame(match=2))
    before = matches(db)
    mode.collect_while_playing(max_polls=3)
    assert matches(db) == before + 2


def test_reopening_the_same_match_does_not_duplicate(mode, db):
    """Layer 1 re-arms because the screen disappeared; layer 2 catches it."""
    mode.feed(details_frame(match=1), overworld_frame(), details_frame(match=1))
    before = matches(db)
    mode.collect_while_playing(max_polls=3)
    assert matches(db) == before + 1


def test_a_partial_read_is_skipped_and_stays_armed(mode, db):
    """A frame caught mid-animation must not record, and must not disarm - the
    next poll gets a clean read of the same screen."""
    mode.feed(details_frame(heroes=4), details_frame())
    before = matches(db)
    mode.collect_while_playing(max_polls=2)
    assert matches(db) == before + 1


def test_a_frame_with_no_winner_is_skipped(mode, db, monkeypatch):
    """A real details screen whose banner has not resolved yields winner=None.
    Recording it would enter a match with no outcome, which the odds model counts
    as neither a win nor a loss.

    This is patched rather than fed a junk image on purpose: a junk image is
    rejected by is_details_screen and never reaches read_summary, so the test
    would pass without exercising the skip at all.
    """
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    real = mod.read_summary
    calls = {"n": 0}

    def winner_none_once(frame, cfg, library, ocr):
        calls["n"] += 1
        read = real(frame, cfg, library, ocr)
        if calls["n"] == 1:
            return SummaryRead(
                winner=None,
                left_player=read.left_player,
                right_player=read.right_player,
                heroes=read.heroes,
            )
        return read

    monkeypatch.setattr(mod, "read_summary", winner_none_once)
    mode.feed(details_frame(), overworld_frame(), details_frame())
    before = matches(db)
    mode.collect_while_playing(max_polls=3)
    assert matches(db) == before + 1


def test_it_records_source_compete_summary(mode, db):
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert matches(db) == 1


def test_theme_is_resolved_by_date_not_read_from_screen(mode, db):
    """The details screen never shows the theme, so it must never be 'ocr'."""
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    with sqlite3.connect(db) as con:
        got = con.execute(
            "SELECT theme_resolved_by FROM match WHERE source='compete_summary'"
        ).fetchall()
    assert got and all(r[0] in ("window", "default") for r in got)


def test_it_does_not_write_identification_audit_rows(mode, db):
    """Audit rows are confirmation evidence for cell tuning, and this mode
    cannot long-press to confirm anything."""
    before = audits(db)
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert audits(db) == before


def test_an_exception_in_one_poll_does_not_stop_the_loop(mode, db, monkeypatch):
    """One bad read must not end an unattended session. Patched for the same
    reason as above - a junk frame never gets as far as the code that could
    raise."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    real = mod.read_summary
    calls = {"n": 0}

    def raise_once(frame, cfg, library, ocr):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated identification failure")
        return real(frame, cfg, library, ocr)

    monkeypatch.setattr(mod, "read_summary", raise_once)
    mode.feed(details_frame(), overworld_frame(), details_frame())
    mode.collect_while_playing(max_polls=3)
    assert matches(db) >= 1


def test_it_stops_when_the_device_connection_dies(mode, monkeypatch):
    """A dead ADB connection must not spin silently for hours while the user
    plays, believing they are collecting."""
    monkeypatch.setattr(mode, "get_screenshot", MagicMock(side_effect=OSError("adb")))
    with pytest.raises(GameActionFailedError, match=r"\\[SC-45\\]"):
        mode.collect_while_playing(max_polls=100)


def test_a_recovered_screenshot_resets_the_failure_counter(mode, db, monkeypatch):
    """Transient failures must not accumulate across a whole session into a
    false 'connection is gone'."""
    calls = {"n": 0}
    real = mode.get_screenshot

    def flaky():
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise OSError("transient")
        return real()

    monkeypatch.setattr(mode, "get_screenshot", flaky)
    mode.feed(*[details_frame()] * 60)
    mode.collect_while_playing(max_polls=60)      # must not raise
    assert matches(db) >= 1


def test_it_warns_once_when_the_two_signals_disagree(mode, caplog):
    """The whole point of the second signal is noticing a broken first one.
    A details screen with its tab strip blacked out fires the template only."""
    frame = details_frame()
    x0, y0, x1, y1 = TAB_STRIP
    frame[y0:y1, x0:x1] = 0
    mode.feed(*[frame] * 40)
    mode.collect_while_playing(max_polls=40)
    assert sum("[SC-46]" in r.message for r in caplog.records) == 1


def test_it_pushes_after_each_recorded_match(mode, sync):
    """NOT on exit: the GUI stop button is SIGTERM (__main__.py:352) and Python
    does not run finally blocks on SIGTERM, so an on-exit push would never fire
    in the only way a user actually stops this mode."""
    mode.feed(details_frame(match=1), overworld_frame(), details_frame(match=2))
    mode.collect_while_playing(max_polls=3)
    assert sync.push_calls == 2


def test_it_does_not_push_when_nothing_was_recorded(mode, sync):
    mode.feed(overworld_frame(), overworld_frame())
    mode.collect_while_playing(max_polls=2)
    assert sync.push_calls == 0


def test_it_emits_a_periodic_heartbeat(mode, caplog, monkeypatch):
    """An on-stop summary would be skipped by SIGTERM exactly like the push.
    A heartbeat also answers 'is this still working?' while the user can act.

    HEARTBEAT_POLLS is 150 in production; patched down so the test does not need
    150 frames to see one line.
    """
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    monkeypatch.setattr(mod, "HEARTBEAT_POLLS", 5)
    mode.feed(*[overworld_frame()] * 12)
    with caplog.at_level("INFO"):
        mode.collect_while_playing(max_polls=12)
    assert sum("[SC-43]" in r.message for r in caplog.records) == 2
```

`sync` is autouse, so it is active in every test in this file; the tests that name it do so only
to read the counter. `HEARTBEAT_POLLS` and `MAX_CONSECUTIVE_SCREENSHOT_FAILURES` must both be read
from the module at use, not captured into defaults, for the same reason as `POLL_SECONDS`.

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

Read `POLL_SECONDS` from the module at each sleep, not captured into a default argument - the
tests patch the module attribute to 0.0 and a captured default would ignore it. The module
imports `time as time_module` and a bare `sleep` (`solstice_clash.py:12-13`); plain `time` is
**not** bound, so write `time_module.sleep(POLL_SECONDS)` - `time.sleep(...)` is a `NameError`.

```python
POLL_SECONDS = 2.0

@register_command(
    name="SolsticeClashCollectCompete",
    gui=GUIMetadata(
        label="Collect While Playing (Compete)",
        category=AFKJCategory.EVENTS_AND_OTHER,
        tooltip="Watch for post-match details screens and record them. Never taps.",
    ),
)
def collect_while_playing(self, max_polls: int | None = None) -> None:
    """Record every details screen the user opens. Never touches the device."""
```

**The resolution check must NOT take a screenshot of its own.** Validate the shape of the
*first polled frame* instead:

`GameActionFailedError` is already imported (`solstice_clash.py:19`). `itertools` is **not** -
add `import itertools` to the module imports.

```python
for poll in itertools.count():
    if max_polls is not None and poll >= max_polls:
        break
    frame = self.get_screenshot()
    if poll == 0 and frame.shape[:2] != (1920, 1080):
        raise GameActionFailedError(
            f"[SC-42] expected a 1080x1920 screen, got "
            f"{frame.shape[1]}x{frame.shape[0]} - this mode cannot resize the "
            f"display because you are in a live match"
        )
    ...
```

A separate `get_screenshot()` before the loop would consume a poll's worth of screen: harmless
in production, but it silently shifts every test's frame queue by one, so a test feeding one
details frame would check the resolution against it and then poll a blank. Reusing the frame also
means the check costs nothing.

Body per the spec's loop, continuing from that frame: `is_details_screen` gates, `armed` prevents
re-recording the same viewing, `match_by_natural_key` is the backstop, and the record uses
`source='compete_summary'` with the theme resolved by date.

**Gate the record on `is_complete`, NOT on `len(read.heroes)`.** `read_summary` returns one
`SummaryHero` per configured cell **always** - a failed identification comes back as
`slug is None`, not as a missing entry (`summary.py:266-280`). So `len(read.heroes) == 6` is true
even for `summary_partial.png`, and that check would record the partial frame and fail
`test_a_partial_read_is_skipped_and_stays_armed`. Filter the nulls first, then use the existing
helper - it is already imported at `solstice_clash.py:31`:

```python
left = [h.slug for h in read.heroes if h.side == "left" and h.slug]
right = [h.slug for h in read.heroes if h.side == "right" and h.slug]
if not is_complete(left, right, read.winner or ""):
    # DEBUG, not info. A details screen that never becomes complete - an
    # unidentifiable hero after a game update, say - is on screen for tens of
    # seconds, and at info this line prints every 2 seconds for all of it. The
    # spec is explicit about this.
    logging.debug(
        f"[SC-41] skipped: {len(left)}+{len(right)} heroes identified, "
        f"winner={read.winner} - staying armed for a cleaner read"
    )
    continue        # do NOT arm: the next poll should retry the same screen
```

`is_complete` also rejects a `winner` that is not `'left'` or `'right'` (`matchkey.py:24`), so it
covers the no-winner case in the same call. `max_polls` exists for the tests; production passes `None`.

**Sync follows the existing pattern exactly** - construct a local `SyncClient(self._store)` the
way `_collect_forever` does at `solstice_clash.py:387`. Do NOT add a `self._sync` attribute:
nothing initialises one on a real instance, so every production run would crash on it.

**Push after each recorded match - NOT on exit.** The spec originally said "push on stop"; that
is undeliverable and the spec has been corrected.

The GUI stop button calls `task_process.terminate()` (`__main__.py:352`), which is SIGTERM on a
`multiprocessing.Process`. No signal handler is installed anywhere in the Python tree, and
`SIGTERM_EXIT_CODE = -15` at `__main__.py:71` shows that is the expected path. **Python does not
run `finally` blocks on SIGTERM.** So a push in a `finally` would never fire in the only way a
user actually stops this mode - and a test asserting it would pass only because the test exits
through `max_polls`, the one exit path production never takes.

Installing a SIGTERM handler was considered and rejected: it changes process-wide behaviour for
every mode to serve one, and it would still lose the data on SIGKILL. Pushing per match deletes
the problem instead of guarding it.

The spec's objection to per-match pushing was network noise during play. It does not apply: a
record only happens when a details screen is up, which is *between* matches, never during one.
The existing `[SC-30]` wrapper already makes a slow or dead endpoint non-fatal.

```python
# Immediately after the match is durably recorded, inside the poll loop.
try:
    if sync.enabled:
        sync.push()
        sync.pull()
except Exception as exc:  # noqa: BLE001 - sync must never cost a match
    logging.warning(f"[SC-30] sync failed, continuing: {exc}")
```

Nothing is lost if the process is killed anyway: unpushed rows keep `pushed_at IS NULL` and go
out on the next Mode A run or manual sync.

**`[SC-43]` is a periodic heartbeat, not an on-stop summary** - for the same reason. Emit it
every `HEARTBEAT_POLLS = 150` polls (about 5 minutes at 2s):

```python
HEARTBEAT_POLLS = 150

if poll and poll % HEARTBEAT_POLLS == 0:
    logging.info(
        f"[SC-43] {poll} polls, {recorded} matches recorded, "
        f"{skipped} skipped this session"
    )
```

An on-stop summary would be silently skipped by SIGTERM exactly like the push. A heartbeat also
answers "is this thing still working?" *during* the session, which is when the user can still act
on the answer.

**Liveness: count consecutive screenshot failures and stop.** "An exception in one poll is logged
and the loop continues" is right for a bad read; it is wrong for a dead connection. If ADB drops -
cable, `adbd` restart, device sleep - `get_screenshot()` raises on every poll and the loop spins
silently for hours while the user plays an entire evening believing they are collecting.

Mode A has a restart budget for precisely this class of failure. Mode B, whose entire failure mode
IS silence, needs the equivalent:

```python
MAX_CONSECUTIVE_SCREENSHOT_FAILURES = 15   # ~30 seconds

# on a get_screenshot() exception:
screenshot_failures += 1
if screenshot_failures >= MAX_CONSECUTIVE_SCREENSHOT_FAILURES:
    raise GameActionFailedError(
        f"[SC-45] {screenshot_failures} consecutive screenshot failures - the "
        f"device connection is gone. Collection has stopped; nothing was being "
        f"recorded."
    )
# reset to 0 on any successful screenshot
```

Stopping loudly is correct here even though the mode is otherwise unstoppable-by-design: the spec's
"there is nothing to recover" applies to *game state*, not to a dead device. A mode that cannot
see the screen is not collecting, and saying so is the whole point.

**Detector disagreement warning.** The spec justifies the second signal as insurance against a
game update restyling the Replay button - but `is_details_screen` is an AND, so a restyle still
stops collection silently, and there are now two things that can. The conjunction is still right
(it is what buys the false-positive margin), but the claimed robustness only exists if something
notices the signals disagreeing:

```python
MAX_SIGNAL_DISAGREEMENT = 30   # ~1 minute of a screen showing one signal only

# When exactly one of the two signals fires, count it; reset on agreement.
# Warn ONCE per session, not per poll.
if disagreements == MAX_SIGNAL_DISAGREEMENT and not warned_disagreement:
    warned_disagreement = True
    logging.warning(
        f"[SC-46] the Replay template and the Ally/Enemy labels have disagreed "
        f"for {disagreements} polls. One of the two detection signals may have "
        f"broken in a game update - collection may be silently degraded."
    )
```

This requires `is_details_screen` to report *which* signals fired. Add a sibling that returns the
detail, and keep the boolean predicate as a thin wrapper over it so Task 1's tests and Mode A are
unaffected:

```python
class DetailsSignals(NamedTuple):
    template: bool
    labels: bool

    @property
    def confirmed(self) -> bool:
        return self.template and self.labels


def details_signals(frame, replay_template, ocr) -> DetailsSignals: ...


def is_details_screen(frame, replay_template, ocr) -> bool:
    return details_signals(frame, replay_template, ocr).confirmed
```

Add to Task 1: a test that `details_signals` reports `template=True, labels=False` on a details
screen whose tab strip has been blacked out, and that `is_details_screen` returns False for it.
That is the exact shape of the failure this is meant to catch.

- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Run the full solstice suite** - `uv run pytest tests/games/afk_journey/services/solstice/ -q`, expect no regressions against the current 124
- [ ] **Step 6: Commit**

---

### Task 4: Adopt the predicate in Mode A

**Files:**
- Modify: `.../mixins/solstice_clash.py`
- Test: `.../tests/games/afk_journey/mixins/test_passive_collection.py` (the file Task 3
  created - these tests reuse its `mode` fixture and `_frame` helper, so they must live
  beside them)

**Depends on Task 3** for that harness, and on Task 1 for the predicate.

Mode A taps the chart button, sleeps two seconds, and reads blind. If the tap misses or the transition is slow, `read_summary()` parses whatever is on screen.

**Scope this honestly:** this would NOT have caught the earlier live-battle-read-as-a-draw bug. That happened in match-end detection, before the chart tap. It prevents the adjacent failure - recording garbage parsed from a non-details screen.

- [ ] **Step 1: Write the failing test**

**Test the extracted helper, not `_run_one_match`.** The method is `_run_one_match` (leading
underscore, `solstice_clash.py:237`) and it taps, navigates, and runs several real timeouts - the
device-free harness cannot drive it, and a test that tried would be asserting on the wrong thing.
So the wait becomes its own method, `_wait_for_details_screen()`, and that is what is tested.
`spectate.png` serves as a confirmed not-details frame; it is already in the Task 1 NOT_DETAILS
list.

```python
def test_it_returns_the_frame_once_the_details_screen_appears(mode):
    """Replaces sleep-and-hope with a bounded wait."""
    mode.feed(_frame("spectate"), _frame("spectate"), details_frame())
    frame = mode._wait_for_details_screen()
    assert frame is not None


def test_it_raises_sc44_if_the_details_screen_never_arrives(mode, monkeypatch):
    """A short timeout: the point is the raise, not waiting 30 real seconds."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    monkeypatch.setattr(mod, "DETAILS_TIMEOUT", 1.0)
    monkeypatch.setattr(mod, "DETAILS_POLL_DELAY", 0.05)
    mode.feed(*[_frame("spectate")] * 40)
    with pytest.raises(GameTimeoutError, match=r"\\[SC-44\\]"):
        mode._wait_for_details_screen()
```

`GameTimeoutError` must be imported in the test file; it is already imported in
`solstice_clash.py:19`.

- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement**

Replace `sleep(2)` after the chart tap with `_execute_or_timeout` polling `is_details_screen`.
**The polled callable must RAISE `_UndesiredResultError` to mean "not yet", not return `False`.**
`_execute_or_timeout` retries only on that exception (`_template_mixin.py:36-62`); any returned
value - including `False` - is a success and exits the loop immediately, so a `return False`
would make the wait a no-op that silently reads the wrong screen. `_UndesiredResultError` is
already imported at `solstice_clash.py:20` and this is exactly how `_match_end` uses it.

Add the two constants next to the existing `RESULT_POLL_DELAY` / `MATCH_TIMEOUT` at module
level - neither exists yet:

```python
DETAILS_POLL_DELAY = 0.5
DETAILS_TIMEOUT = 15.0
```

```python
def _wait_for_details_screen(self) -> np.ndarray:
    """Block until the details screen is up, and return that frame."""
    replay = load_replay_template(self.template_dir)

    def _details_ready() -> np.ndarray:
        frame = self.get_screenshot()
        # _ocr is a PROPERTY (solstice_clash.py:646) - self._ocr(), with the
        # call parens, raises TypeError on the RapidOCRBackend instance.
        if is_details_screen(frame, replay, self._ocr):
            return frame
        raise _UndesiredResultError()

    return self._execute_or_timeout(
        _details_ready,
        delay=DETAILS_POLL_DELAY,
        timeout=DETAILS_TIMEOUT,
        timeout_message=(
            "[SC-44] details screen never appeared after tapping the chart button"
        ),
    )
```

Then wire it into `_run_one_match` at `solstice_clash.py:348-351`, which currently reads:

```python
self.tap(chart)
sleep(2)

self._record_summary(draft_frame, prematch_frame, theme)
```

becomes:

```python
self.tap(chart)
frame = self._wait_for_details_screen()

self._record_summary(draft_frame, prematch_frame, theme, frame=frame)
```

`_record_summary` currently takes its own screenshot at line 495. Give it an optional
`frame: np.ndarray | None = None` and use `frame if frame is not None else self.get_screenshot()`.
Passing the confirmed frame through is the point of the change - re-capturing would reintroduce
the race, because the screen can be dismissed between the check and the second capture. The
default keeps every existing caller and test working unchanged.

Reuse the returned frame for `read_summary` rather than taking a fresh screenshot - it is
already confirmed to be the right screen, and a second capture could catch a dismissal.
- [ ] **Step 4: Run the full solstice suite**
- [ ] **Step 5: Commit**

---

### Task 5: Live verification

- [ ] **Step 1: Play one competitive match** with the mode running. Open the details screen, leave it up for ~30 seconds, dismiss it.

Expected: exactly one `[SC-40]` line, and no repeats while the screen is open.

- [ ] **Step 2: Confirm one row, not many**

```bash
sqlite3 ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite \
  "SELECT COUNT(*), COUNT(DISTINCT natural_key) FROM match WHERE source='compete_summary';"
```

Both numbers equal, and equal to the number of matches played.

- [ ] **Step 3: Confirm the device was untouched** - the match played out normally with no stray
      input, and no perceptible frame drops or input lag. The mode runs `screencap` every two
      seconds during a live ranked match; "never touch the device" was scoped to taps, but a
      screenshot has real on-device cost and this is the only place it gets measured.

- [ ] **Step 3b: Confirm other game modes do not false-positive.** With the mode still running,
      open the post-battle details/stats screen of at least two NON-Solstice battle types (Arena
      and Dream Realm at minimum). Nothing may record.

      This was never measured. The predicate's rejection set is entirely Solstice screens plus the
      overworld - but Mode B runs while the user plays the game at large, and other modes have
      post-battle screens with rosters. Today the protection is accidental: a 5-hero layout read at
      Solstice cell coordinates should fail `is_complete`. "Should" is doing a lot of work there,
      and the cost of being wrong is a non-Solstice match recorded as `compete_summary` and
      **pushed into the shared pool**, which is the one contamination this design most needs to
      avoid.

      If anything records, stop: the predicate needs a third signal, not a tweak.

```bash
sqlite3 ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite \
  "SELECT COUNT(*) FROM match WHERE source='compete_summary';"
```

Must be unchanged from Step 2.

- [ ] **Step 4: Confirm it syncs**

Look for `[SC-35]` in the log on stop. Then confirm locally that the rows were accepted - the
client only sets `pushed_at` after the server adopts them, so a non-null value IS the
confirmation, and it needs no API key:

```bash
sqlite3 ~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite \
  "SELECT COUNT(*) FILTER (WHERE pushed_at IS NOT NULL),
          COUNT(*) FILTER (WHERE pushed_at IS NULL)
   FROM match WHERE source='compete_summary';"
```

Expect all pushed, none pending. Do not hand-roll a curl here: the fork key is baked into the
binary rather than sitting in the environment, so `$KEY` and `$UUID` are not variables the
verifier has.

- [ ] **Step 5: Update CHANGELOG.md and commit**
