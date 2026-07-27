# Changelog

## [Unreleased]

### Added

- **AFK Journey - Solstice Clash Mode B (passive collection)**: records match data from
  the post-match details screen while YOU play competitive matches. Never taps, swipes,
  holds or navigates - `get_screenshot()` is the only device call, because the user is in
  a live ranked match. Nothing is recorded unless you open the details screen yourself.
  - `details_screen.py` - a pure screen predicate with up to three signals: the Replay
    control (template, 1.000 on details screens vs <= 0.643 on fifteen others), an
    "Ally"/"Enemy" roster tab (OCR, exact whole-block match, OR not AND, position
    irrelevant), and an OPTIONAL header title. The title gate is what separates "a
    details screen" from "THIS event's details screen" - the first two signals fire on
    any 3v3 post-battle screen, so without it another game mode's match could be
    recorded as Solstice data. Title matching is `==` after casefolding and collapsing
    whitespace; nothing scores similarity, because the SILVER/SILVEN bug was a fuzzy
    0.833 match.
  - Two dedupe layers: an `armed` flag reset when the screen disappears, so one viewing
    yields one row however long it stays up, plus the `natural_key` backstop for a
    reopened screen the flag cannot see.
  - Loud about silence, which is this mode's characteristic failure: `[SC-45]` stops
    after 15 consecutive screenshot failures (a dropped ADB connection would otherwise
    spin for hours while you play, collecting nothing), and `[SC-46]` warns once when the
    two detection signals disagree for ~1 minute.
  - Verified live before release: one real match recorded, 6/6 heroes identified
    (0.845-0.917, margins 0.258-0.399), zero device actions, zero audit rows, theme
    resolved by date.


- **AFK Journey - Solstice Clash Phase 1**: the vision and storage layer that turns a
  1080x1920 screenshot into identified heroes and database rows.
  - `config.py` - cell geometry, scale chains and accept thresholds read from
    `heroes.sqlite`; no hardcoded geometry or tunables in the package.
  - `icons.py` - decodes the game's own hero art. Files named `*.png` are an `AST`
    header wrapping LZ4-block-compressed ASTC 6x6; dimensions come from the header and
    the decode needs a vertical flip (Unity origin is bottom-left). Gamma 1/1.8 applied
    at library-build time.
  - `vision.py` - template-anchored screen classification, cell extraction, hero
    identification with the `score >= 0.70 AND margin >= 0.10` rule, ban-glyph detection,
    and pool capture with a two-tier candidate strategy.
  - `store.py` + schema v2 - `match`, `match_hero`, `match_pool`, `match_odds`.
  - Measured: 18/18 draft cells and 6/6 locked picks correct, all above their score
    floors; ban detection finds exactly the two banned slots.
  - Adds `lz4` and `texture2ddecoder`; registers the `network` pytest marker.


### Changed

- Mode A now confirms the details screen before reading it, replacing a blind `sleep(2)`
  after the chart tap with a bounded wait raising `[SC-44]`. Scoped honestly: this would
  NOT have caught the live-battle-read-as-a-draw bug, which happened earlier in match-end
  detection. It prevents the adjacent failure - parsing whatever is on screen if the tap
  missed.
- `natural_key` now buckets to **10 minutes** instead of an hour, on both the client and
  the server. An hour is coarse enough that two genuinely different matches with the same
  comps and outcome collide and one is silently dropped - real signal, since the same six
  heroes get placed differently by different players. Existing rows keep their hour-bucket
  keys; no backfill.
- This fork's commands are labelled `WDB:` and grouped at the end of the menu.

### Fixed

- `[SC-41]` was doing three jobs - parser raised, incomplete read, and benign dedupe skip.
  Split into `[SC-41]`/`[SC-47]`/`[SC-48]`, because the first two are opposite in what
  they ask you to do and were indistinguishable in a log.

## [12.9.24] - 2026-07-23

### Bug Fixes

- **AFK Journey**:
  - Improved `_find_date_tabs` logic and hero scanner ROI bounds to self-correct date tabs and log OCR hero readings accurately.
- **UI**:
  - Fixed active profile state synchronization during profile deletion in `+layout.svelte`.
