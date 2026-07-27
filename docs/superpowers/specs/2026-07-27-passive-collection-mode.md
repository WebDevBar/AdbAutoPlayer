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
    if not find_template("battle/result.png", threshold=0.65, screenshot=frame):
        continue                     # cheap: not a details screen
    read = read_summary(frame)
    if read.winner is None or not six heroes identified:
        continue                     # mid-animation or partial read
    key = natural_key(read.winner, left, right, captured_at)
    if store.match_by_natural_key(key):
        continue                     # already recorded; say nothing
    record(read, key)
```

### The detector is upstream's own `battle/result.png`

No new template is needed - the marker already ships with the app, and two candidates that looked
obvious were measured and rejected first.

Measured against every fixture plus a live compete details screen:

| template | on details screens | on everything else | usable |
|---|---|---|---|
| **`battle/result.png`** | **0.729 - 0.754** | **0.360 - 0.524** | **yes** |
| `summary_back` | 1.000 | up to **0.996** (`spectate_draft`) | no |
| the orange/blue winner tint | returns a winner | returns a winner on draft, prematch, longpress | no |
| `battle/records.png` | 0.43 - 0.45 | 0.33 - 0.47 | no |

`battle/result.png` leaves a clean gap from 0.524 to 0.729, so a threshold of **0.65** sits in
the middle of it. It must be passed explicitly: the default of 0.90 rejects every one of these
frames.

Both rejected candidates are worth recording, because both looked correct on reasoning alone:

- **`summary_back`** is on the details screen - Mode A waits for it there - but it is a generic
  back button present on half the screens in the game.
- **The winner tint** was assumed to reject non-result screens. It does not: it returns a winner
  on the draft, prematch and long-press screens too. Only the overworld returns `None`.

After the template check, `read_summary()` still has to produce six identified heroes and a
winner. That is not redundant - it rejects a frame caught mid-animation, where the screen is
right but the content has not finished rendering.

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
