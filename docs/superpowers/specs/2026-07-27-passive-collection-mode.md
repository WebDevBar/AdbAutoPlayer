# Passive Collection (Mode B) - Design

**Status:** APPROVED - 3 review rounds plus an independent review, final NO ISSUES FOUND
**Date:** 2026-07-27
**Related:** Mode A (spectate collection), and the pooled sync at `gameretro.net/adb`

---

## 1. Goal

Collect Solstice Clash match data **while the user plays competitive matches themselves**.

The mode watches the screen, and whenever a 3v3 details screen appears, records it. That is the
entire behaviour. No navigation, no menus, no decisions.

Mode A spectates and drives the game. This one is a passenger.

## 2. The governing rule

**It must never touch the device.**

The user is playing a competitive match with their own ranking at stake. A stray tap could
forfeit a match, exit a battle, or spend a resource. So this mode:

- never taps, swipes, holds, or sends a key event
- never navigates, never dismisses a popup, never presses Back
- only ever calls `get_screenshot()`

Everything else in this document follows from that. Where a safety measure and a data-quality
measure conflict, safety wins - a missed match costs one row, a stray tap costs the user a game.

### It must NOT call `start_up()`

Every other mode begins with `self.start_up()`. This one cannot. `start_up()` calls
`_set_device_resolution()` and, if the game is not detected as running, `start_game()` - it can
resize the display and launch the app underneath a live ranked match.

Instead, the mode verifies the invariant it depends on and refuses if it does not hold:

```python
frame = self.get_screenshot()
if frame.shape[:2] != (1920, 1080):
    raise GameActionFailedError(
        "[SC-42] passive collection needs a 1080x1920 display; found "
        f"{frame.shape[1]}x{frame.shape[0]}. Run any other mode once to set it, "
        "then start this one."
    )
```

Checked once at start, not per poll - the resolution cannot change mid-session without the user
doing something drastic, and a per-poll check would spend work on an invariant.

**Why the invariant exists:** every Solstice coordinate - the cell registry, the OCR strip, the
Replay template - was measured on 1080x1920 (`services/solstice/config.py`, and the
`base_resolution` column on `cell_registry`). Portability is achieved by setting the display to
that size, which is exactly the action this mode may not take. So it checks instead, and says
plainly what to do about it.

## 3. Why this is not just Mode A without navigation

Three differences that change the design.

**The same screen is seen many times.** The details screen stays up until the user dismisses it -
often tens of seconds. Polling every 2 seconds sees one match 20 times. Mode A never had this
problem because it navigates away immediately after recording.

**The theme is never on screen.** Mode A reads the theme during navigation, from the event
screen. This mode never visits it. The theme comes from the date window instead, which is more
reliable anyway (section 6).

**Compete matches are the user's own.** The user is one of the two players, so `left_player` or
`right_player` is them. That is data, not a problem - but it means player-name reads are worth no
more here than in spectate, and are still excluded from identity.

## 4. Two separate concerns: "is this the screen?" and "have I already had it?"

These are different questions and they belong in different places.

### `is_details_screen(frame) -> bool` - a pure predicate

Reusable, stateless, and knows nothing about recording. Mode A can use it too: today it taps the
chart button, sleeps two seconds, and reads blind - if the tap misses or the transition is slow,
`read_summary()` runs against whatever is on screen and the colour probes sample those coordinates
regardless. Replacing the sleep with a bounded wait on this predicate turns "assume it landed"
into confirmation.

To be precise about the scope: this would **not** have caught the earlier live-battle-read-as-a-
draw bug. That happened in match-end detection, before the chart tap, on a path this predicate
does not guard. It prevents the adjacent failure - recording garbage parsed from a non-details
screen - not that one.

It contains no deduplication, because "is this a details screen" and "have I seen this match" are
unrelated questions, and folding them together would make the check useless to any caller that
does not want the second one.

### Deduplication - passive mode only, in two layers

Mode A never needed this: it navigates away immediately after recording. Passive mode polls a
screen the user leaves open for tens of seconds, so it would otherwise record one match twenty
times - and duplicates are worse than omissions, because each one is another vote in the model.

**Layer 1 - arm/disarm on the screen transition.** Record once, then refuse to record again until
the details screen has *disappeared*. Re-arm when it does.

```
armed = True
each poll:
    on_details = is_details_screen(frame)
    if not on_details:
        armed = True          # screen gone - the next one is a new match
        continue
    if not armed:
        continue              # same screen still open, already recorded
    ... record ...
    armed = False
```

This matches the actual flow - open the details screen, dismiss it, play the next match - and
costs one boolean. No database lookup per poll, and it does not depend on the key being
computable, so it still holds if a read is partial.

