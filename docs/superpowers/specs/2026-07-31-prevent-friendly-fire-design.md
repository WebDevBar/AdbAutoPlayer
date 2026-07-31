# Prevent Friendly Fire - Arena and Supreme Arena

Do not attack friends or guild-mates. A per-mode checkbox, off by default.

Written 2026-07-31. Status: design, not yet planned or implemented.

## The problem

Both arena modes currently pick an opponent **positionally**, and neither reads who the
opponent is:

- **Arena** (`mixins/arena.py`, `_choose_opponent`) template-matches `arena/opponent.png`
  inside `CropRegions(right=0.6)` - the left 40% of the screen - and taps the single
  best-scoring match. It never looks at the middle or right cards. The comment
  "Target weakest opponent" is an assumption about the game's ordering, not a measurement.
- **Supreme Arena** (`mixins/supreme_arena.py`, `_sa_choose_opponent`) taps a hardcoded
  coordinate chosen by the `Opponent Position` setting: `Point(165, 950)`, `(540, 950)` or
  `(915, 950)`. It does not template-match the card at all.

So there is no filter to extend. Any friendly-fire guard has to add a READ step that does
not exist today, then a decision, then a fallback.

## Goal and non-goals

**Goal.** When the toggle is on, never initiate a battle against an opponent the game
marks as a Friend or a Guild Member.

**Non-goals.**

- Choosing the *best* opponent. This is a safety filter, not an opponent optimiser.
- Reading names, scores or power. Nothing here needs to know who anyone is.
- Maintaining a friends or guild-member list. The game already labels the cards; a local
  list would be a second source of truth that goes stale.
- Changing behaviour when the toggle is off. Off is the default and the current code path
  must be untouched in that case.

## What the game shows

Measured from frames captured 2026-07-31, archived at
`/mnt/vault/adbautoplayer/arena-friendly-fire/`:

| file | what it establishes |
|---|---|
| `01-friend-badge-middle-card` | Arena, Friend badge on the middle card, Refresh 7/7 |
| `02-guild-member-badge-middle-card` | Arena, Guild Member badge, same card, same y |
| `03-refresh-exhausted-x-button-friend-on-left` | Arena, badge on the LEFT card at a different y; refresh counter replaced by a bare X |
| `04-give-up-confirmation-dialog` | "Give up this challenge?" with X (cancel) and tick (confirm) |
| `05-supreme-arena-select-opponent-no-badges` | Supreme Arena baseline, no badges - the frame that proves a band reads empty |
| `06-supreme-arena-friend-badge-right-card` | Supreme Arena, Friend badge on the right card |
| `07-supreme-arena-guild-member-badge` | Supreme Arena, Guild Member on the right card - same band as Friend |
| `08-supreme-arena-badge-middle-card` | Supreme Arena, Guild Member on the middle card - same width, 67px lower |

### Badges

Two badges, both a solid pill with an emblem and text, centred on their card:

- **Friend** - green, RGB approximately (60, 160, 110)
- **Guild Member** - cyan, RGB approximately (64, 185, 192), wider because the text is longer

When a player is both, the game shows **Friend**. That ordering is irrelevant to the
decision - both mean "do not attack" - and matters only for what the log says.

### Measured geometry

Arena, 1080x1920:

| badge | card | x | y |
|---|---|---|---|
| Friend | middle | 456-644 (w 188) | 954-993 (h 39) |
| Guild Member | middle | 421-679 (w 258) | 953-993 (h 40) |
| Friend | left | 93-281 (w 188) | 1013-1052 |

Both badges occupy the **same y band on the same card**; Guild Member is simply wider.
Cards are staggered vertically, so the band differs per card - the left card sits ~59px
lower than the middle. A fixed y band across all cards would miss badges.

Supreme Arena is a **different screen**, not a variant: angled banner cards, staggered
~100px per step, showing name / power / `Rank +N` / `Top N` with no score bar. Friend
badge on the right card measured at **x 786-922, y 985-1014** (w 137, h 29) - smaller than
Arena's.

