# Solstice Clash Phase 2 (Mode A) - Training and Recording Mode

**Status:** design, awaiting approval
**Date:** 2026-07-26
**Supersedes the Phase 2 sketch in** `docs/superpowers/specs/2026-07-25-solstice-clash-phase1-design.md`

---

## 1. Goal

Mode A does two jobs at once, and both matter:

**Recording.** Collect Solstice Clash match outcomes unattended, overnight, so a
comp-versus-comp win model has enough data to be worth building. One match produces one
record: which six heroes played, which side they were on, which side won, and three
per-hero performance numbers.

**Training.** Use the post-match summary - where identity is confirmed by OCR - as ground
truth to measure how well image matching performs on the *other* screens (draft, prematch
locked) that Modes B and C will depend on, and to learn per-hero transforms for them.

The second job is why Mode A does work its own output does not need. Modes B and C run
under time pressure with no OCR fallback available, so their matchers have to be right the
first time. Mode A is the only mode that can generate labelled data for them, because it is
the only one that ever sees confirmed answers.

## 2. Why this shape (and what we rejected)

The obvious design watches the draft, reads the 20-hero pool, tracks picks as they land,
and captures the locked comps. We built the vision layer for exactly that in Phase 1 and
then measured why it is the wrong thing to automate:

- **The draft is a 15-25 second window under time pressure.** Every read is racing a
  countdown, and a missed frame loses the match entirely.
- **Spectate cannot see the whole pool.** The betting panel covers the fourth row of the
  grid. Verified by comparing a compete frame and a spectate frame *of the same theme*
  ("Fierce Duel"): compete shows 5x4 = 20 cards, spectate shows 5x3 = 15. Independently
  confirmed by the user observing a locked pick that was not among the 15 visible cards.
  So any pool we record from spectate is not merely incomplete, it is **biased** - the
  hidden row is hidden systematically, not at random.
- **The posted odds carry no information.** They are parimutuel, derived from other
  players' bets. They measure crowd opinion, not game state, so they are useless as a
  training signal.
- **The post-match summary has everything we actually want, with no time pressure.** It
  is a static screen that waits for input.

So Phase 2 **derives its data from the summary**. It still captures the draft and prematch
screens, but only as audit material (section 5.4) - no recorded outcome depends on reading
them successfully.

### What this design knowingly gives up

- **No pool.** We never learn "hero X was available and both players passed on it."
- **No odds.** We cannot ask "was the crowd wrong, and where."

Both are acceptable for a win-rate model and both are recoverable later from compete
mode, which does show all 20 cards. Recording that is out of scope here.

Note however that because section 5.4 saves the draft frames unconditionally, the 15
visible pool cards are **preserved on disk** even though nothing parses them today. If pool
data later turns out to matter, it can be extracted offline from the archive rather than
requiring another night of collection.

## 3. Navigation flow

### Startup: establish a known-good state, once

Before the loop begins, the mode does what every other AFK Journey mode does:

```python
self.start_up(device_streaming=False)   # screencap, NOT the H264 stream - see below
self.navigate_to_world()                # known-good screen, wherever we started
```

**Streaming is disabled deliberately.** H264 costs nothing for template matching - the same
six cards scored within 0.002 on stream versus screencap - but it measurably degrades OCR
of stylised text. On the identical summary screen the header OCR'd as `'DefeatVictory'`
from screencap and as `'Defe'` + `'featVictory'` from the stream. Since OCR is what
establishes ground truth in this mode, that loss is not acceptable, and the mode is not
latency-sensitive: it polls every 2 seconds and needs only a handful of frames per match.

This also matches existing practice rather than inventing a rule - six AFK Journey mixins
already pass `device_streaming=False`, and they are exactly the OCR/precision-heavy ones:
`guild_member_scan`, `arena`, `supreme_arena`, `dailies`, `dream_realm`, and the custom
routine.

`navigate_to_world()` (`navigation.py:38-73`) is not a convenience wrapper. It handles
exactly the "we could be anywhere" problem, and it must not be reimplemented:

- **Homestead** - a genuinely different world - is handled explicitly via
  `navigation/homestead/{homestead_enter, homestead_invaded, world}.png` and
  `_handle_homestead_world` (`navigation.py:120-128`).
- **The game entry / notice screen**, quick-purchase popups, guide overlays, login claim
  prompts, battle exits, Arcane Labyrinth screens and the Resonating Hall all have
  templates in `_get_overview_navigation_templates()` (`navigation.py:82-102`).
- **Any unrecognised screen** - being deep inside some *other* event, for instance - falls
  through to `handle_popup_messages()` and otherwise **presses back**, then loops
  (`navigation.py:110-119`). Repeated backing out is what eventually surfaces a known
  anchor, so no per-event knowledge is needed.
- It retries up to 40 times, **restarts the game** if it is not running or after 20 failed
  attempts, and raises `GameNotRunningOrFrozenError` rather than spinning forever.

That covers homestead, foreign event screens, blocking popups and a dead game - the four
ways an unattended start actually goes wrong. `SunlitShowdownMixin` uses the same two calls
(`sunlit_showdown.py:29-30`).

**Returning IS the completion signal.** `_navigate_to_overview` loops until
`_handle_overview_navigation` confirms arrival, then returns the `Overview` reached
(`navigation.py:59-79`); failure raises rather than returning early. So the caller does not
need to poll for "are we there yet" - if the call returns, the transition finished,
including any loading screen. `navigate_to_current_overview()` is also available to read
which overview we are in *without* forcing a move.

