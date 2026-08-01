# Side orientation: trust the trios, not the screen - design

Written 2026-08-01, after a live run exposed the defect and both reviewers confirmed it.

## The defect, with its evidence

Match 1476 was recorded backwards. The operator noticed because an auto-bet visibly lost
while the log announced `HIT`.

**Draft frame** (`/mnt/vault/adbautoplayer/solstice-frames/draft-1476.png`, inspected):

| | player | rating | trio |
|---|---|---|---|
| LEFT / BLUE | Rocky | 4041 | Berial, Silven, Callan |
| RIGHT / RED | Guicts | 4101 | Evie, Lorsan, (Cyran, pick 6) |

**Result screen**: Guicts `Defeat`, Rocky `Victory`. Blue won.

**What was stored**: `outcome = right`, `side='left'` = evie/lorsan/cyran, `side='right'`
= callan/berial/silven. The heroes and the outcome were both taken from the summary
panels, whose order is viewer-relative, and written as though the panels were geometry.

**Why it inverted the score.** `predicted_left` is DRAFT-relative: 0.3403 meant "34% that
Rocky wins", so the model favoured Guicts and the bet went on red. Guicts lost - a real
MISS. But the scorer compares `predicted_left < 0.5` against a summary-relative `outcome`,
and those are different frames, so it recorded a HIT.

**Scale**: an audit of 922 adjudicated draft frames found **76 mirrored, 8.24%**
(95% Wilson 6.64-10.20). Every one of the 76 carries a prediction, so **76 stored HIT/MISS
values are inverted**, and all 76 are already `pushed_at`-stamped - the pool holds 76 wrong
outcomes.

**What is NOT wrong.** The live path was correct throughout. The odds, the overlay and the
auto-bet all run during the draft from draft-anchored data. `comps_key` sorts both trios,
so identity, dedupe and the SC-41 backstop are orientation-invariant and unaffected.

## The three invariants

Settled by the operator; the design exists to make them structural rather than documented.

1. **Local rows are draft-anchored.** Blue = left, red = right, fixed at the draft/locked
   stage and never changed. This is the frame `predicted_left` lives in.
2. **Remote rows have no meaningful side.** A contributor may view the same match with the
   sides rotated; whichever pushed first would set an arbitrary orientation. What must
   hold is that each row is INTERNALLY CONSISTENT - its heroes and its outcome from one
   read. Row 1476 is a bug because it breaks this, not because a label is unfashionable.
3. **Identity is orientation-free**, which is why the key sorts the trios and omits the
   outcome.

**The only rock-solid facts on the details screen are the two TRIOS and WHICH TRIO WON.**
Everything else on it - panel order, banner order, `Ally`/`Enemy`, player names - is
decoration and must not decide anything.

**Names are ignored.** OCR reads them from the wrong place: the operator's profile art
reads as `GAME` on one side and `GAMERETRO` on the other, and rows 1133/1136 read one
player as `m` and `mn`. `matchkey.py:42` already excludes them from identity. They are
stored as a human-readable hint and never determine a side.

## Scope

1. Wire the orientation resolver into recording (client).
2. Carry the merged draft+locked trios to record time (client).
3. Save the summary frame (client).
4. Fix the stale-carryover bug (client).
5. Replace `left_*`/`right_*` with `trio_1`/`trio_2` on the server (`0007`).
6. Teach the client to store pulled rows without sides.
7. Reconcile the local/synced pairs already on disk.
8. Repair the 76 mirrored rows, locally and in the pool.

**Hard constraint, verified by both reviewers: nothing may change realtime behaviour.**
Odds, overlay and auto-bet complete during the locked screen, before any summary exists,
and were correct in the instance that exposed this. Every change here happens at or after
`_record_summary`.

---

## Part 1 - orientation from the trios

`services/solstice/orient.py` is already written, tested (11 tests) and committed as
`4512ffb4`. It is pure and has no callers yet.

```python
resolve(panel_top, panel_bottom, draft_blue, draft_red) -> Resolution
side_for_panel(panel, orientation) -> "left" | "right"
```

**Both panels are scored jointly**, not just the winner. The draft screen never shows pick
6, so a draft-frame red side is only ever two heroes - true of all 922 audited frames - and
matching the winning panel alone gives 1-vs-1 on a single misread, which is a tie. Joint
scoring gives 5-vs-0 clean and 4-vs-1 with one misread. `MIN_MARGIN = 2`.

Refusal is for evidence that CONTRADICTS itself, not merely thin evidence: two heroes
misread with every survivor pointing one way is 2-vs-0 and resolves; 3-vs-2 refuses,
because one more misread would flip it.

**When the panel tint is unreadable**, do NOT fall through to `_winner_by_colour` or the
header OCR for orientation. Those read the banner, whose geometry is the thing on trial.
Record unresolved instead.

## Part 2 - carrying the draft trios to record time

`merge_screens(draft_reads, locked)` at `solstice_clash.py:866` already builds exactly the
complete, draft-oriented trios that `predicted_left` was computed from. It is local to that
scope and `_last_draft_reads` is cleared at :912, before `_record_summary` runs.

Carry the merged trios the same way `_pending_prediction` is carried, and consume them in
`_record_summary`. Anchoring against the merged set rather than the saved draft frame makes
the scoring self-consistent by construction: the same six heroes the prediction used.

Fall back to re-reading the saved draft frame when the locked read was abandoned
(`_last_draft_reads = []` at :814). Five heroes still resolve at 5-vs-0.

## Part 3 - save the summary frame

The frame is currently discarded after extraction (`solstice_clash.py:1187-1196`). It is
the source of the winner tint, six identifications, six stat rows and the defect itself,
and we have never saved one - the vault holds only `draft-N.png` and
`draft-pending-N-N.png`.