### Refresh and give up

- Arena: **Refresh 7/7**. Supreme Arena: **Refresh 5/5**. Per match, not per day.
- When refreshes are exhausted the control becomes a **bare X** in the same position, with
  no counter.
- Tapping the X raises **"Give up this challenge?"** with a white X (cancel, x 555-710) and
  a green tick (confirm, x 795-950), both around y 1245.

**The X forfeits the challenge.** It is not a close button. The bot only reaches it as the
designed last resort, never incidentally.

## Detection

Two independent signals per card. **Either one firing flags the card.** A false positive
costs one refresh; a false negative attacks a friend. Those costs are not comparable, so
the union is deliberate.

### Signal 1 - colour run

Exact predicate, per pixel, on 8-bit RGB. A pixel is badge-coloured if it satisfies
EITHER:

```
Friend (green): g > 120  and  r < 110  and  g - r > 60  and  g - b > 40
Guild  (cyan):  g > 130  and  b > 130  and  r < 110  and  |g - b| < 45  and  g - r > 60
```

A card is flagged when a **connected component** of badge-coloured pixels satisfies ALL of:

```
area   >= 2000 px
width  >= 100 px
height <= 80 px
width / height >= 2.0
```

**Card assignment is by x-range OVERLAP, not by centre.** Each mode defines three card
x-ranges, and a component is assigned to **every card whose range it overlaps**:

| mode | card 1 | card 2 | card 3 |
|---|---|---|---|
| Arena | 40-340 | 395-700 | 755-1060 |
| Supreme Arena | 60-400 | 390-720 | 720-1050 |

**Tap targets.** Selecting a card needs a point, and Arena's existing code can only find
one: it matches `arena/opponent.png` inside `CropRegions(right=0.6)`, the left 40%, so it
has no way to tap card 2 at all.

| mode | how to tap card N |
|---|---|
| Arena | the card's green sword button, located as a connected component within the card's x-range. Measured on frame 01: card 1 at (227, 1433), card 3 at (943, 1314). Staggered with their cards, so LOCATED rather than hardcoded |
| Supreme Arena | the existing hardcoded points, unchanged: (165, 950), (540, 950), (915, 950) |

Those buttons are the same ~8000px round green components the badge detector rejects on
shape, so one pass finds both: the `w/h ~ 0.7` blobs it discards are exactly the tap
targets.

A component overlapping two ranges flags **both** cards. That is deliberate: a centre-based
rule is undefined for a component sitting on a boundary, and the failure mode of guessing
wrong is marking the wrong card safe and attacking a friend. Overlap is deterministic and
errs toward refusing to attack.

All six observed badges fall cleanly inside a single range (Arena left 93-281, middle
456-644 and 421-679; Supreme Arena middle 445-636, right 758-948), so the both-cards case
has never been seen - but it is defined rather than left to an implementer.

**Shape, not position.** An earlier draft searched a y band anchored to the player-name
row. Peer review killed that: nothing in the frames uniquely identifies the name row
against the adjacent score, power and rank text, and "the band above it" has no defined
extent - so the anchored detector was not implementable, and anchoring was the only thing
excluding the sword-button false positives.

A badge is a **wide short bar**; the green battle button is a **blob**. That distinction
needs no anchor, and it makes the vertical stagger irrelevant:

| | w x h | aspect |
|---|---|---|
| badges, all six observed | 191x42, 261x42, 193x41, 141x47, 193x54, 193x54 | **3.00 - 6.21** |
| sword buttons | 111x158, 110x157 | **0.70** |
| other artwork | 48x107 | 0.45 |

The baseline frame produces no qualifying component at all.

**Area alone would NOT work.** The largest badge is 7948px and the smallest sword button
8012px - they overlap. Aspect ratio is what separates them, and a rule written on area
would have passed every frame in this set while being wrong.

