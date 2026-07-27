# Passive Collection (Mode B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A mode that records Solstice Clash match data while the user plays competitive matches themselves, by watching for the post-match details screen and never touching the device.

**Architecture:** One new pure module (`details_screen.py`) holding a reusable screen predicate, one new command on the existing `SolsticeClashMixin`, and one added value in the store's allowed-source set. Everything else already exists.

**Spec:** `docs/superpowers/specs/2026-07-27-passive-collection-mode.md` (approved, 3 rounds plus an independent review)

## Global Constraints

- **The mode must never touch the device.** No tap, swipe, hold, key event, navigation, or popup dismissal. `get_screenshot()` is the only device call permitted. The user is playing a ranked match.
- **It must NOT call `start_up()`.** That calls `_set_device_resolution()` and can call `start_game()` - resizing the display and launching the app under a live match. Verify 1080×1920 once and refuse instead.
- **Exact string matching only**, never substring. `"ally" in text` accepts "Really" and "Rally". This project already replaced fuzzy hero matching after `SILVER` scored 0.833 against `SILVEN`.
- **Duplicates are worse than misses.** Each duplicate is another vote in the model; a miss costs one row.
- Source paths: `src-tauri/src-python/adb_auto_player/games/afk_journey/`. Tests: `src-tauri/src-python/tests/games/afk_journey/`.
- Log codes continue the existing `[SC-nn]` scheme: `SC-40` recorded, `SC-41` skipped, `SC-42` wrong resolution, `SC-43` session summary.

---

### Task 1: `is_details_screen()` - the reusable predicate

**Files:**
- Create: `.../services/solstice/details_screen.py`
- Test: `.../tests/games/afk_journey/services/solstice/test_details_screen.py`

**Interfaces:**
- Consumes: `IconLibrary`, `SolsticeConfig`, an `OCRBackend`, and the template matcher.
- Produces: `is_details_screen(frame, find_template, ocr) -> bool`, plus the constants `TAB_STRIP` (the OCR region) and `TAB_LABELS = frozenset({"ally", "enemy"})`.

Deliberately **stateless and dedupe-free**: Mode A wants the predicate without the recording policy.

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
def test_accepts_every_details_screen(name, frames, read_frame, find_template, ocr_backend):
    assert is_details_screen(read_frame(frames[name.removesuffix(".png")]),
                             find_template, ocr_backend) is True


@pytest.mark.parametrize("name", NOT_DETAILS)
def test_rejects_every_other_screen(name, frames, read_frame, find_template, ocr_backend):
    assert is_details_screen(read_frame(frames[name.removesuffix(".png")]),
                             find_template, ocr_backend) is False


def test_a_popup_over_the_ally_tab_still_counts(frames, read_frame, find_template, ocr_backend):
    """longpress_ally1 shows only 'Enemy' - it is still a details screen with a
    full set of data, so the label check is OR, not AND."""
    assert is_details_screen(read_frame(frames["longpress_ally1"]),
                             find_template, ocr_backend) is True


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

The `find_template` fixture wraps `game_find_template_match` for a supplied frame; add it to the solstice `conftest.py` alongside the existing `read_frame` / `ocr_backend` fixtures.

- [ ] **Step 2: Run to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_details_screen.py -v`
Expected: FAIL, `ModuleNotFoundError: ...solstice.details_screen`

- [ ] **Step 3: Implement**

```python
"""Is this frame the post-match details screen?

Pure, stateless, and free of any recording policy - Mode A wants this check
without Mode B's deduplication.

Two independent signals, because one template is a single point of failure: a
game update that restyles it would silently stop collection.
"""

# The Replay control, bottom-right, cropped inside the solid disc and down to
# the descender of the 'p' so no semi-transparent background is baked in.
# Measured: 1.000 on all four details screens, <= 0.643 on fifteen others.
REPLAY_TEMPLATE = "event/solstice_clash/details_replay"

# The roster tab strip: both tabs, below the player-name header and left of the
# stat columns, so no other text is in frame. Absolute pixels on 1080x1920,
# matching how summary.py already addresses its OCR regions.
TAB_STRIP = (0, 350, 220, 1730)          # x0, y0, x1, y1
TAB_LABELS = frozenset({"ally", "enemy"})


def is_details_screen(frame, find_template, ocr) -> bool:
    if find_template(REPLAY_TEMPLATE, frame) is None:
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
- Test: `.../tests/games/afk_journey/services/solstice/test_store_sources.py`

- [ ] **Step 1: Write the failing test**