**Tested on device, not assumed.** Two starting states, same unmodified
`navigate_to_world()` call:

| start state | matched template | confidence | elapsed |
|---|---|---|---|
| Settings screen | `arcane_labyrinth/back_arrow.png` | 99.5% | ~2s |
| Homestead | `navigation/homestead/world.png` at (1011,1594) | 99.0% | ~6s |

The Settings case is the important one: it matches none of the overview templates, so it
exercised the press-back fall-through and still recovered. The Homestead case confirms the
6-second figure includes the full world reload - the call did not return early into a
loading screen. Homestead is effectively a separate world with its own load, and it is
handled by an existing template rather than anything we need to write.

### Reset policy

Recovery cannot be unbounded, or an unattended run can spend the night quietly retrying.

| level | trigger | action |
|---|---|---|
| step | an expected screen does not appear within its timeout | retry the step |
| cycle | the expected screen still absent after **60 seconds** | abandon the match, `navigate_to_world()`, restart the loop from step 1 |
| mode | **3 consecutive** cycle-level restarts with no successful match | stop the mode and raise |

The counter resets on any successfully recorded match, so a single bad match cannot
accumulate toward the limit across an otherwise healthy night. Three consecutive failures
means something structural has changed - a game update moved a button, the event ended, the
device is wedged - and continuing would produce nothing but noise. Stopping with a clear
error is the correct outcome; silently looping is not.

The restart limit is a setting, defaulting to 3.

**This step runs once, at mode start - not per match.** After a match completes, the exit
path already lands on the overworld, which *is* a known-good state, so the loop re-enters
directly at step 1 below. `navigate_to_world()` is re-invoked only on an error path, when
the mode has lost track of where it is.

### The per-match loop

**Verified end to end over ADB on 2026-07-26.** Every step below was executed by driving
the device directly, and the full 701-frame recording is archived at
`/mnt/vault/solstice/live/navflow/raw/`. The run confirmed three things the design depends
on: the chain reaches a live match, a match can be caught early enough to capture its draft
phase (entered at countdown 24), and the exit path lands back on the **overworld** - a
known-good state - so the loop restarts at step 1 without re-running `navigate_to_world()`.

1. Hamburger menu -> Events
2. Solstice Clash
3. **Fortune Picks** icon
3a. **Three possible branches after Fortune Picks**, depending on where the character is
    standing. All three were executed and observed on 2026-07-26 and all converge on the
    same NPC dialog, so the mode handles them uniformly: *tap the popup if present, then
    poll for the NPC dialog.*

    | branch | condition | observed timing |
    |---|---|---|
    | already at the NPC | standing next to it | dialog immediate |
    | nearby, same waystone area | short walk, **no popup** | avatar walks, dialog at **~4s** |
    | far away, different area | **teleport popup** appears | ~12s including auto-path |

    The teleport popup reads *"Teleport to the Waystone closest to the target?"* with an X
    and a green check; tap the **green check**.

    **Poll every 1 second, with a 30-second timeout, in every branch.** The middle branch
    is what makes a fixed sleep wrong: there is no popup to detect and no signal that
    anything is happening, so code assuming an immediate dialog would silently fall
    through. Polling resolves the fast cases in ~4s regardless of the ceiling.

    The timeout is 30s, not 10s: the far branch was *measured* at ~12s, so a 10s limit
    would fail a verified-good path. The measured worst case gets roughly 2.5x headroom
    because travel time depends on distance and we have sampled exactly one far position.

    Captured frames: `summary/teleport_dialog.png` (the popup at native 1080x1920),
    plus full recordings at `live/teleflow/raw/` and `live/walkflow/raw/`.

    **Reuse the existing handler.** `games/afk_journey/popup_message_handler.py` already
    does exactly this job - OCR the dialog text, match it against a registered
    `PopupMessage`, tap the confirm button. The teleport dialog is registered as one more
    `PopupMessage` entry rather than hand-rolled detection.

    The dialog also offers a *"Don't remind for 7 days"* checkbox which would suppress it
    entirely. `PopupMessage.has_dont_remind_me` must be left **False**: when True the
    handler actively taps that checkbox (`popup_message_handler.py:278-280`), which is a
    persistent change to the user's game settings. Silently altering the user's game state
    is out of scope for a collector - if the user wants the reminder suppressed, that is
    their call to make in-game.
4. **Spectate Live**
4a. **If the match is still in its draft phase**, capture one draft frame **late in the
    draft, when as many pick slots as possible are filled** (up to five), plus one
    prematch-locked frame, for cross-screen training (section 5.4). The training targets
    are the pick slots, so an early frame with an empty strip is worthless - capture as
    late as possible before the screen changes. If we entered mid-match, skip this
    entirely: it is optional material, never a precondition.
5. Wait for the match to end, **polling every 2 seconds** for the result screen. The end
   state is identified by three co-occurring signals: the **Back** button, the
   **chart/details** icon to its left, and the **"... WINS!" / "... LOSES!"** banner text.

   A 2-second interval is deliberate. Combat runs for minutes and the result screen waits
   for input, so there is nothing to miss - polling faster would burn CPU decoding frames
   for no gain, and this is a mode meant to run for hours.

   **The timeout must be passed explicitly.** `wait_for_template(timeout=None)` falls back
   to `self.template_timeout`, which comes from settings and defaults to **10 seconds**
   (configurable max 60s) - far shorter than a match. Called with the default it would
   raise `GameTimeoutError` on every normal match. The call is therefore:

   ```python
   self.wait_for_template(
       "event/solstice_clash/result_back.png",
       delay=2.0,
       timeout=MATCH_TIMEOUT_SECONDS,   # minutes, not the 10s default
       timeout_message="no result screen - abandoning this match",
   )
   ```

   `MATCH_TIMEOUT_SECONDS` is a setting. On timeout the mode abandons the match and goes
   to the cycle-level reset (section 3, reset policy) rather than propagating the error.