Three further properties are load-bearing, each established by measurement:

**It must be green OR cyan.** A green-only rule sails straight past Guild Member.

**Connectivity is 8-way**, and components under 400px are discarded as specks before any
shape test.

**The predicate is exact, not a tolerance.** Codex demonstrated that a "within N of this
RGB" formulation flips the answer with N - an 18px run at one setting, 92px at another -
and one of those settings attacks the friend.

Language-independent, costs microseconds, needs no template file.

### Measured badge boxes

For fixture tests and for validating a detection:

| mode | card | badge | x | y |
|---|---|---|---|---|
| Arena | middle | Friend | 456-644 | 954-993 |
| Arena | middle | Guild | 421-679 | 953-993 |
| Arena | left | Friend | 93-281 | 1013-1052 |
| Supreme Arena | right | Friend | 786-922 | 985-1014 |
| Supreme Arena | right | Guild | 759-948 | 981-1020 |
| Supreme Arena | middle | Guild | 446-636 | 1048-1091 |

Badge width is constant per mode regardless of column, which is a useful invariant.

### Signal 2 - OCR

OCR the card's crop rectangle - defined below - and flag on an exact badge-text match.

**The match rule is exact, for the same reason the colour predicate is.** Leaving it as
"flag on Friend or Guild Member" is the defect the colour arm already had:

- RapidOCR returns **per-box** results. Compare each box's text individually; never
  concatenate boxes and search the result.
- Normalise: strip surrounding whitespace, collapse internal whitespace runs to one space,
  casefold.
- Flag when a normalised box text **equals** `friend` or `guild member`. Equality, never
  substring.
- Ignore boxes whose OCR confidence is below **0.6**.

**Substring matching would attack friends and spare strangers.** The OCR rectangle contains
the opponent's NAME row by design, and names are arbitrary player strings: a substring rule
flags an opponent called "Friendzone" and burns refreshes on them. Per-box equality cannot.

**Both crops are defined explicitly.** Leaving them as "a generous band" and "the whole
card" was a leftover from the abandoned anchored-band design and is not implementable.

- **Colour** runs over the WHOLE FRAME. Connected components are found first and assigned
  to cards afterwards by x-range overlap, so it needs no per-card crop at all and the
  vertical stagger cannot affect it.
- **OCR** runs on a per-card rectangle: the card's x-range from the assignment table,
  crossed with a per-mode y-range covering everything a card can display.

| mode | OCR y-range | covers |
|---|---|---|
| Arena | 900-1300 | badges observed at 953-1052, name and score rows below |
| Supreme Arena | 950-1500 | badges observed at 975-1091, name / power / rank rows below |

The y-ranges are per MODE, not per card, and are wide enough to contain every card's
content at any stagger - which is why they do not need anchoring either. They are
deliberately loose: OCR contamination from a neighbouring card is prevented by the
x-range, and a false "Friend" read costs one refresh.

**The two signals remain scoped differently on purpose.** Colour is frame-wide and
shape-based; OCR is a bounded rectangle and text-based. A geometry surprise is caught by
OCR; a localised client is caught by colour. They share no crop, so they cannot share a
failure.

OCR cost is acceptable here because it reads one card crop, not a full frame - unlike the
long-press OCR that was shelved in Solstice Clash for exactly that reason. **The mode has
no timer**, so a second confirming screenshot before acting is free.

## Selection algorithm

Per attempt, with the toggle on:

1. Screenshot the select-opponent screen.
2. Evaluate **cards 1 and 2 only**. The right card is never considered - it is routinely
   outside the player's power bracket. This is a deliberate product decision, not a
   detection limit.
