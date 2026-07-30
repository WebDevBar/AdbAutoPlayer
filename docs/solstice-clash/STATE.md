# Solstice Clash - where things stand

Written 2026-07-29, before a machine format. This is the "pick it up cold" document: what
works, what is shipped, what was decided and why, and what to do next. The measurement
history lives in `model-findings-ledger.md`; this file is the operational state.

## Restoring after a format

Everything that cannot be regenerated is on `/mnt/vault` (a separate 3.7T disk, untouched
by a `/home` format):

    /mnt/vault/backup/preformat-20260729/
      AdbAutoPlayer/          -> restore to ~/.local/share/AdbAutoPlayer
      webdevbar/              -> restore to ~/.local/share/webdevbar   (CREDENTIALS)
      logs/                   -> ~/.local/state/adb-auto-player  (expendable, kept for reference)
    /mnt/vault/adbautoplayer/solstice-frames/   362M of saved draft frames

**The match data itself is safe regardless.** All 402 locally-collected matches are pushed
to the pool at `https://gameretro.net/adb`, and a fresh install pulls them back. What the
pool does NOT hold, and the backup does, is the **locally earned calibration**: 5,778
identification-audit rows, 50 cell-registry entries, 24 art transforms, 7 hero aliases.
That is measured screen geometry for THIS machine's capture path and it is not
reproducible from the wiki or the pool - restore it rather than re-measure it.

The database also carries this install's `instance_uuid`. Restoring it keeps our
contributions attributed to the same identity; losing it means the next sync claims a new
one, which is harmless but makes attribution discontinuous.

## What is shipped, as of WDB release 22

The model, in `services/solstice/odds.py`:

- **Regularised Bradley-Terry on hero identity**, theme-scoped. `SIGMA_THETA = 0.20`.
  The only thing that has ever beaten the base rate.
- **`CROSS_THEME_WEIGHT = 0.0`** - hero strength does not survive a theme rotation.
  Measured three independent ways; see the ledger.
- **`W_CROWD = 0.0`** - the betting market is uninformative here.
- **A flat rating nudge**: nothing under a 100-point gap, then +-0.25 log-odds. Weakened
  at 155 rated matches and now on notice with a pre-registered challenger.
- **Nothing else.** Six families of ideas have been measured and closed.

Around it:

- **The Android odds overlay** - a 9KB plain-Java APK that draws one number at the bottom
  of the game screen. Install/uninstall are explicit menu commands; a collection run never
  installs it silently.
- **Auto-bet** (off by default) - stakes ~1000 tokens on the favoured side at >=58%
  confidence, by dragging the stake handle 18px off centre. Never fires while the odds are
  gated. See "the honest numbers" below before trusting the threshold.
- **`hero_matchup` view** - hero-vs-hero records, derived rather than stored.
- **Client-side schema migration** - the client now upgrades its own database on startup.
  This was a real defect: a contributor on an older database lost every match to
  `no such column: predicted_left`.

## The honest numbers

Walk-forward, out of sample, production call path (band evidence included - omitting it
inflates the figures by 17 points, which happened once and is a trap worth knowing):

| | Converging Paths (365) | Flourishing Wilds (126, live) |
|---|---|---|
| overall directional | 54% | 58% |
| >=52% | 59% | 64% |
| >=56% | 78% (n=27) | 67% (n=15) |

**Do not quote the 78%.** Codex measured 64% (n=36) for what it described as the same
configuration and the discrepancy was never reconciled. The threshold also fails its own
selection audit under the production path: permutation p = 0.246, and 53% when the line is
chosen on one theme and tested on the other. The defensible statement is **~61% correct on
the ~15% of matches that clear 56%, 95% interval 49-72**.

So: the model is *selective* rather than accurate. It knows when it knows - the confidence
ordering is real, calibration slope 2.07 (SE 0.96) - but the specific line is not earned.

## What was decided, and why it will not be re-litigated