6. Tap the chart/details icon -> match summary
7. Identify heroes, read screen data, record
8. Tap the **back arrow** (bottom left of the summary) -> returns to the result screen
9. Tap the green **Back** button -> overworld
10. Repeat from step 1

The result screen carries three controls, left to right: a **back arrow**, the
**chart/details** icon, and the green **Back** button. Only the chart icon and the green
Back are used from this screen; the summary's own back arrow is what closes the summary.

Each match therefore costs one full navigation chain rather than a single tap. That is
acceptable: the chain is a handful of template waits against a static overworld, and it
has the advantage of always re-entering from a known state instead of assuming where the
previous match left us.

Step 5 is a `wait_for_template` on the result screen, which means the mode **sleeps
through the entire combat** rather than polling. This is the single biggest efficiency
win in the design: no frame analysis happens between entering a match and its result.

## 4. Screens

A `screen` table gives each screen an id and a description, and transform parameters
reference it. Phase 2 registers one screen: `solstice_summary`. The Phase 1 screens
(`draft_selecting`, `prematch_locked`) are registered too, so existing `cell_type`
strings migrate onto a proper foreign key instead of a free-text column.

### The summary screen (measured, 1080x1920)

| element | geometry |
|---|---|
| hero card centre x | 90 |
| ally card centre y | 476, 566, 656 |
| enemy card centre y | 1123, 1215, 1307 |
| crop (current best) | x +-26, y -18 .. +30 |

Also on screen and to be recorded: the **Defeat / Victory** header with both player
names, and three stat bars per hero.

**Reading the winner is positional, not textual.** A full-screen OCR of the summary
returns the header as a single merged block `'DefeatVictory'` - the two words are
adjacent and get joined, so the string alone cannot say which side won. The winner must
therefore be determined by **which half of the header band contains "Victory"**, either by
OCR'ing the left and right halves separately or by using the returned bounding box. A
naive substring test would be a coin flip.

Measured full-screen OCR of the summary, for reference:
`['Solstice Clash', 'DefeatVictory', 'Faust', '莉奈', 'Ally', '699K', '0', '2924K', ...,
'Enemy', '2459K', '1828K', '10,500K', ..., 'Replay']`

Note that both player names read correctly including CJK (`莉奈`), and every stat number
parses, including thousands separators (`10,500K`) which the number parser must strip.

**The theme is NOT on this screen.** The spec records `theme` per match, but the summary
shows only the title, the header, the two rosters and the stats - no theme anywhere. The
theme is displayed on the *draft* screen ("Current Theme: Converging Paths"), which this
mode never visits. It must therefore be read **once per session** from the Solstice Clash
event screen during navigation and attached to every match collected in that session.
Recording matches without a theme would silently mix balance epochs, which is the exact
failure the epoch field exists to prevent.

**`Ally` / `Enemy` to blue/red mapping - evidenced, needs one more confirmation.** The two
roster panels are labelled Ally and Enemy, which are spectator-relative labels, while the
result banner is phrased in terms of "BLUE". Measured mean channel values on the captured
summary:

| region | B | R | reads as |
|---|---|---|---|
| banner left half | 158.6 | 106.9 | blue |
| banner right half | 94.5 | 192.0 | orange |
| Ally tab | 167.7 | 121.7 | blue |
| Enemy tab | 95.1 | 184.6 | orange |

**The tab colours are static UI, NOT a per-match signal.** Measured across two different
matches with different winners:

| | summary_01 | summary_02 |
|---|---|---|
| Ally tab | B=167.71 R=121.73 | B=167.67 R=121.72 |
| Enemy tab | B=95.10 R=184.57 | B=95.10 R=184.57 |

Identical to within 0.05. The Ally tab is simply drawn blue and the Enemy tab orange,
always. An earlier draft of this spec proposed deriving side from tab colour; that would
have appeared to work while actually reading a constant. It is not a usable discriminator
and must not be implemented.

**What is actually established, and how.** Confirmed on two matches with known winners:

| match | banner | header left | header right | Ally panel |
|---|---|---|---|---|
| summary_01 | - | Faust, Defeat | 莉奈, Victory | Faust's heroes |
| summary_02 | `BLUE LOSES!` | OdieLuvr69, Defeat | Caffu, Victory | OdieLuvr69's heroes |

In summary_02 the result banner independently says blue lost, and the left player lost. So
in spectate: **blue = left player = the Ally panel**, verified twice, once against an
independent banner.

The mode therefore reads the winner from **which side of the header carries "Victory"**
(section 4), and maps the Ally panel to the left/blue player. The `Ally` / `Enemy` words
are never parsed - they are spectator-relative and meaningless when watching two other
players.

**This mapping is verified for SPECTATE ONLY.** In compete, `Ally` means the player's own
side and the game assigns that side at random, so a compete run could have `Ally` = red.
Mode A only ever spectates, so it is safe here - but Modes B and C must re-verify this
before recording compete outcomes. Getting it wrong there would invert every result
silently: no crash, no failed test, just a corrupted training target.

### The three stat bars - semantics UNVERIFIED

