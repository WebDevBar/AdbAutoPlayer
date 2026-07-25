# Design spec - Solstice Clash tooling, Phase 1: vision layer + hero library

**Date:** 2026-07-25
**Repo:** `WebDevBar/AdbAutoPlayer` (private fork), branch `webdevbar`
**Game:** AFK Journey v1.7.2, Solstice Clash event (3v3 real-time PvP with spectator wagering)
**Device:** Waydroid, ADB `192.168.240.112:5555`, native **1080x1920**

## 1. Why this exists

Solstice Clash lets you spectate live 3v3 matches and back a side with event tokens. The displayed
odds are **crowd-derived** (parimutuel-style, spectator pools), not the game's assessment of who
should win. So an edge exists wherever a model's view of team strength disagrees with the crowd's.

The eventual goal is three modes:

| Mode | Purpose |
|---|---|
| **A - watch compete** | Passively record the user's own ranked matches (teams, players, outcome) |
| **B - spectate loop** | Automatically loop spectating matches to gather data unattended |
| **C - odds calculator** | Live, fast readout of our predicted win probability vs the crowd's odds |

**All three depend on one thing: correctly identifying six hero portraits from screenshots.**
If hero ID is unreliable, every downstream number is garbage. Phase 1 therefore builds *only*
the vision layer and a clean, labelled hero library. No model, no betting maths, no automation.

## 2. What we verified before designing (2026-07-25 session)

Empirical findings from ~2,077 captured frames and a live automated navigation run:

- **Hero matching works, but not with existing assets.** AdbAutoPlayer's 133 roster templates max
  out at **0.63** against Solstice card art even after an exhaustive crop sweep - they are tight
  face crops of different artwork. **Fresh templates cut from Solstice screens are required.**
- **Search-based matching is decisive.** Comparing a portrait *searched within* a padded card gives
  **0.999** for the same hero with a **0.573 gap** to the runner-up. Fixed-crop comparison of
  equal-sized images gives ~0.77 and is unreliable - `matchTemplate` on same-size inputs performs
  no alignment search. **All comparisons must use search-based matching.**
- **Skins are mostly free.** A recoloured skin (Talene red -> blue hair) still matched its own grid
  card at rank 1, because matching runs on **grayscale** and recolours barely affect structure.
  Only silhouette-changing skins would need separate templates - and the library handles those
  automatically as new entries.
- **Level/frame uniformity helps.** All heroes are locked to Lvl 240 with identical card frames, so
  the only variation is portrait art.
- **Brightness heuristics for screen detection do not work.** Three separate attempts failed
  (1/5 accuracy; result screens classified as drafts; ~50% of library entries turned out to be
  fragments of result/outworld screens). **Screen detection must use template matching.**

### Verified geometry (1080x1920)

```
Pool grid (compete BANNING and SELECTING, 4x5 = 20 cards):
  ROWS = [665, 900, 1135, 1370]   COLS = [155, 315, 475, 635, 795]   card 150x190
  portrait sub-region within card: [45:165, 20:130]   (below stars/badges, above gem)

Pick slots (draft screens): y 390..555
  Blue1 x=48   Blue4 x=190   Blue5 x=333   Red2 x=630   Red3 x=772   Red6 x=915  (w=120)

Spectate draft shows 3 rows (betting panel occupies the lower third); compete shows 4.
Overlays: pick badge (top-left) and padlock (top-right) sit ABOVE the portrait region and do not
interfere. Ban = coloured circle-slash OVER the portrait -> such cards must be skipped, not matched.
```

### Verified navigation (automated end-to-end, no mis-taps)

```
outworld -> events icon (1010,1160) -> Battle Modes -> Solstice Clash (195,1130)
         -> Fortune Picks (130,1590)     [Join at (640,1610) - NEVER tap: enters as a player]
         -> Spectate Live (540,1400) -> live match (may join at any draft stage)
         -> result -> Back (755,1815) -> outworld
History: event page -> History (985,455) -> tabs Records | My Bets | Custom Match
```

