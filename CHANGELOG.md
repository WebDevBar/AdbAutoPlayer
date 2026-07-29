# Changelog

## [Unreleased]

### Added

- **AFK Journey - Solstice Clash: the odds appear inside the game.** A small rounded
  plate at the bottom of the screen shows the favoured side's probability during a draft,
  so it can be read without looking at another window. Blue or red for which side, one
  number, and nothing at all when neither side is favoured - inside the middle band the
  model is right 50.2% of the time across 705 predictions, so "no favourite" and "nothing
  worth showing" are the same condition, and the plate appearing is itself the first half
  of the signal.
  - Two new commands install and remove it. A collection run never installs it: absence
    is a decision, and a run that quietly restored something the user deleted would be the
    tool overriding them. An install that is present but outdated is still upgraded.
  - Positioned below every read band and every tap point, and detached rather than blanked
    whenever it has nothing to say - the only state provably identical to not having the
    overlay at all.
  - Two device bugs found and fixed along the way, both silent. `am` parses a bare
    component as the intent spec's trailing argument, so every extra after it was dropped
    and the service started, received nothing and painted nothing. And Waydroid's
    `dex2oat` hangs - installd killed it after 570 seconds on a 4KB dex, wedging seven
    install sessions at 90% - so the install sets `pm.dexopt.install=skip` first and
    completes in 25 seconds.
  - The APK is 9KB of plain Java. The first Kotlin build was 612KB because the standard
    library rode along in a 2MB dex to draw one text view.