The columns are headed by a **sword**, a **heart**, and a **shield**. Damage dealt and
healing are the obvious readings of the first two; the third is genuinely ambiguous
(damage taken? damage blocked? shielding applied?). They are therefore stored as
`stat_sword`, `stat_heart`, `stat_shield` - names that describe what is on screen rather
than a guess at meaning. Renaming them is a follow-up once confirmed against a watched
match. Values read fine (e.g. `699K`, `0`, `2924K`).

## 5. Identification

### Primary: image match, with the Phase 1 rule unchanged

Accept only when `score >= 0.70 AND margin >= 0.10`. The margin is what catches errors:
every wrong match observed in Phase 1 had a collapsed margin of 0.01-0.04.

**Measured on the summary screen** (one match, six cards, crop x+-26 y-18..+30):

| card | truth | score | margin |
|---|---|---|---|
| ally1 | atalanta | 0.871 | 0.359 |
| ally2 | igor | 0.913 | 0.239 |
| ally3 | indris | 0.876 | 0.163 |
| enemy1 | baelran | 0.798 | 0.278 |
| enemy2 | pippa | 0.871 | 0.306 |
| enemy3 | solise | 0.781 | 0.201 |

6/6 accepted, every one correct, confirmed independently by OCR (section 5.2).

**Verified on the real capture path.** The same six cards were matched on an H264
`DeviceStream` frame, which is what the tool actually uses, versus the lossless
`screencap` PNG. Maximum difference: **0.002**. Compression does not degrade
identification, so these numbers are not optimistic.

### 5.2 Verification mode: long-press EVERY card and OCR the name

**Default ON.** Every card is long-pressed and OCR'd regardless of how well it matched,
and the OCR name is treated as ground truth. The image match still runs first, and both
answers are recorded.

The reason is not distrust of the current numbers, it is their sample size: six cards,
one match. A confident false positive - the matcher naming the wrong hero at 0.85 with a
healthy margin - is the failure mode that silently poisons a dataset, and nothing we have
measured so far could have detected it. Running both channels makes that failure
observable instead of invisible.

**Record every comparison, not only the disagreements.** Logging misfires alone yields a
numerator with no denominator: three errors is meaningless without knowing whether it was
three in fifty or three in five thousand. Every card writes a `identification_audit` row,
agreements included, so the false-positive rate is computable.

On a disagreement, additionally capture: the full frame (saved to disk, path recorded),
the matcher's answer and its score and margin, the OCR answer, and the library art file
the matcher chose. That is enough to reproduce the misfire offline without the device.

**Graduation.** Verification mode is a setting, not a permanent cost. Once the measured
false-positive rate over a large sample justifies it, it can be turned off, at which point
5.2b applies and the loop speeds up by roughly 18s per match.

**Side benefit:** with every card OCR-confirmed, the learning gate in 5.3 is satisfied on
every sighting, so optimal per-hero transforms accumulate across the whole roster as a
by-product rather than one hero at a time.

### 5.2b Fallback when verification mode is off

On rejection (`unknown`), long-press that card and OCR the hero name from the popup.

Verified working: a long-press at the card centre opens a tooltip containing the hero name,
power, class, and skill levels. RapidOCR read all six names exactly - Atalanta, Igor,
Indris, Baelran, Pippa, Solise - matching the image matcher 6/6.

**Press duration: 3000ms.** An 800ms press happened to work in the first test, but user
testing found short presses can fail to open the popup while over-long presses do no harm
at all. The cost is asymmetric - a press that is too short costs a failed read plus a
retry cycle, a press that is too long costs only the extra wall-clock - so the correct
choice is to bias long rather than to tune for the minimum that worked once.

At six cards this is ~18s per match in verification mode. That is the dominant per-match
cost of the mode and it is accepted deliberately, because the alternative is retries that
cost more and sometimes still fail.

The popup renders **downward from ally cards and upward from enemy cards**, so its
position is not fixed. Detection is therefore by content, not geometry: run OCR and check
whether any returned string matches a known hero name.

**Name matching uses `StringHelper.fuzzy_substring_match`** (`util/string_helper.py:51`),
already used by the popup handler, rather than exact string equality. OCR of a stylised
name can drop or substitute a character, and an exact match would throw away a perfectly
identifiable read. Two constraints on using it:

- It is a **substring** match, so a short hero name can match inside unrelated text. Run it
  against the OCR blocks from the popup region only, never the whole frame.
- It returns a bool, so it cannot rank. Score every hero name against the block and take
  the **single best** match, rejecting the read if two names tie - a tie means the OCR was
  too degraded to be ground truth, and ground truth is the whole point here.

**Retry policy:** up to 2 additional attempts per card if no popup is detected. If all
attempts fail, record the hero as `unknown` and continue - a partially identified match
is still recorded, never discarded.

### 5.3 Learned per-hero transforms

A hero that matches in the mediocre band (`>= 0.70` but `< 0.80`) is accepted, but its
transform is a candidate for tuning.

**Learning is gated on long-press confirmation.** This is the important constraint. If
we optimised the transform for whichever hero the matcher named, and the matcher was
wrong, the optimiser would make the wrong answer score *better* - potentially pushing it
past the threshold and suppressing the very check that would have caught it. The
optimiser amplifies whatever it is pointed at, including an error.

So the rule is: **a transform is only ever learned from an identity that a long-press
confirmed.** Sequence, once per hero per screen:

1. Hero matches at 0.70-0.80.
2. Long-press to establish the name as ground truth.
3. If OCR confirms the matcher's answer, search crop/scale for the parameters that
   maximise **margin** (not raw score - see below), and store them.
