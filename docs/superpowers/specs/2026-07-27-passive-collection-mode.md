# Passive Collection (Mode B) - Design

**Status:** design, under review
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

## 4. Deduplication - the core of it

`natural_key` already answers "is this the same match" (theme-free hash of outcome, both sorted
hero sides, and the UTC hour bucket). The problem is *when* it is computed.

Mode A's path inserts the match row, records the heroes, and only then sets the key - because it
navigates to the summary and reads it in that order. With `natural_key` NULL on insert, the
`ON CONFLICT(natural_key) DO NOTHING` clause does nothing at all: SQLite permits unlimited NULLs
in a UNIQUE column.

So passive mode inverts the order:

1. parse the summary from the frame
2. compute `natural_key` from what was parsed
3. `match_by_natural_key()` - already exists in the store
4. if it returns an id, **do nothing at all**, not even a log line at info level
5. otherwise insert the match, its heroes, and the key

Step 4 is the one that matters. Recording the same match 20 times would corrupt the model far
more effectively than missing it entirely - each duplicate is a vote.

### The hour-bucket edge

A match straddling :59 and :00 gets two different keys and would be recorded twice. Mode A has
the same edge and accepts it. Here it is *less* likely, because both readings come from the same
machine within seconds of each other rather than from two spectators minutes apart.

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
every POLL_SECONDS (2.0):
    frame = get_screenshot()
    if not find_template("event/solstice_clash/details_replay", screenshot=frame):
        continue                     # cheap: not a details screen
    blocks = ocr(frame[350:1730, 0:220])        # the roster tab strip
    labels = {b.text.strip().casefold() for b in blocks}
    if not (labels & {"ally", "enemy"}):        # EXACT match, never substring
        continue                     # second, independent signal
    read = read_summary(frame)
    if read.winner is None or not six heroes identified:
        continue                     # mid-animation or partial read
    key = natural_key(read.winner, left, right, captured_at)
    if store.match_by_natural_key(key):
        continue                     # already recorded; say nothing
    record(read, key)
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

The same shape as Mode A: one `match` row plus six `match_hero` rows, `source='compete_summary'`
to distinguish it, `origin='local'`, and `natural_key` set at insert.

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