Full detail in the ledger's two tables ("Do NOT test these again" and "Re-open when the
data arrives"). The short version:

**Closed by two or three independent implementations:** cross-theme transfer of hero
strength; rank-weighted popularity (its correlation with fitted strength is 0.008 - it was
never a strength proxy); plain popularity; post-match atk/heal/tank stats in ~16 shapes;
class/faction/race composition and class-vs-class counters; faction synergy; player
identity; the crowd's betting split; rank as a corrective on a confident call; stacking a
feature onto BT with a fitted weight; literature methods needing 10k-1M matches.

**The whole pair programme is closed**, six shapes across three rounds: cross-side counter
tallies, fitted low-rank counters, same-side synergy tallies, fitted same-side pair terms,
depth-restricted variants, whole-comp terms. The fitted version's own sweep chooses
`sigma_pair = 0` - the maths refuses the pair terms when left to itself - and assigning
them to random pairs that never played together behaves identically. Named "synergy" pairs
sit on 4-7 observations at ~0.02 percentage points of effect, and a shuffled null produced
a *larger* spread than the real data.

**Whole comps are impossible, not merely thin:** 717 distinct comps from 730 observations
on the mature theme, zero seen three times; on the live theme all 238 are unique.

## Why everything fails, in one sentence

Every closure traces to the same cause: **a few hundred matches per theme, against
thousands of possible parameters, with the clock reset every three days.** Bradley-Terry
needs ~240-300 matches before it says anything, and nothing carries across a rotation.

## What actually moves the needle

Not modelling. Two things:

1. **Collect longer within a theme.** A three-day theme at ~18 matches/hour could hold
   well over a thousand; Converging Paths accumulated 365. The window is barely used.
2. **More contributors to the same theme.** Two machines is ~730 matches per theme instead
   of 365 - the difference between 42 hero pairs seen five times and enough to fit
   something. This is why the migration fix mattered: a second collector had been silently
   failing to record anything at all.

## Open items

| item | note |
|---|---|
| **Auto-bet is untested live** | Built and unit-tested; never run with the toggle on. The `[SC-94]` log line reports each stake. |
| Device Stream wedges | `screenrecord` hangs inside Waydroid (OS-level, not ours - a 2-second capture ran 39s). Falls back to screenshots. Two cheap fixes not done: stop re-trying once proven wedged (13s wasted per match), and make the error state the actual reason instead of swallowing it. |
| Windows log path | `wdb_log_path()` uses `~/.local/state` on Windows too. It works, but no Windows user would find it. `%LOCALAPPDATA%` is where it belongs. |
| SC-05 / SC-06 frame capture | Fired twice across two machines. Benign (the match is recorded first), but no frame is saved so a recurrence cannot be diagnosed. Instrument rather than fix. |
| Live log legibility | Colour on the odds and hero-matching lines, a match-result line saying hit or miss against our call, and a won/lost line when auto-bet staked. Agreed 2026-07-30; detail in `odds-display-next-steps.md` section 5. The auto-bet outcome line is the one that matters, since the toggle has never been run live. |
| The rating step | Decide at ~250 rated matches against its pre-registered challenger. Currently 193. |
| The confidence threshold | Settles itself as the live theme fills. No work needed. |

## Traps that have each cost an evening

- **Call `predict` exactly as the mixin calls it**, band evidence included. Omitting it
  produced a 78%-vs-61% discrepancy.
- **A pass at one sample size is not a pass.** Rank-weighted popularity cleared the bar at
  3.2x SE on 61 matches and turned out to measure nothing.
- **Audit the selection, not the selected value.** A fixed threshold permutes at p=0.0025;
  the procedure that chose it permutes at 0.246.
- **Never run `migrate.py` against the committed seed database.** It writes a
  machine-specific `install` row, which would make every contributor claim the same UUID.
  Use `strip_seed.py` if it happens.
- **`with sqlite3.connect(...)` commits but does not close.** Leaked connections deadlock
  anything that needs a write lock.