4. If OCR contradicts the matcher, record the correct hero and log the disagreement.
   Store nothing; a contradiction means something is wrong that tuning would paper over.

On every later sighting, the stored transform applies and the hero scores high without
any long-press.

**Optimise margin, not raw score.** A hero at 0.78 with 0.20 margin is safer than one at
0.85 with 0.05. The 0.80 target is a goal, not the acceptance criterion; the acceptance
criterion stays `score >= 0.70 AND margin >= 0.10`.

**What "wiggle" means here.** `matchTemplate` already searches x/y offsets internally for
free - fixing the offset instead of the scale is what dropped Temesia from 0.978 to 0.408
in Phase 1. So the parameters worth tuning are the **crop rectangle** (which part of the
card is used as the template - a property of the screen) and the **scale** (which varies
per hero). The per-hero row stores scale; the screen row stores the crop rect.

**Free speedup:** a known per-hero scale collapses the scale chain from ~35 steps to 1,
so identification gets roughly 30x cheaper as the table fills.

### 5.4 Cross-screen training against confirmed truth

The summary confirms the identity of the six picked heroes. Those same six heroes also
appear on the **draft** screen (as the numbered picks) and on the **prematch locked**
screen. So every completed match yields up to twelve extra labelled identifications on
screens whose accuracy has never been measured against ground truth.

**The draft GRID is not a training target.** Only 3 of its 4 rows are visible in spectate
(section 2), so it is systematically biased, and Phase 1's own vision module warns that
spectate and compete do not share grid geometry - reading compete draft cells off a
spectate frame "would silently produce nonsense". No spectate grid geometry is defined and
none is needed.

**The training targets are the pick slots instead.** The spectate draft screen carries a
top strip of six pick slots (Blue 1 / Blue 4 / Blue 5, Red 2 / Red 3 / Red 6) which fill in
as picks land. Up to five are locked while the draft is still running; the sixth - and any
that were missed - come from the prematch locked screen. Every one of those six is
confirmed later by the summary, so all six are usable as labelled training data, and none
of them depend on the grid.

Each match therefore:

1. Captures one **draft** frame (late enough to have up to five picks locked) and one
   **prematch locked** frame, saving both to disk unconditionally.
2. Runs identification on the **six pick-slot cells**, taking each hero from whichever
   screen first shows it locked.
3. After the summary confirms the six heroes, scores those earlier reads against the
   confirmed answers and writes an `identification_audit` row per cell, tagged with the
   screen it came from.
4. Learns/updates `hero_screen_transform` rows for the draft and prematch screens, gated on
   the same confirmation rule as everything else (5.3).

**Frames are saved unconditionally, not only on mismatch.** A mismatch is discovered
minutes later, when the match is over and the screen is long gone - by then the frame is
unrecoverable. Storing every draft/prematch frame is the only way to have something to
optimise against when a mismatch shows up.

Two constraints this creates, neither of them optional:

**Constraint 1 - the loop must catch matches early.** "Spectate Live" can drop us into a
match already in progress, in which case there is no draft phase left to capture. This must
be handled as *normal*, not as an error: if the draft or prematch screen was never seen,
the match still records its outcome and simply contributes no cross-screen audit rows. The
mode must never skip recording an outcome because the training capture was missed.

**Constraint 2 - retention has to be bounded.** Two 1080x1920 PNGs per match at roughly
2.5MB each is about 5MB/match; a night of a few hundred matches is on the order of 1-2GB.
That is affordable on `/mnt/vault` (3.6TB free) but not unbounded. Frames are written under
`/mnt/vault/solstice/training/<date>/` and pruned by policy: keep everything for matches
with a disagreement, keep a rolling sample of agreements, and delete the rest beyond a
configurable age. Never write these to `/tmp`, which is a 16GB tmpfs and has already been
filled once by this project.

**Do not copy `guild_member_scan`'s screenshot handling wholesale.** That mixin does
`shutil.rmtree(self._screenshot_dir)` at startup (`guild_member_scan.py:52-53`), which is
correct for throwaway debug output and catastrophic here - it would delete every previously
archived training frame at the start of each run. Training frames accumulate across runs by
design; only the pruning policy above ever removes them.

**Geometry still to be measured.** Two new screens must be registered with their own cell
rows, measured from frames already captured - the Phase 1 compete geometry must NOT be
reused for either:

| screen | cells | source frames |
|---|---|---|
| `spectate_draft_picks` | 6 pick slots in the top strip | `live/match01/raw/000039317.png`, `frames/182527_374.png` |
| `spectate_prematch` | 6 locked cards, 3 per side | `live/match01/raw/000104002.png` |

**What this cannot validate.** The summary confirms only the six *picked* heroes. The
unpicked cards in the draft grid are never confirmed by anything, so accuracy on them
remains unmeasured - but since the grid is not read at all, that is a gap in coverage, not
a source of bad data.

### 5.5 What Modes B and C inherit

Mode A exists partly to make the later modes possible, so the component boundaries are
drawn around what they will need - not around what Mode A alone would find convenient.

Modes B and C read **comps from the prematch locked screen** and the **winner from the
post-match result banner**. Neither opens the summary: Mode C in particular has to know the
comps *before* the match resolves, and the summary does not exist yet at that point.

That gives three reusable pieces, each usable independently of Mode A's loop:

| piece | used by | notes |
|---|---|---|
| prematch comp reader | A (audit), B, C | the one Mode A validates and tunes |
| result-banner winner reader | A (cross-check), B, C | reads `... WINS!` / `... LOSES!` |
| summary parser | A only | ground truth source; not on B/C's path |