## 3. Scope of Phase 1

**In scope**
1. **Screen classifier** - template-anchored, covering every screen in both flows
2. **Field extraction** - hero cards, pick slots, VS-screen lineups, player names/ratings/ranks,
   theme, countdown, odds/pools, token balance, result banner
3. **Hero library** - build, de-duplicate, frequency-filter, and label
4. **Accuracy measurement** - reported, with an explicit unknown-hero path
5. **SQLite store** - schema created and written to (no Postgres yet)

**Out of scope** (later phases): the prediction model, EV/betting maths, mode C's readout,
automated spectating loops, Postgres sync, auto-betting.

## 4. Architecture

```
adb_auto_player/games/afk_journey/
  services/solstice/
    vision.py     screen classification + field extraction   (shared)
    heroes.py     library build, matching, labelling         (shared)
    store.py      SQLite schema + writers                    (shared)
  mixins/
    solstice_data.py    Phase 2+: farm History, watch compete
```

Modes stay **thin** - navigation and looping only. All recognition and storage lives in shared
services so improvements propagate to every mode at once.

**Integration pattern: follow `HeroScannerMixin`, not `FrostfireShowdownMixin`.** Verified in this
checkout, `AFKJourneyBase` inherits `Navigation, HeroScannerMixin, DreamRealmMixin,
GuildMemberScanMixin, Game` (`games/afk_journey/base.py:45`) - `FrostfireShowdownMixin` exists and
carries the decorators but is **not** in the live mixin chain, so it is a reference for command
registration only, not for wiring. `HeroScannerMixin` is also the closer analogue: it is a thin
mixin delegating to a `services/hero_scanner.py` service, which is exactly the split proposed here.
A new `SolsticeMixin` must therefore be added to the `AFKJourneyBase` inheritance list to be live.

Commands are exposed via `@register_command` / `@register_custom_routine_choice`; screen waits via
`wait_for_template`; navigation via `_navigate_menu_chain` and the existing `navigate_to_world()`.

### 4.1 Screen classifier

Each screen is identified by template-matching a small, stable anchor - never by pixel statistics.

| Screen | Anchor |
|---|---|
| Outworld | handled by existing `navigate_to_world()` |
| Battle Modes | "Ongoing Events" header |
| Event page | "Fortune Picks" button / "Current Theme" panel |
| NPC dialogue | "Welcome to Royal City Show!" |
| Banning | "Banning" label + bottom prompt bar |
| Selecting | "Selecting..." label |
| VS intro | `VS` diamond + dual name plates |
| Decision (spectate) | "(Odds Hidden)" text |
| Battle | "Solstice Clash" title bar + match timer |
| Result (spectate) | banner `(BLUE\|RED)\s+(WINS\|LOSES)` |
| Result (compete) | `VICTORY` / `DEFEAT` + `Play Again` |

Anchors are cropped from the captured corpus and stored under
`templates/event/solstice_clash/`. Every classifier decision carries a confidence; below threshold
returns `UNKNOWN` rather than a guess.

### 4.2 Hero identification

- **Probe:** portrait sub-region of a live card, grayscale (~120x110).
- **Library entry:** the same portrait stored with ~12px padding on each side (~144x134), so the
  probe can be *searched within it* and small alignment differences are absorbed.
- **⚠ Matching direction is load-bearing and must not be inverted.** The existing helper
  `TemplateMatcher.find_template_match(base_image, template_image)` slides `template_image` inside
  `base_image` and **raises `ValueError` if the template is larger than the base**
  (`template_matching/template_matcher.py:281`). Therefore:
  **`base_image` = padded library entry, `template_image` = live probe.**
  This is the *opposite* orientation to `game_find_template_match`, which uses the screenshot as
  base and a stored asset as the sliding template (`game/_template_mixin.py:98`).
  Hero matching therefore uses a **dedicated matcher in `heroes.py`** that calls `TemplateMatcher`
  directly - it must NOT go through `game_find_template_match`, which cannot express this
  orientation or return per-candidate scores.