**Layer 2 - `natural_key`, as a backstop.** The arm/disarm flag only covers one continuous
viewing. If the user reopens the same match's details later, or restarts the mode, the flag is no
help. `match_by_natural_key()` catches that, and it is the same key the pooled server dedupes on,
so local and remote agree.

Most of this layer already exists. `record_match()` does `INSERT ... ON CONFLICT(natural_key) DO
NOTHING` and returns the existing id, and its docstring was written for exactly this case:
*"Re-observing a match must not duplicate it or raise - Mode B will see the same match on
consecutive polls."* `record_heroes()` likewise upserts on `(match_id, side, slot)`. So the
explicit `match_by_natural_key()` check is an optimisation that avoids the write, not the thing
that provides correctness.

Layer 1 is the cheap common case; layer 2 is correctness. Neither alone is enough.

### One ordering constraint

The key can only be computed after the summary is parsed - it is made of the outcome and the two
hero sides. Mode A's path inserts the match row first and sets the key afterwards, which is why
its `ON CONFLICT(natural_key) DO NOTHING` never fires: SQLite permits unlimited NULLs in a UNIQUE
column, so a NULL key conflicts with nothing.

Passive mode therefore parses first, computes the key, checks it, and only then inserts.

### Two known limits of the key, stated rather than hidden

**A match straddling :59 and :00** gets two different keys and could be recorded twice. Mode A has
the same edge and accepts it. Layer 1 makes it rarer here, since one continuous viewing is covered
by the flag regardless of the clock.

**Two genuinely different matches with identical comps, identical outcome, in the same UTC hour
collide** - the second is silently dropped. The key is only outcome, both sorted hero sides, and
the hour.

This is more likely in compete than in spectate, and the reason is sitting on the screen: the
details screen has a **Play Again** button. Replaying the same matchup and winning again produces
an identical key. Estimated cost is still low - it needs the same six heroes on both sides AND the
same outcome AND the same hour - but it is a real loss, and it is a loss of the user's own match.

It is accepted rather than fixed because the alternative is worse: the pooled server dedupes on
this exact key, so adding a local-only discriminator would make local and remote disagree about
what one match is. If it proves to bite in practice, the fix belongs in the shared identity model
and has to be coordinated with the API, not patched here.

**Do not "fix" this by adding player names to the key.** They are excluded deliberately -
`matchkey.py` records why - and adding them would break dedupe between two spectators of the same
match, which is the pool's whole purpose.

**The one duplicate neither layer can catch: hero-slug jitter.** The key is built from identified
hero slugs. If a borderline identification resolves differently on a second viewing of the same
screen, the keys differ and both rows are stored - a duplicate carrying a corrupted comp. Unlikely
on a static screen re-read from near-identical frames, and the accept rule (`score >= 0.70 and
margin >= 0.10`) makes a flip improbable, but it is the residual hole and is worth knowing before
someone chases a mystery duplicate.

**The hour rollover bites on re-records, not just first records.** A flicker that briefly hides
the detector, or a reopen, that happens to straddle :59/:00 produces a different bucket and
therefore a genuine duplicate. That is the realistic path to the hour edge in this mode, rather
than a single record landing on the boundary.

## 5. The user's flow

The user drives; the mode watches:

1. play a competitive match
2. when it ends, open the details screen
3. the mode notices and records it
4. dismiss, play the next match, repeat

Nothing is expected of the user beyond opening the screen they would look at anyway. The mode
never signals readiness, never asks for anything, and never blocks - if a details screen is
missed, the cost is one row.

## 6. The polling loop

```
armed = True

every POLL_SECONDS (2.0):
    frame = get_screenshot()
    if not is_details_screen(frame):
        armed = True                 # screen gone; next one is a new match
        continue
    if not armed:
        continue                     # still the same screen, already recorded

    read = read_summary(frame)
    if read.winner is None or not six heroes identified:
        continue                     # mid-animation or partial read

    key = natural_key(read.winner, left, right, captured_at)
    if not store.match_by_natural_key(key):     # backstop
        record(read, key)
    armed = False
```

### The detector is the Replay button

`event/solstice_clash/details_replay.png` - the circular replay icon in the bottom-right of the
details screen, cropped tight inside the solid disc so none of the semi-transparent dimmed
background is baked into the template.

Measured against every fixture, a live compete details screen, result screens, the draft, the
prematch, the betting screen, a menu, and the overworld:

| template | details screens | result screens | everything else | verdict |
|---|---|---|---|---|
| **`details_replay`** | **1.000** | 0.418 | 0.371 - 0.511 | **use this** |
| `battle/result` | 0.729 - 0.754 | **0.970** | 0.350 - 0.524 | fires a screen too early |
| `summary_back` | 1.000 | - | up to 0.996 | generic back button |
| winner tint | a winner | a winner | a winner on most screens | not discriminating |