So the prematch reader is the component that actually matters long-term, and it is exactly
the one with no OCR fallback available at runtime. Mode A's whole training job is to get
its per-hero transforms measured and tuned **before** B and C depend on it.

**Modes B and C still record outcomes.** They write the same `match` / `match_hero` rows,
sourced from prematch + banner instead of the summary. The `source` column distinguishes
them, so a later analysis can weight or exclude records by how their identities were
established. Mode A's records are the only ones with OCR-confirmed identities; that
distinction must survive into the data.

## 6. Data model

### New tables

`migrate.py` executes `schema.sql` on **every** run and relies on `CREATE TABLE IF NOT
EXISTS` throughout (`migrate.py:72`). All new tables must follow that or the second
migration run fails with `table screen already exists`, breaking the idempotency
requirement in section 9.

```sql
CREATE TABLE IF NOT EXISTS screen(
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,   -- 'solstice_summary'
    description   TEXT NOT NULL,
    base_resolution TEXT NOT NULL,        -- '1080x1920'
    crop_half_w   INTEGER,                -- card crop, relative to cell centre
    crop_top      INTEGER,
    crop_bottom   INTEGER
);

-- Declared BEFORE hero_screen_transform, which carries an FK to it. SQLite tolerates a
-- forward reference at CREATE time but fails on INSERT once foreign_keys is ON, so the
-- order in schema.sql is load-bearing, not cosmetic.
CREATE TABLE IF NOT EXISTS identification_audit(
    id            INTEGER PRIMARY KEY,
    -- Nullable: an audit row can be written before the match row is committed, and a
    -- match that fails to record entirely should still leave its audit evidence behind.
    match_id      INTEGER REFERENCES match(id) ON DELETE CASCADE,
    screen_id     INTEGER NOT NULL REFERENCES screen(id),
    side          TEXT NOT NULL,          -- 'blue' | 'red'
    slot          INTEGER NOT NULL,
    image_slug    TEXT,                   -- matcher's answer, NULL if rejected
    image_art_ref TEXT,                   -- which library art the matcher chose
    image_score   REAL NOT NULL,
    image_margin  REAL NOT NULL,
    ocr_slug      TEXT,                   -- OCR answer, NULL if no popup
    agreed        INTEGER NOT NULL,       -- 1 | 0 - the thing we are measuring
    frame_path    TEXT,                   -- full frame saved ONLY on disagreement
    created_at    TEXT NOT NULL,
    -- 'agreed' must MEAN what it says. Without this, a row could be written with
    -- agreed=1 while image_slug and ocr_slug disagree, and that row would then be
    -- accepted as confirming evidence by the trigger below - laundering a contradiction
    -- into a learned transform.
    CHECK(agreed IN (0, 1)),
    CHECK(agreed = 0 OR (image_slug IS NOT NULL
                         AND ocr_slug IS NOT NULL
                         AND image_slug = ocr_slug))
);

CREATE TABLE IF NOT EXISTS hero_screen_transform(
    id            INTEGER PRIMARY KEY,
    screen_id     INTEGER NOT NULL REFERENCES screen(id),
    hero_slug     TEXT NOT NULL REFERENCES hero(slug),
    art_ref       TEXT NOT NULL,          -- which art won (base or a named skin)
    scale         REAL NOT NULL,
    -- Per-hero crop overrides. NULL means "use the screen default". Measured:
    -- the optimum crop differs per hero (solise hw=22 bot=26, indris hw=22 bot=32,
    -- baelran hw=24 top=14), so a single screen-level crop leaves accuracy on the table.
    crop_half_w   INTEGER,
    crop_top      INTEGER,
    crop_bottom   INTEGER,
    score         REAL NOT NULL,          -- score achieved when learned
    margin        REAL NOT NULL,          -- margin achieved when learned
    -- The gate from 5.3, enforced by the DATABASE rather than by convention. Without the
    -- CHECK, 'self' is accepted and the "cannot be written with confirmed_by='self'" test
    -- in section 9 would be asserting something the schema does not actually prevent.
    confirmed_by  TEXT NOT NULL CHECK(confirmed_by IN ('longpress_ocr')),
    -- NOT NULL: a nullable audit_id would let a row claim 'longpress_ocr' with no
    -- confirming evidence at all, which is precisely what 5.3 forbids.
    audit_id      INTEGER NOT NULL REFERENCES identification_audit(id),
    verified_at   TEXT NOT NULL,
    UNIQUE(screen_id, hero_slug, art_ref)
);

-- NOT NULL only proves an audit row EXISTS. It cannot prove that the row agrees, has an
-- OCR answer, names this same hero, or came from this same screen - SQLite CHECK cannot
-- reference another table. A trigger can, so the remaining conditions are enforced here
-- rather than left to the calling code to remember.
CREATE TRIGGER IF NOT EXISTS hero_screen_transform_confirm_insert
BEFORE INSERT ON hero_screen_transform
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'transform requires an agreeing OCR-confirmed audit row for the same hero and screen')
    WHERE NOT EXISTS (
        SELECT 1 FROM identification_audit a
        WHERE a.id        = NEW.audit_id
          AND a.agreed    = 1
          AND a.ocr_slug  = NEW.hero_slug
          AND a.screen_id = NEW.screen_id
    );
END;

-- Section 5.4 says transforms are "learned/updated", and re-tuning an existing row is an
-- UPDATE (or an upsert's DO UPDATE). A BEFORE INSERT trigger alone would let an update
-- swap audit_id to unconfirmed evidence without ever firing - the gate would hold on the
-- first write and leak on every subsequent one.
CREATE TRIGGER IF NOT EXISTS hero_screen_transform_confirm_update
BEFORE UPDATE ON hero_screen_transform
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, 'transform update requires an agreeing OCR-confirmed audit row for the same hero and screen')
    WHERE NOT EXISTS (
        SELECT 1 FROM identification_audit a
        WHERE a.id        = NEW.audit_id
          AND a.agreed    = 1
          AND a.ocr_slug  = NEW.hero_slug
          AND a.screen_id = NEW.screen_id
    );
END;

```