- **Score:** max `TM_CCOEFF_NORMED` over the search window.
- **Accept** at >= 0.90 **and** a margin of >= 0.10 over the runner-up. Otherwise `unknown`.
  (Measured separation on real frames was 0.999 vs 0.426 - a 0.573 margin - so 0.10 is
  conservative. The margin rule exists to catch the genuinely ambiguous case, e.g. a
  silhouette-changing skin partially resembling its base.)
- **Skip** cards carrying a ban overlay (detected by colour cast over the portrait).
- **Library growth:** an unmatched card is admitted only after being seen in **>= 6 frames**,
  which eliminates transient mid-animation artefacts (890 of 1,045 candidates in testing).
- **Skins:** stored as additional templates linked via `hero.is_skin_of`. Recolours generally match
  the base template already; silhouette changes become new entries for labelling.

### 4.3 Labelling

Auto-generated contact sheet of library entries -> user names them -> `hero.label` populated.
One-time pass; thereafter only genuinely new heroes/skins surface for naming. Chosen over
automated label harvesting (long-press reveals a hero's name, but requires locating moving units
mid-battle) because manual labelling of a ~44-70 entry pool is far cheaper and more reliable.

### 4.4 Storage

SQLite at the AdbAutoPlayer data dir. **SQLite is always the source of truth locally.**

```sql
match(id, source, captured_at, theme, balance_epoch,
      blue_player, blue_rating, blue_rank,
      red_player,  red_rating,  red_rank,
      outcome, outcome_source, natural_key UNIQUE)
match_hero(match_id, side, slot,
           hero_id,                 -- NULL when not recognised
           recognition_status,      -- ok | unknown | not_extracted | banned_overlay
           confidence, runner_up_score,
           crop_path,               -- saved crop when status != ok, for later labelling
           hero_slug)               -- denormalised stable slug, so match rows are portable
                                    -- across machines without resolving local hero.id
match_odds(match_id, sampled_at, blue_pool, red_pool, blue_odds, red_odds, spectators)
hero(id, slug UNIQUE NOT NULL, label, is_skin_of)   -- slug = stable committed identifier
hero_template(hero_id, image_path, sightings)
```

Fields present from day one even though unused in Phase 1, because retrofitting them later would
invalidate collected data:

- **`natural_key`** - dedup when multiple contributors submit the same spectated match.
  A raw capture timestamp is NOT usable: two spectators enter at different draft stages and record
  seconds apart, and player names come from OCR so they vary. The key is therefore built from
  **game-stable facts only**, normalised:
  `sha1(norm(blue_player) | blue_rating | norm(red_player) | red_rating |
        sorted(blue_hero_slugs) | sorted(red_hero_slugs) | theme | outcome | time_bucket)`
  where **`hero_slug` is an IMMUTABLE identifier assigned once in the committed manifest** and
  **never renamed** - e.g. `hero_017` stays `hero_017` forever, even after you label it "Tilaya".
  The human-readable name lives in `label`; only `label` ever changes. Renaming a slug would
  re-hash every historical `natural_key` and silently duplicate matches across contributors, so it
  is forbidden. Local SQLite row IDs are likewise NOT used - they differ per machine.

  (Merging a skin into its base hero does not rename anything either: the skin keeps its own slug
  and gains `is_skin_of = <base slug>`. Aggregation resolves through `is_skin_of` at query time,
  leaving historical keys intact.)
  `norm()` lowercases and strips non-alphanumerics, and `time_bucket` is the capture time
  rounded to 30 minutes (matches are minutes long, so collisions between distinct matches with
  identical players, ratings, lineups AND outcome are vanishingly unlikely).
  Rows whose heroes are not all recognised get `natural_key = NULL` and are **excluded from sync**
  until labelled, since an incomplete lineup cannot form a stable key.