Gate it behind the existing frame-capture setting, reuse the frame already in memory, name
it `summary-pending-<timestamp>.png` and rename to `summary-<match_id>.png` once
`record_match` returns - the same claim pattern the draft frames use.

**Save every summary frame, not only unresolved ones.** Match 1476 looked successfully
resolved to the code that recorded it; the failure was semantic. We cannot know in advance
which frames will matter. Roughly 2.4 MB each.

Also log the raw read at `[SC-75]` - both trios, the winner, the resolution and its margin -
so the next occurrence is diagnosable from a log alone.

## Part 4 - the stale-carryover bug

Found by the second reviewer while reviewing this. After a draw (`SC-10`) or an `SC-03`
timeout, `_pending_prediction` and `_draft_ratings` survive - they are cleared only inside
`_record_summary` at :1275-1276. A following mid-match join then records the PREVIOUS
match's prediction and ratings against a new match.

The trio anchor doubles as the guard: when the panels match neither carried trio, refuse to
record the carried prediction and ratings as well as refusing the orientation. Clear both
on every exit path from a match, not only the successful one.

## Part 5 - the server drops sides (`0007`)

`match.left_player`, `left_rating`, `left_rank` and their right counterparts are replaced
by `trio_1` and `trio_2`, sorted canonically exactly as `comps_key` sorts them, plus
`winning_trio` (1 or 2). `outcome` as a left/right value is removed.

Rationale: a field that does not exist cannot be misread as a side. Server-side left/right
is arbitrary by construction, so removing it converts invariant 2 from a comment into a
property of the schema.

`match_hero.side` becomes `trio` (1 or 2), consistent with the above.

Player names are dropped from the server entirely. They are ignored by design, excluded
from identity, and OCR-fragile; keeping a field nobody may trust invites its use.

**Ingest** computes the trios from the client's heroes as it already does for `comps_key`,
assigns `trio_1`/`trio_2` by the same sort, and maps the client's reported winner onto
`winning_trio`. **Pull** returns trios and `winning_trio`.

## Part 6 - pulled rows have no sides

A pulled row with no local counterpart is a match we never watched. Store its trios with
`side` NULL on `match_hero`, and a row-level flag recording that it is not draft-anchored.

`matches_for_fit()` currently returns a `side` column the model consumes. It must read trio
membership instead. The model fits on comps, and a comp is a set - it never needed a side.

## Part 7 - reconciling the pairs already on disk

Four local/synced pairs currently share a `comps_key` with neither marked superseded, so
three of them are counted twice in the fit. They arose from pulling a match another
contributor pushed and then spectating it ourselves; the SC-41 backstop prevents new ones
but nothing reconciles those that exist.

A one-off pass, run at startup alongside the existing backfill:

- For each `comps_key` held by more than one local row, group rows whose `captured_at` fall
  within the **±2 minute** window.
- Within a group, keep the `origin='local'` row - it is draft-anchored - and mark the
  synced copies `superseded_by` it.
- **Rows outside the window are different matches and must be left alone.** Ids 1 and 45
  share a `comps_key` and are 31.6 hours apart: a genuine rematch, correctly two rows. A
  reconciliation that ignores the window would destroy it.

## Part 8 - repairing the 76

`scripts/solstice_frame_side_audit.py` already classifies and can repair, gated behind
`--apply`, and its sidecar carries the 76 verdicts. Two corrections needed first:

- Its documented assumption that summary-header names are "side-correct"
  (lines 722-724) is contradicted by 1476 and must be removed. Names are not repaired.
- Only `match_hero.side` and `match.outcome` are touched. `predicted_left` is draft-relative
  and already correct - flipping it would re-break the pairing.

The 76 are already pushed, so the pool holds 76 wrong outcomes. After the local repair they
must reach the server. Their `comps_key` does not change - identity is orientation-free - so
a re-push is an update to an existing row, not a new one. The server needs an ingest path
that accepts a corrected `winning_trio` for a known identity, from the contributor that
originally supplied it.

**This is the one step that mutates data the operator can see.** It runs only on an
explicit go-ahead, after a `pg_dump` and a local snapshot.

## Ordering

Parts 1-4 are the client fix and stop new corruption. Part 5 is the server schema. Parts
6-7 depend on 5. Part 8 depends on 1-5 being live, because a repaired row must push into
the new shape.

The audit must have run before Part 8, which it has - `side-audit-2026-08-01.json`.

## Risks

| risk | mitigation |
|---|---|
| The resolver refuses too often and matches go unrecorded | Unresolved rows are still stored with everything side-neutral; only the outcome is withheld. The margin rule is tested against one and two misreads |
| `0007` lands on a pool that had `0005` and `0006` hours earlier | `pg_dump` first, restore-verified, and the migration is mechanical: trios are derivable from `match_hero`, which is unchanged |
| The reconciliation collapses a genuine rematch | The ±2 minute window is the test, not `comps_key` alone. Ids 1/45 are the committed fixture |
| Repairing the 76 corrupts good rows | Dry-run default, snapshot, `predicted_left`/names untouched, and the same two-phase swap the repair already uses |
| A pulled row's missing side breaks the model fit | `matches_for_fit` moves to trio membership; the model never used side |

## What this does not do

It does not change how a match is played, watched, predicted, displayed or bet on. It does
not touch `predicted_left`. It does not attempt to explain why row 1476 stored
`left_player = Rocky` when the header showed Guicts - both reviewers failed to account for
it, no summary frame exists from that window, and Part 3 exists so the next such question
is answerable.