The Replay button leaves a 0.49 margin between 0.511 and 1.000, so even the default 0.90
threshold is safe.

Three candidates were rejected on measurement, and every one of them looked right on reasoning:

- **`battle/result`** is upstream's statistics icon and appeared to be free reuse. But it scores
  **higher on the result screen (0.970) than on the details screen (0.754)**, because it IS the
  bright chart button you tap to open details - dimmed once you are there. It would have fired one
  screen early on every match.
- **`summary_back`** is genuinely on the details screen - Mode A waits for it there - but it is a
  plain back arrow present on half the screens in the game.
- **The winner tint** was assumed to reject non-result screens. It returns a winner on the draft,
  prematch and long-press screens too; only the overworld returns `None`.

### A second, independent signal: the roster tab labels

The Replay button is one template. If a game update moves or restyles it, detection silently
stops and the mode quietly collects nothing. So the frame must also show **"Ally" or "Enemy"**,
read by OCR from `x 0-220, y 350-1730` - the strip holding both roster tabs, below the player-name
header and left of the stat columns, so no other text is in frame.

Measured on that region: all four details screens produce one or both labels, and none of the
fifteen other screens produces either.

OCR rather than a template, because the tabs are tinted by outcome - orange for the winning trio,
blue for the losing one - so a template cut from an orange "Ally" would not match a blue one. The
text is the same either way.

**Exact matches only - never `"ally" in text`.** Verified against real strings: a substring test
accepts `"Really"` and `"Rally"`. `"All In"` - which appears on the betting screen, two words away
from "Ally" - is rejected by both, but only because of a space OCR may not preserve. This project has already
paid for that lesson once: fuzzy hero-name matching scored `SILVER` against `SILVEN` at 0.833 and
was replaced with `resolve_hero_name_strict`. An OCR block is compared whole, casefolded and
stripped, against the exact set `{"ally", "enemy"}`.

**"Ally" OR "Enemy", not both.** On `longpress_ally1` a popup covers the Ally tab and only "Enemy"
reads - yet that is still a details screen with a full set of data worth capturing. Requiring both
would reject it for no benefit.

### And finally the data itself

After both checks, `read_summary()` must still produce six identified heroes and a winner. That is
not redundant: it rejects a frame caught mid-animation, where the screen is right but the content
has not finished rendering.

**Incomplete reads are skipped silently.** A frame caught mid-animation may parse partially.
Skipping costs nothing: the screen is still up, and the next poll gets a clean read.

**No failure counter, no restart budget.** There is nothing to recover: the mode has no state to
lose and takes no actions that can fail. An exception in one poll is logged and the loop
continues.

## 7. Event and theme

Both are known without reading the screen.

- **Event** is fixed - this mode only understands Solstice Clash.
- **Theme** comes from `resolve_theme(captured_at, ocr_name=None)`, which resolves by dated
  window. This is strictly better than Mode A's screen read: a window cannot be misread.

If the capture falls outside every window, it resolves to the event default and is stamped
`theme_resolved_by='default'` - kept, visibly unknown, and excluded from training until a window
is filled in. That is the existing behaviour and needs no special case here.

## 8. What it records

The same shape as Mode A: one `match` row plus six `match_hero` rows, `origin='local'`, and
`natural_key` set at insert.

`source='compete_summary'`, which **must be added to `_SOURCES`** in `store.py` - the store
enforces that set deliberately, so an unlisted value fails before insert rather than persisting a
typo. It parallels the existing `spectate_summary`.

**It does not feed the identification learning path.** `identification_audit` and
`hero_screen_transform` exist to tune this machine's cell geometry, and that tuning is driven by
long-press OCR confirmation which this mode cannot do without tapping. Recording audit rows
without confirmation evidence would pollute the learning path with unverified reads.

## 9. Sync

Rows collected here are `origin='local'` with a `natural_key`, so they are pushed by the existing
`pushable_matches()` selection with no change. Push happens on mode stop rather than per match -
the user is playing, and a network call between matches is unnecessary noise.

## 10. User feedback

The user is playing a game, not watching a log. One line per recorded match, and nothing at all
for the polls that see nothing:

```
[SC-40] recorded match 47: left won, Converging Paths
[SC-41] already recorded, skipping
```

`[SC-41]` at debug level only - at info it would print every 2 seconds while a details screen is
open.

On stop, a summary: how many matches were recorded and how many were pushed.

## 11. Open questions

1. Should the mode detect that the event has ended (no more Solstice Clash matches) and stop
   itself, or simply keep polling until stopped? Polling forever is simpler and costs almost
   nothing.
2. Should it also record `match_odds` (the game's own betting pool) when visible? It is on the
   prematch screen, not the details screen, so it would need a second detector.