- **`outcome_source`** (`observed` | `inferred`) - timeouts are detected by *absence* of a result
  screen, so they must be down-weightable
- **`balance_epoch`** - hero adjustments change per patch (spread runs `ATK +50%` to
  `HP -50%/ATK -50%/SPD -30%`), which makes older per-hero data stale

**Future Postgres (Phase 4+):** sync-only, bidirectional, periodic. The client never depends on it
being reachable; it pushes local matches and pulls others', de-duplicated on `natural_key`.

### 4.5 Reproducibility - committed seed, not just images

SQLite lives in the **runtime app-data dir** (`SettingsLoader.get_app_config_dir() / "data"`, the
convention `hero_scanner.py:124` already uses), which is per-machine profile state and is NOT
committed. Committing template PNGs alone therefore would NOT reproduce a labelled library - the
labels and `hero`/`hero_template` rows would be missing.

So the library is defined by **two committed artefacts** under
`games/afk_journey/templates/event/solstice_clash/`:

1. **`heroes/*.png`** - the padded portrait templates (resource tree, committed)
2. **`hero_library.json`** - the manifest: for each hero, its `label`, `is_skin_of`, and the list
   of template filenames with sighting counts

**Authority is split by data type, and the two never overlap:**

| Data | Source of truth | Notes |
|---|---|---|
| Match rows, odds samples, recognition results | **SQLite** (local) | never regenerated from the manifest |
| Hero slugs, labels, skin links, template list | **`hero_library.json`** (committed) | versioned with the code |

On startup the store **reconciles** the library tables against the manifest on *every* run - not
only when empty - by upserting on `hero_slug`. So pulling a colleague's newly-labelled heroes via
git takes effect immediately, and a stale local DB cannot shadow the committed library. A labelling
pass writes to both: SQLite for immediate use, and the manifest for commit.

Conflict rule: if a slug's `label` differs between DB and manifest, **the manifest wins** (it is the
reviewed, committed artefact); local-only slugs not yet in the manifest are preserved and flagged
for labelling.

## 5. Success criteria

1. Screen classifier scores **>= 95%** on a hand-labelled sample from the captured corpus.
2. Hero library converges to a **plausible pool size** (expected ~44-70 entries, reconciled against
   the in-game Usable Heroes and Hero Adjustments screens) with **zero** result/outworld fragments.
3. Hero matching achieves **>= 99%** accuracy on a hand-checked sample, with unknowns flagged
   rather than mis-assigned.
4. A VS-intro frame yields a complete, correct match row written to SQLite.
5. A fresh clone + `hero_library.json` + committed templates reproduces an identical labelled
   library (verified by seeding an empty SQLite and re-running matching on a golden frame set).

## 6. Risks

| Risk | Mitigation |
|---|---|
| Screen detection regresses (failed 3x today) | Template anchors only; classifier validated against a labelled sample as an automated test |
| Silhouette-changing skin mis-identified | Margin requirement + unknown path; never silently guess |
| Card geometry shifts with a game update | Geometry in one constants module; a golden-frame test detects drift |
| Library polluted by non-pool screens | Classifier gates harvesting; frequency filter; visual review before labelling |
| Corpus disk growth (4.5 GB from one session) | Long-term frames to `/vault` (4 TB); runtime keeps only unknowns + a rolling debug buffer |

## 7. Open questions

1. **Spectate pool completeness** - the spectator view shows 15 cards where compete shows 20.
   Unresolved whether it scrolls or truncates. Does not block Phase 1 (prediction reads pick slots,
   not the grid) but must be settled before pool/ban analysis.
2. **Usable-pool size** - the Usable Heroes grid looked like ~44 while Hero Adjustments ran to ~70
   rows. Needs reconciling to validate criterion 2.
3. **Odds formula** - displayed odds are not a pure function of the visible pools (blue showed 2.93
   where even a zero-rake parimutuel caps at 2.80), implying a house subsidy. Out of scope for
   Phase 1 but must be derived from data before mode C computes EV.