Every identified card writes one `identification_audit` row. Agreements are cheap (no
frame saved) and are what make the false-positive rate computable; disagreements also
persist the frame so a misfire can be reproduced offline without the device.

`confirmed_by` exists so the gate in 5.3 is enforceable *in the data*, not only in the
code path that writes it. A row that cannot name its confirming evidence is a bug.

### Extensions to existing tables

`CREATE TABLE IF NOT EXISTS` does **not** add columns to a table that already exists. The
repo handles this with an explicit `ADD_COLUMNS` list in `migrate.py:26-42`, applied to
databases that predate a column; `schema.sql` carries the same columns in the `CREATE` for
fresh databases. Both places must be updated or Phase 2 will fail with `no such column` on
the existing v2 database.

New columns, to be added in **both** `schema.sql` and `ADD_COLUMNS`:

```python
("match",      "theme",         "TEXT"),
("match",      "blue_player",   "TEXT"),
("match",      "red_player",    "TEXT"),
("match",      "blue_rank",     "INTEGER"),
("match",      "red_rank",      "INTEGER"),
("match_hero", "stat_sword",    "INTEGER"),
("match_hero", "stat_heart",    "INTEGER"),
("match_hero", "stat_shield",   "INTEGER"),
("match_hero", "power",         "INTEGER"),
("match_hero", "identified_by", "TEXT"),
```

Player identity is recorded because player skill is a real confound - without it, a strong
player's hero picks look like strong heroes. `identified_by` is `image` or `longpress_ocr`.

Note that `ADD_COLUMNS` entries must be additive and nullable: SQLite cannot add a
`NOT NULL` column without a default to a populated table, and existing v2 rows have no
values for any of these.
- `MatchStore._SOURCES`: add `spectate_summary`. The existing set is
  `{compete, spectate}`; Phase 2 records are `spectate_summary` so that OCR-confirmed
  identities remain distinguishable from the prematch-derived records Modes B and C will
  write later (section 5.5). The set is a validation whitelist, so every new mode must add
  its own value - a mode writing an unregistered source raises rather than silently
  persisting an unknown provenance.

### The POOL_SIZE bug

`store.py` hardcodes `POOL_SIZE = 20` and `pool_is_complete()` checks against it. Phase 2
records no pool, so this is not on the Phase 2 path - but it is wrong for any spectate
pool (15 visible) and must not be used to validate one. Left as-is, documented here.

## 7. Unattended operation

This runs for hours with nobody watching, so failure handling is a feature, not an
afterthought.

| situation | response |
|---|---|
| unexpected screen | back out, re-navigate the chain from Events, continue |
| summary unreadable | record the match with winner only, continue |
| one hero unidentifiable after retries | record that slot `unknown`, keep the rest |
| no match available to spectate | wait and retry the Spectate Live entry |
| device/stream dies | stop cleanly with a clear log line; do not spin |

**The loop must never abort the run because of one bad match.** Every recoverable failure
is logged with enough context to diagnose later, and the loop moves to the next match.

**Duplicate protection.** Phase 2 does **not** invent a key format. The canonical
`natural_key` is already specified in `docs/solstice-clash/README.md` and must be used
verbatim:

```
sha1(norm(blue_player) | blue_rating | norm(red_player) | red_rating |
     sorted(blue_hero_slugs) | sorted(red_hero_slugs) | theme | outcome | time_bucket)
```

`norm()` lowercases and strips non-alphanumerics; `time_bucket` is capture time rounded to
30 minutes. The time bucket is what stops two genuine rematches between the same players,
with the same lineups and the same winner, from collapsing into one row - an earlier draft
of this spec omitted it and would have silently lost those matches.

Two consequences that follow from the existing code rather than from this spec:

- **Conflicts DO NOT update.** `MatchStore.record_match` uses
  `ON CONFLICT(natural_key) DO NOTHING` and then re-selects the existing id
  (`store.py:172-181`). First writer wins. A re-entered match therefore cannot inflate the
  dataset, but it also cannot correct a badly-read one - re-reading is not a repair
  mechanism.
- **A match that cannot form a key gets `natural_key = NULL` and always inserts**
  (`store.py:176-177`). This is exactly the failure cases in the table above: a match with
  an unreadable summary, or with any hero unidentified, has no stable key. Those rows are
  intentionally un-deduped and must be **excluded from cross-contributor sync** until
  labelled, or they would duplicate across machines.

Both behaviours are inherited deliberately. Phase 2 changes neither.

## 8. Components

| file | responsibility |
|---|---|
| `services/solstice/summary.py` | pure: frame in, parsed result out. No device, no taps. |
| `services/solstice/tuning.py` | pure: crop/scale search, margin-maximising. |
| `mixins/solstice_clash.py` | navigation, the loop, taps, retries. Device-facing. |
| `services/solstice/store.py` | extended for the new columns and source. |
| `data/solstice_clash/schema.sql` + `migrate.py` | the two new tables, schema v3. |