```python
def test_compete_summary_is_an_allowed_source(db):
    """The store enforces _SOURCES deliberately, so an unlisted value fails
    before insert rather than persisting a typo."""
    store = MatchStore(db)
    mid = store.record_match(MatchRecord(
        source="compete_summary",
        captured_at="2026-07-25T12:00:00+00:00",
        outcome="left", outcome_source="observed",
    ))
    assert mid > 0


def test_an_unknown_source_is_still_rejected(db):
    with pytest.raises(ValueError):
        MatchStore(db).record_match(MatchRecord(
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


def test_one_details_screen_records_once_across_many_polls(mode, store):
    """The screen stays up for tens of seconds; recording it twenty times would
    corrupt the model far more effectively than missing it."""
    mode.feed(*[details_frame()] * 10)
    before = store.match_count()
    mode.collect_while_playing(max_polls=10)
    assert store.match_count() == before + 1


def test_it_re_arms_after_the_screen_disappears(mode, store):
    mode.feed(details_frame(match=1), overworld_frame(), details_frame(match=2))
    before = store.match_count()
    mode.collect_while_playing(max_polls=3)
    assert store.match_count() == before + 2


def test_reopening_the_same_match_does_not_duplicate(mode, store):
    """Layer 1 re-arms because the screen disappeared; layer 2 catches it."""
    mode.feed(details_frame(match=1), overworld_frame(), details_frame(match=1))
    before = store.match_count()
    mode.collect_while_playing(max_polls=3)
    assert store.match_count() == before + 1


def test_a_partial_read_is_skipped_and_stays_armed(mode, store):
    """A frame caught mid-animation must not record, and must not disarm - the
    next poll gets a clean read of the same screen."""
    mode.feed(details_frame(heroes=4), details_frame())
    before = store.match_count()
    mode.collect_while_playing(max_polls=2)
    assert store.match_count() == before + 1


def test_a_frame_with_no_winner_is_skipped(mode, store): ...


def test_it_records_source_compete_summary(mode, store):
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert store.newest_match().source == "compete_summary"


def test_theme_is_resolved_by_date_not_read_from_screen(mode, store):
    """The details screen never shows the theme, and a window cannot be misread."""
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert store.newest_match().theme_resolved_by in ("window", "ocr", "default")


def test_it_does_not_write_identification_audit_rows(mode, store):
    """Audit rows are confirmation evidence for cell tuning, and this mode
    cannot long-press to confirm anything."""
    before = store.audit_count()
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert store.audit_count() == before


def test_an_exception_in_one_poll_does_not_stop_the_loop(mode, store):
    mode.feed(broken_frame(), details_frame())
    mode.collect_while_playing(max_polls=2)
    assert store.match_count() >= 1


def test_it_pushes_on_stop_not_per_match(mode, sync):
    mode.feed(details_frame(match=1), overworld_frame(), details_frame(match=2))
    mode.collect_while_playing(max_polls=3)
    assert sync.push_calls == 1
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

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

Body per the spec's loop: verify resolution once (`[SC-42]`), then poll - `is_details_screen` gates, `armed` prevents re-recording the same viewing, `read_summary` must yield six heroes and a winner, `match_by_natural_key` is the backstop, and the record uses `source='compete_summary'` with the theme resolved by date. Push once on exit. `max_polls` exists for the tests; production passes `None`.

- [ ] **Step 4: Run to verify it passes**
- [ ] **Step 5: Run the full solstice suite** - `uv run pytest tests/games/afk_journey/services/solstice/ -q`, expect no regressions against the current 124
- [ ] **Step 6: Commit**

---

### Task 4: Adopt the predicate in Mode A

**Files:**
- Modify: `.../mixins/solstice_clash.py`

Mode A taps the chart button, sleeps two seconds, and reads blind. If the tap misses or the transition is slow, `read_summary()` parses whatever is on screen.

**Scope this honestly:** this would NOT have caught the earlier live-battle-read-as-a-draw bug. That happened in match-end detection, before the chart tap. It prevents the adjacent failure - recording garbage parsed from a non-details screen.

- [ ] **Step 1: Write the failing test**

```python
def test_mode_a_confirms_the_details_screen_before_reading(mode):
    """Replaces sleep-and-hope with a bounded wait."""
    mode.feed(result_frame(), result_frame(), details_frame())
    mode.run_one_match()
    assert mode.recorded == 1


def test_mode_a_raises_if_the_details_screen_never_arrives(mode):
    mode.feed(*[result_frame()] * 20)
    with pytest.raises(GameTimeoutError, match=r"\\[SC-44\\]"):
        mode.run_one_match()
```

- [ ] **Step 2: Run to verify it fails**
- [ ] **Step 3: Implement** - replace `sleep(2)` after the chart tap with `_execute_or_timeout` polling `is_details_screen`, raising `[SC-44]` on timeout.
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

- [ ] **Step 3: Confirm the device was untouched** - the match played out normally with no stray input.

- [ ] **Step 4: Confirm it syncs** - `[SC-35]` on stop, then check the row reached the pool:

```bash
curl -sS -H "X-API-Key: $KEY" -H "X-Instance-Id: $UUID" \
  "https://gameretro.net/adb/v1/matches?since=0" | python3 -m json.tool | grep -c natural_key
```

- [ ] **Step 5: Update CHANGELOG.md and commit**
