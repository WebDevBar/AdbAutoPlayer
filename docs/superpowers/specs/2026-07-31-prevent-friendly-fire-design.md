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

OCR a crop of the **whole card**, and flag on "Friend" or "Guild Member".

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
3. Take the first unflagged card, preferring card 1.
4. If both are flagged: **classify the bottom-right control FIRST** - it is either
   Refresh or the X, and it must be positively matched as one of them before anything is
   tapped. If it is Refresh: tap it, take a fresh screenshot, re-evaluate. If it is
   already the X, go straight to step 6 without tapping Refresh.
5. Repeat until a card is taken or the control classifies as the X. Do not count
   refreshes: the limit differs per mode (7 and 5) and could change in a patch. Exhaustion
   is a visual fact, never an inferred one.

   **The control is classified before every tap, not once.** An already-exhausted screen
   on the first pass would otherwise send a Refresh tap into the X and open the
   destructive give-up flow.
6. When exhausted with both still flagged: **positively match the X control** before
   tapping it - never tap the refresh coordinate on the assumption that it has become the
   X. Then wait for the "Give up this challenge?" dialog and match it before tapping the
   confirm tick. Two template matches, both required, because this path forfeits a daily
   attempt.

Before any tap that commits to a battle, take a second screenshot and re-evaluate.

**If the two reads disagree, the card is treated as FLAGGED** and the algorithm returns to
step 3 with that card excluded - so a card seen clean once and flagged once is never
attacked. If that leaves no unflagged card, it refreshes exactly as though both had been
flagged. The frame is archived as a disagreement.

This is the only safe resolution: proceeding on the optimistic read would defeat the
feature, and treating disagreement as fatal would stop the mode over a single noisy frame.

Being slow is free; being wrong is not. The mode has no timer.

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

Every evaluated select-opponent frame is written to
`/mnt/vault/adbautoplayer/arena-friendly-fire/`, named with mode, timestamp and the
detection outcome.

Two reasons this is in scope rather than a nice-to-have:

- The Supreme Arena **Guild Member** badge has never been observed. Its geometry is
  assumed (see Open assumptions) and only a real sample can confirm it.
- A frame where the two signals **disagree** is the most informative artefact this feature
  can produce: it means one detector is wrong, and it shows which.

## Error handling

| situation | behaviour |
|---|---|
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
- A test that the run-length threshold rejects the 134 stray pixels in the baseline frame.
- Decision-table tests for the selection algorithm: card 1 clean; card 1 flagged and card 2
  clean; both flagged with refreshes left; both flagged and exhausted.
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