The split matters: everything interpretive is pure and testable against saved frames;
everything device-facing is thin enough to read in one sitting.

### Reuse before building - a hard rule for this work

**Before writing any capability, check whether AdbAutoPlayer already provides it.** This
project has repeatedly found the tool already solved something we were about to rebuild
worse. Confirmed reusable components, all verified present in this repo:

| need | existing component |
|---|---|
| screen capture | `DeviceStream` (H264, stream-first) via `get_screenshot()` |
| waiting for a screen | `wait_for_template` |
| menu navigation | `_navigate_menu_chain` |
| dialogs / confirmations | `PopupMessageHandler` + `PopupMessage` entries |
| text reading | `RapidOCRBackend` / `TesseractBackend` |
| GUI registration | `@register_command` + `GUIMetadata` |
| fuzzy name matching | `StringHelper.fuzzy_substring_match` |
| closest precedent, navigation | `SunlitShowdownMixin` (same event family) |
| closest precedent, long OCR scan | `guild_member_scan` (OCR backend selection + fallback, debug OCR dump, screenshot dir, results to JSON) |

New code is justified only where nothing equivalent exists - which for Phase 2 means the
summary parser, the tuning search, and the Solstice-specific navigation. Anything else
should be an existing call, not a reimplementation.

## 8b. Templates required, and what we can cut them from

Navigation primitives already exist and are reused unchanged:
`navigation/hamburger_menu.png`, `dailies/hamburger/events.png`, `navigation/confirm.png`,
`navigation/x.png`.

Solstice-specific templates do **not** exist yet - the only ones present are
`event/solstice_clash/anchors/{draft_selecting, prematch_locked, ban_glyph_*}.png` from
Phase 1. The following must be cut before the loop can run:

All but one are cuttable from frames we already hold. The 1110-frame spectate archive at
`/mnt/vault/solstice/frames/` captured a full navigation run and contains the screens:

| template | source frame |
|---|---|
| events carousel, Solstice Clash tab | `frames/182315_111.png` (bottom strip) |
| Solstice Clash event screen | `frames/182315_111.png` |
| Fortune Picks button | `frames/182315_111.png` (bottom left) |
| Spectate Live button | `frames/182426_146.png` (NPC dialog) |
| result banner (`... WINS!` / `... LOSES!`) | `frames/190923_801.png`, `live/match01/raw/000151423.png` |
| chart/details icon | same result frames |
| green Back button | same result frames |
| summary back arrow | `summary/summary_01.png` |
| teleport confirm dialog | `summary/teleport_dialog.png` (captured over ADB, 1080x1920) |

**No prerequisite captures remain.** The teleport dialog was the last gap and has since been
captured by driving the flow from a distant location. Every template above can be cut from
frames already on disk.

### The theme comes from the event screen

`frames/182315_111.png` also resolves the "theme is not on the summary" problem from
section 4: the Solstice Clash event screen displays **"Current Theme: Fierce Duel"** and
**"Rotates in 7h"**. Since the mode passes through this screen on every navigation cycle,
the theme can be read there - no extra navigation - and attached to the match recorded in
that cycle.

The "Rotates in Nh" countdown is a bonus: it makes the epoch boundary computable rather
than guessed, so matches either side of a rotation can be separated exactly.

## 9. Testing

- **Fixtures**: the captured summary frame plus the six long-press frames, committed.
  Ground truth is known and independently confirmed (image match + OCR agreeing).
- **Unit**: summary parsing returns the six correct heroes, the correct winner, and the
  stat numbers; the accept rule rejects a deliberately degraded card.
- **Unit**: the tuning search improves margin on a card and never returns parameters
  that reduce it.
- **Unit**: the confirmation gate rejects every unconfirmed path. Verified against SQLite
  before this spec was finalised - all six cases behave as specified:

  | insert | expected |
  |---|---|
  | agreeing audit, same hero and screen | accepted |
  | audit with `agreed = 0` | rejected by trigger |
  | audit from a different `screen_id` | rejected by trigger |
  | audit naming a different hero | rejected by trigger |
  | `confirmed_by = 'self'` | rejected by CHECK |
  | `audit_id` NULL | rejected by NOT NULL |
- **Migration**: v2 -> v3 is idempotent and non-destructive; existing rows survive.

Existing Phase 1 tests (46) must stay green.

## 10. Open items

- **Stat bar semantics** (section 4) - the shield column is unconfirmed.
- ~~Tuning headroom~~ **measured.** A crop/scale grid search on the three weakest cards:

  | card | before | tuned | margin | best crop |
  |---|---|---|---|---|
  | solise | 0.781 | 0.866 | 0.244 | hw=22 top=18 bot=26 |
  | baelran | 0.798 | 0.844 | 0.323 | hw=24 top=14 bot=26 |
  | indris | 0.876 | 0.905 | 0.189 | hw=22 top=14 bot=32 |

  0.80 is reachable for all three by crop tuning alone, and the current default crop
  (hw=26, top=18, bot=30) is too generous - every optimum was tighter. The optima differ
  per hero, which is why `hero_screen_transform` carries crop overrides.
- **Skinned heroes on the summary screen** - the six-card sample contained no skin, so
  skin handling on this screen is inferred from Phase 1 behaviour, not measured.
- **Result-screen banner wording.** Observed as both `BLUE WINS!` and `BLUE LOSES!`, i.e.
  the banner is phrased relative to the blue side rather than naming the winner directly.
  The summary header (`Defeat` / `Victory` either side of the two player names) is the
  more direct signal and is the one to parse; the banner is a secondary check.
