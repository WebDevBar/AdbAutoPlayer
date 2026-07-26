# Solstice Clash Phase 2 - Autonomous Spectate Collector

**Status:** design, awaiting approval
**Date:** 2026-07-26
**Supersedes the Phase 2 sketch in** `docs/superpowers/specs/2026-07-25-solstice-clash-phase1-design.md`

---

## 1. Goal

Collect Solstice Clash match outcomes unattended, overnight, at whatever rate the game
produces them, so that a comp-versus-comp win model has enough data to be worth
building.

One match produces one record: which six heroes played, which side they were on, which
side won, and three per-hero performance numbers.

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

So Phase 2 ignores the draft entirely and reads the summary.

### What this design knowingly gives up

- **No pool.** We never learn "hero X was available and both players passed on it."
- **No odds.** We cannot ask "was the crowd wrong, and where."

Both are acceptable for a win-rate model and both are recoverable later from compete
mode, which does show all 20 cards. Recording that is out of scope here.

## 3. Navigation flow

The exact chain, per the user:

1. Hamburger menu -> Events
2. Solstice Clash
3. **Fortune Picks** icon
4. **Spectate Live**
5. Wait for the match to end. The end state is identified by three co-occurring
   signals: the **Back** button, the **chart/details** icon to its left, and the
   **"... WINS!" / "... LOSES!"** banner text.
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
5.2b applies and the loop speeds up by roughly 15s per match.

**Side benefit:** with every card OCR-confirmed, the learning gate in 5.3 is satisfied on
every sighting, so optimal per-hero transforms accumulate across the whole roster as a
by-product rather than one hero at a time.

### 5.2b Fallback when verification mode is off

On rejection (`unknown`), long-press that card and OCR the hero name from the popup.

Verified working: a 800ms long-press at the card centre opens a tooltip containing the
hero name, power, class, and skill levels. RapidOCR read all six names exactly -
Atalanta, Igor, Indris, Baelran, Pippa, Solise - matching the image matcher 6/6.

The popup renders **downward from ally cards and upward from enemy cards**, so its
position is not fixed. Detection is therefore by content, not geometry: run OCR and
check whether any returned string matches a known hero name.

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

## 6. Data model

### New tables

```sql
CREATE TABLE screen (
    id            INTEGER PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,   -- 'solstice_summary'
    description   TEXT NOT NULL,
    base_resolution TEXT NOT NULL,        -- '1080x1920'
    crop_half_w   INTEGER,                -- card crop, relative to cell centre
    crop_top      INTEGER,
    crop_bottom   INTEGER
);

CREATE TABLE hero_screen_transform (
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
    confirmed_by  TEXT NOT NULL,          -- 'longpress_ocr' - never 'self'
    verified_at   TEXT NOT NULL,
    UNIQUE(screen_id, hero_slug, art_ref)
);

CREATE TABLE identification_audit (
    id            INTEGER PRIMARY KEY,
    match_id      INTEGER REFERENCES "match"(id),
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
    created_at    TEXT NOT NULL
);
```

Every identified card writes one `identification_audit` row. Agreements are cheap (no
frame saved) and are what make the false-positive rate computable; disagreements also
persist the frame so a misfire can be reproduced offline without the device.

`confirmed_by` exists so the gate in 5.3 is enforceable *in the data*, not only in the
code path that writes it. A row that cannot name its confirming evidence is a bug.

### Extensions to existing tables

- `match`: add `theme`, `blue_player`, `red_player`, `blue_rank`, `red_rank`.
  Player identity is recorded because player skill is a real confound - without it,
  a strong player's hero picks look like strong heroes.
- `match_hero`: add `stat_sword`, `stat_heart`, `stat_shield`, `power`,
  `identified_by` (`image` | `longpress_ocr`).
- `MatchStore._SOURCES`: add `spectate_summary`.

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

**Duplicate protection.** A match gets a `natural_key` derived from the two player names
plus the six hero slugs plus the winner. Re-recording the same key updates rather than
inserts, so a re-entered match cannot inflate the dataset.

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

## 9. Testing

- **Fixtures**: the captured summary frame plus the six long-press frames, committed.
  Ground truth is known and independently confirmed (image match + OCR agreeing).
- **Unit**: summary parsing returns the six correct heroes, the correct winner, and the
  stat numbers; the accept rule rejects a deliberately degraded card.
- **Unit**: the tuning search improves margin on a card and never returns parameters
  that reduce it.
- **Unit**: `hero_screen_transform` rows cannot be written with `confirmed_by = 'self'`.
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