- **The log prints the fork's own version**, e.g. `App Version: 12.9.25   WDB Version:
  12.9.25-19`. The upstream number says which AdbAutoPlayer release these patches sit on;
  it does not say which of our builds is running, and those differ by a great deal within
  one upstream version.

- **Solstice Clash: optionally keep the draft screenshot of each match**, off by default,
  under a new WDB Modes settings section. Every idea about reading something new off the
  draft - hero levels, star tiers, pick order - is otherwise answerable only by collecting
  for weeks. Frames are named for the match they belong to, because a timestamp join is
  the kind that silently pairs the wrong records.

### Changed

- **Solstice Clash odds: the crowd is out and the hero model is in at full strength.**
  The operator reported confident calls landing the wrong way - a 75% pick losing - and 54
  scored predictions made it measurable: the displayed number was right 27 times in 54 and
  scored worse than always guessing the base rate. `W_CROWD` 0.70 to 0.0, `W_HEROES` 0.50
  to 1.0, `SIGMA_THETA` 0.15 to 0.20.
  - The crowd is flat across its own confidence, scores 0.83 logloss standalone against a
    0.69 constant, and is a noisy echo of the rating gap: correlation 0.475, the same pick
    40 times in 51, and on the 11 disagreements the rating is right 8 and the crowd 3. At
    weight 0.70 it outvoted every signal that works and supplied nearly all of the
    displayed spread, which is precisely why the confident calls were wrong. Set to zero
    rather than negative - the fitted slope is indistinguishable from zero.
  - The hero model was raised because it now works. It measured no edge at 245 matches and
    at 340 it clears the pre-registered bar in two rounds, on shuffle splits AND
    walk-forward validation, in three independent implementations. The earlier null was
    correct when made; the corpus grew past where 93 hero parameters become learnable.
  - Pools and spectator counts are still read, recorded and synced every match, so the
    crowd remains a weight to revisit rather than something deleted.
  - Everything measured is in `docs/solstice-clash/model-findings-ledger.md` with its
    sample size and what would re-open it, including the ~58 configurations that lost.

- **Solstice Clash: matches pool across themes within an event.** `CROSS_THEME_WEIGHT`
  0.35 to 1.0, and the display gate counts the event. A theme applies modifiers that hit
  every hero equally, so a sibling theme is evidence about the same heroes; the old
  weighting would have dropped every collected match to a third of its weight at the
  rotation, starving the model exactly when it had the most data.

### Fixed

- **The odds block named signals that were not in the number**, and the final block
  invented the counts it passed to the display gate - reporting "0 matches for this theme"
  against hundreds collected whenever a match's ratings OCR failed.

- **The theme is decided by its dated window alone.** OCR was a fallback when no window
  covered a capture; it is now a hint stored for backfilling and nothing more. On a pooled
  server one contributor's drifting screen read could file everyone's matches under the
  wrong theme, and theme is what the model conditions on.

### Added

- **Solstice Clash: the odds block names what built the number.** The header said "from
  the rating gap" on a figure the crowd had already moved by twenty points - a match
  with a 9-point rating gap (a coin flip on ratings alone) displayed 34%, which was the
  betting pools talking. It now reads `ODDS from rating + crowd + heroes`, and a signal
  is named only if it actually moved the result: equal ratings, a comp of heroes nobody
  has seen, or a twelve-spectator market are all in the arithmetic at a weight near zero
  and none of them are claimed. The stored `predicted_source` records the same
  composition compactly (`r+c+h`), because a rating-only call and a rating-plus-crowd
  call are different models and pooling their calibration would hide whichever is wrong.

- **AFK Journey - Solstice Clash: live odds during the draft.** A regularised
  Bradley-Terry model over collected matches - one strength per hero, an intercept for
  any structural side advantage - fitted once when the draft is confirmed and predicted
  after every pick from the FOURTH onward, while betting is still open. Shown as its own
  block in the log, framed by rules and blank lines, because a log is a stream of
  one-line statuses and a number worth acting on has to stop looking like one.
  - **Labelled UNPROVEN, and that is a measurement rather than modesty.** Validated out
    of sample on the first 245 matches (25 shuffle splits, 80/20) against "predict the
    base win rate": the best variant scored 0.6967 against a 0.6993 baseline and won 15
    of 25 splits, where the design asks for a 0.01 margin and 80% of splits. Every
    displayed number says so until that changes.
  - Hero prior tightened to 0.15 from the design's 0.30, because at 0.30 the model was
    measurably WORSE than the base rate on this much data.
  - Player terms are off: 162 distinct players over 245 matches meant most appeared
    once, and including them moved logloss by less than 0.0001 while adding 162
    parameters. The machinery stays, switched off, with a test that it still works.
  - Cross-theme matches count at 0.35 rather than being excluded - another theme changes
    the pool and battlefield, but early in a theme those matches are most of what exists.
  - Matches without a full three-a-side read are dropped, never padded: a 2v3 comp would
    teach the model that two heroes beat three.

- **Upstream 12.9.25 merged**: multi-display targeting for screenshots, input and device
  streaming, plus a Qwen2-VL crash fix. Our Waydroid continuous-streaming patch and
  upstream's `screenrecord` change now coexist in `device_stream.py`.

- **AFK Journey - Solstice Clash Mode C, phase 1 (`WDB: Watch Draft Picks`)**: logs each
  pick as it locks while you spectate, in draft order, then the locked six. It predicts
  nothing and stores nothing - it exists to establish whether the draft screen can be read
  correctly and fast enough to predict from, which the odds design refuses to build on
  unmeasured. Never taps, swipes or navigates: `get_screenshot()` is the only device call,
  and `start_up()` is deliberately not called, because it can resize the display or launch
  the app underneath a live match.
  - Reads the 20-card pool once per draft and identifies picks against it, so each cell is
    matched against at most 20 candidates instead of ~121 - faster, and a pick outside the
    pool becomes a detected error rather than a silent wrong answer.
  - Two cell geometries are registered for the same six positions, a day apart and about
    20px apart, and only one can be right: the collected audit rows pass the accept rule on
    39% of `draft_pick` reads against 100% on the prematch and summary screens. Phase 1
    reads BOTH and logs which one answered, with score and margin, so the winner is decided
    by collected evidence rather than by which registration looked more careful.
  - Joining mid-draft reports every pick already on screen, in order, on the first poll.
  - Polls at 0.4s and logs its own slowest read against that interval, because being right
    about a pick after the betting closes is worth nothing.

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

- **Hero identification could only ever work on the developer's machine.** The icon
  library was built from `/mnt/vault/solstice/gamefiles/ui/icon`, a path on one person's
  disk, with no fallback and no override, and `hero.game_icon` holds a filename rather
  than the image. Anywhere else the library was empty, and an empty library is
  indistinguishable from an unreadable frame at the call site, so every cell returned
  `unknown` silently. Matches were recorded with no heroes, never earned a `natural_key`,
  and were therefore never syncable - a contributor could run the mode for hours and
  produce nothing usable. The 584 hero icons (8.6 MB) now ship in
  `data/solstice_clash/icons/hero/`, resolved by the same ladder as the bundled database
  (`ADB_SOLSTICE_ICON_DIR`, packaged resources, dev checkout). A missing directory now
  raises `[SC-49]` instead of quietly identifying nothing.

- **Sync never worked on a fresh install.** Seeding deletes the bundled `install` row so
  contributors cannot share one identity, and nothing recreated it - a shipped build never
  runs `migrate.py`, which is where the row was minted. The client therefore sent an empty
  `X-Instance-Id` and the server answered `400` to every request, so a new contributor's
  matches were collected correctly and then never left their machine. `[SC-33] sync server
  error 400` on every cycle was the symptom. The identity is now created on first use in
  `store.instance_uuid()`, with `INSERT OR IGNORE` plus a re-select so two processes on one
  database cannot mint two. Existing backlogs upload on the first sync after updating -
  nothing collected during the outage was lost or marked rejected.

- `[SC-41]` was doing three jobs - parser raised, incomplete read, and benign dedupe skip.
  Split into `[SC-41]`/`[SC-47]`/`[SC-48]`, because the first two are opposite in what
  they ask you to do and were indistinguishable in a log.

## [wdb-12.9.24-7] - 2026-07-27

Fixes both reasons a contributor other than the developer produced nothing: hero art is
now bundled, and the install identity is created on first use. Windows `.exe`, Linux `.rpm`
and `.deb` attached via GitHub Actions.

https://github.com/WebDevBar/AdbAutoPlayer/releases/tag/wdb-12.9.24-7

If you were running an earlier build, matches that DID identify heroes will upload by
themselves on the first sync after installing this one. Matches recorded with no heroes -
which is everything collected on a machine without the vault path - cannot be recovered:
they never earned an identity, and there is nothing in them to pool.

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

## [12.9.25] - 2026-07-28

### Bug Fixes

- **Device / AFK Journey**:
  - Fixed screenshots, taps, and the device-streaming (`screenrecord`)
    capture path all targeting the wrong virtual display on emulators
    that expose multiple displays (observed on MuMuPlayer's Android 15
    image), which could cause tasks to loop indefinitely without
    recognizing the screen, or occasionally act on an unrelated app.
- **OCR**:
  - Fixed an intermittent access-violation crash in the Qwen2-VL GPU
    OCR backend caused by two conflicting copies of the OpenMP runtime
    library.

## [12.9.24] - 2026-07-23

### Bug Fixes

- **AFK Journey**:
  - Improved `_find_date_tabs` logic and hero scanner ROI bounds to self-correct date tabs and log OCR hero readings accurately.
- **UI**:
  - Fixed active profile state synchronization during profile deletion in `+layout.svelte`.
