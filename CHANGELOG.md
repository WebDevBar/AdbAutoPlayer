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
- The working copy moved from `/mnt/docs/adbautoplayer` to `~/Dev/webdevbar/adbautoplayer`,
  matching every other repo on this machine. `/mnt/docs` survives a reformat and `~/Dev`
  does not, so the last built RPM is kept at `/mnt/docs/adbautoplayer-rpm/` and
  `AdbAutoPlayer-latest.rpm` points there until the next local build. Paths in
  `build-rpm.sh`, `PERSONAL-NOTES.md` and the plan docs were repointed, and the
  hardcoded-developer-path guard in `test_paths.py` now bans the new path too.

### Removed

- **Screenshot archiving, entirely.** Mode A wrote a full 1080x1920 PNG per hero per
  match into `/mnt/vault/solstice/training/` - **345 MB in a single day, 72% of it
  byte-identical duplicates** (measured: 347 files, 97 unique). 407 MB reclaimed.

  Nothing ever read one back. `train_from_frame` takes the frame **in memory**
  (`frame: np.ndarray`) and `learn_if_improved` takes `gray`; `frame_path` was only a
  string stored in a column that appears in no production SELECT, and
  `match_hero.frame_path` was a dead column no production code ever wrote.

  It was also serving a shelved feature: `learn_if_improved` returns `False` when
  `confirmed_slug is None`, which comes from `confirmed_sides()`, which only counts
  `identified_by == "longpress_ocr"` - and long-press verification is off. Learning could
  never fire, so every frame was written for a loop that cannot run.

  The write was buggy twice over. `self._archive(frame)` sat inside `for hero in
  read.heroes`, writing the same frame six times per match. And the guard
  `None if confirmed == hero.slug else archive(...)` reads as "archive on disagreement",
  but with verification off `confirmed` is always `None` - so it fired for every
  identified hero, while a FAILED identification (`hero.slug is None`, so `None == None`)
  was the one case guaranteed *not* to get a frame, despite being the only case the
  schema comment says the frame exists for.

  A screenshot is input: once parsed into rows, the rows are the artifact. Re-derivation
  was never possible anyway - pooled rows from other contributors carry no frames.
  704 dangling `identification_audit.frame_path` values were nulled; audit rows and
  matches untouched.

### Fixed

- `[SC-41]` was doing three jobs - parser raised, incomplete read, and benign dedupe skip.
  Split into `[SC-41]`/`[SC-47]`/`[SC-48]`, because the first two are opposite in what
  they ask you to do and were indistinguishable in a log.

## [wdb-12.9.24-6] - 2026-07-27

Rebuild of upstream 12.9.24 carrying the screenshot-archiving removal and the `[SC-41]`
split above. Windows `.exe`, Linux `.rpm` and `.deb` attached via GitHub Actions.

https://github.com/WebDevBar/AdbAutoPlayer/releases/tag/wdb-12.9.24-6

The tag and the RPM release number now agree. `wdb-12.9.24-4` was re-run after the
archiving removal landed, so it shipped `AdbAutoPlayer-12.9.24-5.x86_64.rpm` under a tag
named `-4` - the assets were current, the label was not, and the release read as stale.
Rule from here: bump the RPM release, then tag `wdb-<version>-<release>` to match it.

## [wdb-12.9.24-4] - 2026-07-27

First release of the WebDevBar fork, built on upstream 12.9.24. Windows `.exe`, Linux
`.rpm` and `.deb` attached via GitHub Actions.

https://github.com/WebDevBar/AdbAutoPlayer/releases/tag/wdb-12.9.24-4

Contains everything under Unreleased above. The bundled sync key is not authentication -
it ships inside a binary. `ADB_SYNC_ENABLED=false` opts out of sync entirely;
`ADB_SYNC_KEY` points at your own endpoint.

Note for future rebuilds on the same upstream version: only the Linux RPM config carries
a release number, so the `.exe` and `.deb` filenames do not change between rebuilds. The
RPM release field must be bumped or `dnf` sees nothing newer and the install silently
does nothing.

## [12.9.24] - 2026-07-23

### Bug Fixes

- **AFK Journey**:
  - Improved `_find_date_tabs` logic and hero scanner ROI bounds to self-correct date tabs and log OCR hero readings accurately.
- **UI**:
  - Fixed active profile state synchronization during profile deletion in `+layout.svelte`.