3. Take the first unflagged card, in **preference order** (see below).
4. If both are flagged: **classify the bottom-right control FIRST** (see "Classifying the
   control" below) - it is either
   Refresh or the X, and it must be positively matched as one of them before anything is
   tapped. If it is Refresh: tap it, take a fresh screenshot, re-evaluate. If it is
   already the X, go straight to step 6 without tapping Refresh.
5. Repeat until a card is taken or the control classifies as the X. Do not count
   refreshes: the limit differs per mode (7 and 5) and could change in a patch. Exhaustion
   is a visual fact, never an inferred one.

   **The control is classified before every tap, not once.** An already-exhausted screen
   on the first pass would otherwise send a Refresh tap into the X and open the
   destructive give-up flow.
6. When exhausted with both still flagged: **check the give-up precondition first** (see
   "Never forfeit on one signal"). If it is not met, stop the mode instead. If it is,
   **positively match the X control** before
   tapping it - never tap the refresh coordinate on the assumption that it has become the
   X. Then confirm the dialog per "The give-up dialog" below. Two positive matches are
   required before anything is tapped, because this path forfeits a daily attempt.
7. **After a give-up, the mode STOPS.** It does not return to the attempt loop. Both run
   loops would otherwise continue - `run_arena` over its 5+2 attempts, `run_supreme_arena`
   over `attempts` - and a saturated opponent pool could forfeit EVERY attempt in the run,
   one give-up per iteration. One forfeit is a bounded cost; a run of them is not, and if
   the pool is that friendly the user should hear about it rather than pay for it.

Before any tap that commits to a battle, take a second screenshot and re-evaluate.

**If the two reads disagree, the card is treated as FLAGGED** and the algorithm returns to
step 3 with that card excluded - so a card seen clean once and flagged once is never
attacked. If that leaves no unflagged card, it refreshes exactly as though both had been
flagged. The frame is archived as a disagreement.

This is the only safe resolution: proceeding on the optimistic read would defeat the
feature, and treating disagreement as fatal would stop the mode over a single noisy frame.

Being slow is free; being wrong is not. The mode has no timer.

## Never forfeit on one signal

The design rests on an asymmetry: a false positive costs one refresh, a false negative
attacks a friend. **That is only true for a TRANSIENT false positive**, and an earlier draft
stated it without the qualifier.

A persistent false positive - a player named "Friendzone" under a substring rule, a wide
green UI element on a screen state none of the eight frames show - reproduces on every
refresh. It drains all 7 or 5, reaches the X, and walks the give-up path, forfeiting a daily
attempt because of a false read. The escalation ladder converts the cheap failure into the
expensive one, which destroys the cost model the whole design rests on.

**Precondition for the give-up path: on the final evaluation, every flagged card must be
flagged by BOTH signals.** If any flag rests on colour alone or OCR alone, stop the mode and
log it rather than forfeiting.

The asymmetry is deliberate. One signal is enough to SKIP a card, because skipping is cheap
and missing a friend is the thing being avoided. One signal is not enough to SPEND AN
ATTEMPT, because a single detector agreeing with itself across refreshes is exactly what a
persistent false positive looks like.

Stopping is the safe failure: the attempt stays unspent and visible, and the archived frames
are there to look at.

## Classifying the control

The bottom-right control is Refresh or the X, and one of those forfeits a daily attempt.
"Positively match it" is not implementable without numbers, so:

**Search region** - the button face, measured in both states and identical whether it shows
Refresh or X:

| mode | x | y |
|---|---|---|
| Arena | 882-1052 | 1724-1864 |
| Supreme Arena | 860-1029 | 1718-1859 |

**Match the GLYPH, not the text.** The refresh state renders "Refresh : 7/7" or
"Refresh : 5/5" beside a circular-arrow icon; the count varies by mode and the word is
localised. The circular arrow and the X are neither.

**The templates are GLYPH crops, strictly smaller than the search region.** An earlier
draft gave the whole button face as the crop box - 170px wide - while Supreme Arena's
search region is 169px. A template wider than the image it is searched in cannot be
matched at all, so the exhausted control would have errored rather than classified. Peer
review caught it by comparing the two numbers.

Glyphs measured as connected components inside each button face: Arena X 55x55,
Supreme Arena X 55x55, Supreme Arena refresh 62x63. An 80x80 crop contains each with
margin and fits inside both search regions.

| template file | cut from | crop box (80x80) |
|---|---|---|
| `arena/refresh_glyph.png` | `01-friend-badge-middle-card-20260731.png` | x 930-1010, y 1745-1825 |
| `supreme_arena/refresh_glyph.png` | `05-supreme-arena-select-opponent-no-badges-20260731.png` | x 905-985, y 1735-1815 |
| `arena/give_up_glyph.png` | `03-refresh-exhausted-x-button-friend-on-left-20260731.png` | x 930-1010, y 1745-1825 |

**Invariant, and it must be asserted in a test:** every template's width and height are
strictly less than its search region's. 80x80 against 170x140 in Arena and 169x141 in
Supreme Arena.

**The refresh glyph is NOT shared between the modes; the X is.** An earlier draft asserted
both modes used the same artwork with only the button box moving. Peer review disproved it
by measurement: the Arena refresh template scores **0.36** against the Supreme Arena
control, far below the 0.8 floor, so Supreme Arena's genuine Refresh would have classified
as "neither" and stopped the mode every time both cards were flagged.

Side by side the difference is plain: **Arena's arrow is anticlockwise and thin, Supreme
Arena's is clockwise and thick.** Different icons.

The X cross-matches at **1.00** between the modes, and both sit at the same offset within
their button face, so one template serves both and `give_up_glyph.png` has no per-mode
variant.

The lesson is recorded because it nearly shipped: a claim that two assets are "the same
artwork" is a measurement, not an observation, and this spec was wrong about it until
someone matched them.

**Threshold and tie-break.** A confidence floor of 0.8 on each template, and then exactly
one of these three outcomes:

| result | meaning | action |
|---|---|---|
| refresh matches, X does not | refreshes remain | tap it |
| X matches, refresh does not | exhausted | proceed to the give-up path |
| both match, or neither matches | **unknown** | re-screenshot and classify once more; if still unknown, stop the mode |

Both-match is treated as unknown rather than resolved by higher confidence. The two glyphs
are visually unalike, so a double match means something is wrong with the read, and the
cost of guessing is a forfeited attempt.

## The give-up dialog

Reached only from step 6, and it spends an attempt, so every element is pinned.

**The dialog is detected by its green confirm tick**, not by its sheet and not by its text.

An earlier draft matched "the blank upper sheet, which is the same in every language".
That was wrong twice over, and peer review caught both: the crop it specified
(x 300-800, y 900-1000) actually CONTAINED the sentence "Give up this challenge?", so it
was language-dependent after all; and a blank cream rectangle is a useless template
regardless, because it carries no information and would match many places at high
confidence. Measured: the sheet has a pixel standard deviation of **4.8**, the tick
**54.5**.

| element | geometry |
|---|---|
| template `arena/give_up_confirm.png` | cut from `04-give-up-confirmation-dialog-20260731.png`, x 786-947, y 1163-1321 |
| search region | x 700-1010, y 1100-1380 |
| confidence floor | 0.8 |
| tap target | the **matched centre**, not a fixed point. Observed at (866, 1241) |
| cancel button, cream circle | x 500-779, y 1150-1331, centre (639, 1240) |

Three properties make this the right anchor:

**It is language-independent** - an icon, not a word.

**It is feature-rich**, so the confidence floor means something.

**It is the tap target**, so detection and action cannot disagree. Tapping a matched centre
removes the failure where the dialog appears shifted and a hardcoded coordinate lands
somewhere else.

On the observed frame, every pixel passing the Friend-green predicate lies inside
x 786-946, y 1163-1320 - the tick and nothing else - so the search region cannot be
satisfied by anything else on that screen.

**If the tick does not match within the normal navigation timeout, stop the mode.** Do not
tap hoping: without the dialog, that area is bare background in Arena and a card in
Supreme Arena.

The cancel button's geometry is recorded for one reason: so an implementation can assert
it is not tapping it.

## Preference order, and `Opponent Position`

Supreme Arena already has a user-facing `Opponent Position` setting (Left / Middle / Right,
`settings.py:404`). An earlier draft ignored it, which would have silently overridden an
explicit user choice the moment the toggle was enabled - against this spec's own reason for
defaulting to off.

**The toggle RESPECTS the setting; it only ever skips.**

| `Opponent Position` | preference order with the toggle on |
|---|---|
| Left (default) | card 1, then card 2 |
| Middle | card 2, then card 1 |
| Right | card 3, then card 1, then card 2 |

Two consequences, both deliberate:

**Right is honoured as a first choice but never as a fallback.** A user who chose Right
asked for that opponent, so it is offered first. It is not used to rescue a flagged card 1
or 2, because card 3 is routinely out of the power bracket - falling back onto it would lose
the battle in order to avoid a friend, and that is not a trade to make silently.

**Card 3 is only ever read when it is the configured choice.** Under Left and Middle it is
never evaluated and never tapped, exactly as today.

Arena has no equivalent setting, so its order is fixed: card 1, then card 2.

## Settings

One field per mode, so the two stay independently controllable as they are today:

```
Prevent Friendly Fire - do not attack friends or guild-mates in this mode
```

`bool`, default **false**.

`SupremeArenaSettings` already exists and gains the field. **`ArenaSettings` does NOT
exist** - Arena has no settings section at all today, no class and no entry in the root
`Settings` model. So the Arena side requires creating the class, registering it on
`Settings` with an `Arena` alias, and accepting that a new `[Arena]` section appears in
every user's `AFKJourney.toml`. That is a visible change to existing installs and is
stated here deliberately rather than discovered during implementation.

Pydantic generates the UI from the schema, so no frontend work is needed once the backend
model is correct.

Off is the default because this changes which opponent gets attacked, and an upgrade must
not silently alter behaviour for anyone who has not asked for it.

## Frame collection

Every evaluated select-opponent frame is written to a `friendly-fire/` directory under the
**per-user application data directory**, named with mode, timestamp and detection outcome.

**Not `/mnt/vault`.** An earlier draft named the author's own vault mount, which no end user
has - this ships to Windows and macOS. Use the resolution the Solstice Clash mode already
uses (`services/solstice/paths.py:user_data_dir`): `%APPDATA%` on Windows,
`~/Library/Application Support` on macOS, `$XDG_DATA_HOME` or `~/.local/share` otherwise.

The eight development frames stay at `/mnt/vault/adbautoplayer/arena-friendly-fire/` as test
fixtures; that path appears in this spec only as the source of committed test data, never in
shipped code.

Two reasons this is in scope rather than a nice-to-have:

- The Supreme Arena **Guild Member** badge has never been observed. Its geometry is
  assumed (see Open assumptions) and only a real sample can confirm it.
- A frame where the two signals **disagree** is the most informative artefact this feature
  can produce: it means one detector is wrong, and it shows which.

## Error handling

| situation | behaviour |
|---|---|
| A popup is covering the select-opponent screen | Call `handle_popup_messages()` before the read, as `_choose_opponent` already does at `arena.py:115`. Popups appear there routinely; treating one as "not the select-opponent screen" would abort the mode over a weekly notice. |
| Screenshot fails or screen is not the select-opponent screen | Retry the read once. If it fails again, **stop the mode** and log an error. Do NOT fall through to the old path: the old path attacks the left card without looking, which is precisely the outcome the toggle exists to prevent. |
| OCR raises | Treat as no-signal, rely on colour, log at warning. OCR failure must not abort the mode - but note the colour arm is then unbacked, which is why its predicate is pinned numerically above. |
| The two signals disagree | Flag the card - safe side - and archive the frame prominently. |
| Refresh tapped but the screen does not change | Re-screenshot once. If it still has not changed, **stop the mode**. Do NOT infer exhaustion from a static screen: a stalled refresh and an exhausted one look identical, and acting on the guess taps a control that forfeits the challenge. Exhaustion is only ever established by positively matching the X. |
| Give-up dialog does not appear after tapping X | Log and stop the mode. Do not tap coordinates hoping. |
| The bottom-right control matches NEITHER Refresh nor X | Re-screenshot and classify once more. If it still matches neither, **stop the mode** and log an error. Never tap an unclassified control: it is either harmless or it forfeits a daily attempt, and we do not know which. This bound also guarantees the selection loop terminates - every iteration either takes a card, consumes a refresh, reaches the X, or stops. |
| Toggle off | Existing code path, untouched, no screenshots, no OCR. |

## Testing

- Pure-logic tests over the six archived frames: each must produce the expected per-card
  flags. The baseline frame must produce **no** flags on any card - that is the false
  positive guard.
- A test that the colour rule accepts cyan as well as green, since a green-only rule is the
  obvious regression.
- A test that the 400px speck discard rejects the scattered badge-coloured pixels in the
  baseline frame: it contains 400 such pixels in total, none forming a component that
  survives the discard, so the frame must yield zero candidates before the shape rules are
  even reached.
- A test that the shape rules reject the sword buttons, which DO survive the speck discard
  at ~8000px each and are excluded only by `height <= 80` and `w/h >= 2.0`. This is the
  test that would have caught the original design, and it must fail if either bound is
  relaxed.
- A test that area alone is insufficient: the largest badge is 7948px and the smallest
  sword button 8012px, so any implementation that reintroduces an area-only rule must be
  caught.
- Decision-table tests for the selection algorithm: card 1 clean; card 1 flagged and card 2
  clean; both flagged with refreshes left; both flagged and exhausted.
- **Control classification**, which had no test at all: frames 01 and 05 are ready-made
  Refresh fixtures, 03 and 06 ready-made X fixtures. Each must classify correctly in its own
  mode.
- **The per-mode refresh glyph.** Point Supreme Arena at Arena's template and the suite must
  FAIL. Without this, the round-10 defect - a glyph wrongly assumed shared, scoring 0.36 -
  is reintroducible in silence, and its symptom is a mode that quits instead of refreshing.
- **The template-fits-its-search-region invariant** for every template, which is the
  round-11 defect in general form.
- **Give-up tick detection** on frame 04, including that the tap target is the MATCHED
  centre rather than a constant.
- **The give-up precondition**: a card flagged by one signal only must stop the mode, not
  forfeit. This is the round where the cost model was wrong, and it is the most expensive
  defect to reintroduce.
- **The failure branches**, none of which had tests: a failed read must not fall through to
  the old blind path; a static screen must not be read as exhaustion; a disagreeing second
  read must flag the card.
- **Preference order** against each `Opponent Position` value, including that card 3 is
  never used as a fallback.

The test suite's job is to fail if any defect found in review is reintroduced. Detection
tests alone would not have caught most of them - and every one on the path that forfeits an
attempt was uncovered until this list.

- No device is required for any of the above; frames are fixtures.

## Open assumptions

1. ~~Supreme Arena's Guild Member badge position is assumed.~~ **RESOLVED 2026-07-31** -
   observed on the right card (x 759-948, y 981-1020) and the middle card (x 446-636,
   y 1048-1091). Same band as Friend, same centre, same width across columns.
2. **Arena card 3 and Supreme Arena card 3 are never evaluated**, so no geometry was
   measured for them. If the product rule ever changes, that work is outstanding.
3. **Badge bands were measured at 1080x1920 only.** Other resolutions are unverified.
4. **No badge has been observed on Supreme Arena's LEFT card.** Its band is inferred from
   the two measured columns plus the card stagger. Mitigated by the OR rule and resolved
   over time by frame collection - this is the specific gap the collection exists to fill.
