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
(95% Wilson 6.64-10.20). Every one carries a prediction, so **76 LOCAL stored HIT/MISS
values are inverted**.

**The pool is a different number, and the first draft got it wrong.** Server migration
`0006`, applied earlier tonight, already loads the audit verdicts and swaps the heroes and
outcome of every `frame_corrected` survivor (`0006_backfill_identity.py:294, 389, 413`).
But it used a DIFFERENT sidecar: the server copy has 916 rows and 73 mirrored, the client
copy 929 and 76. Six mirrored matches exist only in the client copy - local ids 1108, 1313,
1316, 1472, 1474 and **1476, the match that exposed this**.

So the pool holds roughly **6** wrong outcomes, not 76, and re-pushing all 76 would
re-break the ~70 that `0006` already fixed. See Part 8.

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
4b. Store trios canonically in the LOCAL database too, with `blue_trio` as the pointer.
5. Replace `left_*`/`right_*` with `trio_1`/`trio_2` on the server (`0007`).
6. Teach the client to store pulled rows without a `blue_trio`.
7. Reconcile the local/synced pairs already on disk.
8. Repair the 76 mirrored rows, locally and in the pool.

**Hard constraint: nothing may change what the draft path DOES.** Odds, overlay and
auto-bet complete during the locked screen, before any summary exists, and were correct in
the instance that exposed this.

Both reviewers flagged the earlier wording ("every change happens at or after
`_record_summary`") as one this design cannot satisfy: Part 2 must capture state inside the
locked-read block, and Part 4 must clear state on pre-summary exit paths. Both are
behaviour-neutral - they retain and release values without reading, computing or displaying
anything differently. The constraint is on BEHAVIOUR, not on line numbers.

The one place this bites for real is Part 6, which changes `matches_for_fit` - and that
feeds the draft-time fit. See Part 6.

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

## Part 4b - one shape, both databases

Adopted after the operator proposed it: store trios canonically in the LOCAL database too,
not only on the server.

**This makes the bug unrepresentable rather than fixed.** Match 1476 happened because there
were two independent side labels - `outcome` said left/right and `match_hero.side` said
left/right - and nothing forced them to agree. Replace both with one trio identity plus one
annotation and there is nothing left to contradict.

**`match_hero` is the ONLY place a composition lives.** Each hero row carries
`trio` (1 or 2) and keeps its `slot`. The row itself stores no trio columns.

| column | meaning |
|---|---|
| `match_hero.trio` | 1 or 2, canonically assigned - the sole record of who played |
| `winning_trio` | 1 or 2 |
| `blue_trio` | 1, 2, or NULL when we did not watch the draft |

The first draft ALSO put `trio_1`/`trio_2` on the match row, and review caught that this
does not make the bug unrepresentable - it moves it. With the composition stored twice, a
row could say `trio_1 = (A,B,C)` while its `trio=1` hero rows were `(D,E,F)`, and
`winning_trio` would point into a contradiction. That is the same defect as 1476 wearing
different column names.

So the composition is stored once. `trio_1` and `trio_2` are DERIVED by the canonical sort
whenever they are needed - the backfill and `comps_key` already do exactly that. Anything
that wants them as columns must be a materialised view or a denormalisation with a
constraint, never a second writable source.

Which trio is 1 and which is 2 is decided by the same sort `comps_key` uses, so the
assignment is a pure function of the heroes and cannot drift from it.

### Storing it once is necessary but not sufficient

Review round 3 showed the "cannot contradict" claim still did not hold: nothing stopped
six heroes being tagged `trio=1` while `winning_trio=2` pointed at a composition that does
not exist. One source of truth removes the DISAGREEMENT; it does not by itself make the
reference valid.

The shape therefore carries constraints, enforced in the schema where SQLite allows and in
a single validated write boundary where it does not:

- `match_hero.trio IN (1, 2)`.
- **`UNIQUE(match_id, hero_slug)` for identified heroes** - a hero appears once in the
  whole MATCH, not merely once per trio. Round 4 caught the weaker per-trio form: it
  permits the same hero in both trios and therefore two identical `(A,B,C)` trios, at which
  point the canonical sort cannot tell trio 1 from trio 2 and both pointers stop naming a
  composition. The game's shared exclusive pick pool makes this impossible on screen, which
  is precisely why a misread must not be able to represent it.
- **`UNIQUE(match_id, trio, slot)`** - the trio-space equivalent of today's
  `UNIQUE(match_id, side, slot)`. Without it two heroes can share a slot while another is
  absent, which passes the three-distinct-heroes check and silently breaks the plate
  recovery this design promises.
- **Exactly three distinct identified heroes per trio**, for any match with a decided
  outcome. This is the same completeness rule `is_complete` and the backfill already use,
  so a five-hero row like 625 is simply never assigned a trio rather than being a
  constraint violation.
- `winning_trio` and `blue_trio`, when not NULL, must be 1 or 2 AND that trio must exist
  for the match. A pointer to an absent composition is the failure round 3 described.

All writes go through one function that asserts these before committing. A test inserts
each violation and asserts it is rejected - a constraint nobody has tried to breach is a
comment.

**Nothing local is lost.** `blue_trio` points at whichever trio was blue, so left/right is
fully recoverable for any match we spectated, and with `slot` so are the plate numbers.
Side stops being a label that can contradict the heroes and becomes a pointer to one of
them, which cannot. For a pulled row `blue_trio` is NULL - honest, and the only thing lost
is something we never had.

`predicted_left` is unchanged and still means "P(blue wins)". It stays interpretable
because `blue_trio` says which trio blue was, and scoring becomes `predicted_left >= 0.5`
against `winning_trio == blue_trio`. There is no second frame for it to mismatch.

The fit then reads trios uniformly with no branching on origin, and `blue_trio IS NOT NULL`
becomes the single condition for contributing to the intercept - the distinction from
Part 6 expressed as data rather than as logic.

### The live path is untouched

Realtime is the operator's hard requirement and this does not reach it. During the draft
everything runs from the in-memory reads: left/right, pick order, the coloured log, the
odds, the overlay, the bet. `blue_trio` is written at RECORD time, after all of that has
happened. Side matters live, for betting on the correct side and for the log to be
truthful; after the details screen it only has to be recoverable, and it is.

### It must migrate itself, on every install

The operator's collaborator runs the Windows build and will never run a script by hand.
`migrate.py` already executes on every launch through `_ensure_schema`, which is how their
database acquired the identity columns without them doing anything - so the mechanism
exists and must be used.

**Do not gate it on a column check.** `_schema_is_current` skips `migrate.apply()` when
every column is present, which is exactly why the `comps_key` backfill silently never ran
after its columns landed and 50 pulled rows stayed keyless across two upgrades.

A shape change needs a "not done yet" predicate, and it must carry the SAME completeness
requirements the backfill uses (`store.py:342`): decided outcome AND three identified
heroes on each side. "Decided and `trio` unassigned" is not enough - row 625 is decided
with five heroes, can never be assigned, and would send the migration round on every
launch forever. Review caught exactly that loop.

The predicate is therefore: a row with `outcome IN ('left','right')`, three identified
heroes per side, and no `trio` assignment. Row 625 fails the hero count, is skipped by
both the migration and the predicate, and the check settles.

It must also survive a database in any prior state. The collaborator's was old enough to
fail with `no such column: predicted_left`.

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
`USE_PLAYER_TERMS = False` at `odds.py:52`, so nothing reads them.

**Every other side-relative column must move too, or the row becomes uninterpretable.**
Both reviewers caught this; the first draft canonicalised the heroes and the winner and
left the rest pointing at an orientation that no longer exists:

**The server derives its compositions the same way.** Round 3 caught Part 5 keeping
writable `trio_1`/`trio_2` columns on `match` while `match_hero.trio` also existed - which
is exactly the duplicate source 4b rejects, reintroduced one repo over. `match_hero.trio`
is the sole record there too, under the same constraints; anything wanting `trio_1` as a
column is a generated column or a materialised view, never a second thing to write.

The side-relative VALUES below still move, because they are not compositions - they are
measurements attached to one:

| today | becomes | why |
|---|---|---|
| `left_rating`, `right_rating` | `trio_1_rating`, `trio_2_rating` (trio_N here names the DERIVED order, not a stored composition) | NOT derivable from `match_hero`. 1,189 of 1,474 local rows carry them and the fit consumes the signed gap (`odds.py:260-261`). Dropping them loses the rating signal permanently |
| `left_rank`, `right_rank` | `trio_1_rank`, `trio_2_rank` | same argument; currently NULL everywhere but the column should not become a trap |
| `predicted_left` | `predicted_trio_1` | ingested and returned (`schemas.py:39`, `models.py:179`, `matches.py:251`). Left side-relative it associates a probability with an unknown trio |
| `left_pool`, `right_pool`, `left_odds`, `right_odds` | `trio_1_*`, `trio_2_*` | same |

All are migratable by the same canonical sort that produces `trio_1`/`trio_2`.

**Ingest** computes the trios from the client's heroes as it already does for `comps_key`,
assigns `trio_1`/`trio_2` by the same sort, and maps the client's reported winner and every
side-relative value above onto the canonical order. **Pull** returns the same shape.

### The API transition

`schema_versions_supported = {4}` (`config.py:24`) and every client sends 4
(`sync.py:206`). The request and response contracts require `side` and `outcome`
(`schemas.py:12`, `schemas.py:80`). Replacing them outright breaks every client mid-rollout,
including the BlueStacks contributor's.

So: **the server accepts BOTH 4 and 5 for one release.** A version-4 payload keeps sending
`side`/`outcome` and the server canonicalises it on the way in - it already has to, since
that is exactly what the migration does to existing rows. Version 5 sends trios directly.
Pull returns the shape matching the requested version. Support for 4 is dropped only once
the contributor has upgraded, which is a decision, not a timer.

## Part 6 - pulled rows have no sides

A pulled row with no local counterpart is a match we never watched. Store its heroes with
their canonical `match_hero.trio` membership and `blue_trio = NULL` - the NULL IS the flag,
so no separate column is needed. (An earlier draft said "`side` NULL", written before 4b
removed `side` entirely.)

**Side is NOT load-bearing. The TRIO is.** The first draft said the model "never used
side", which was wrong; the review then over-corrected and treated side as load-bearing,
which is also wrong. The operator settled it: historically we do not care whether blue or
red won, only which trio beat which trio.

That is provable from the encoding, which is ANTISYMMETRIC. `odds.py:250-253` gives heroes
`+1` on left and `-1` on right, `odds.py:260-261` is a signed left-minus-right rating gap,
and `y = left_won` (`odds.py:262`). Flip a row's sides and every one of those terms negates
while `y` becomes `1 - y`. Since `sigma(-x.b) = 1 - sigma(x.b)`, the likelihood contribution
is IDENTICAL. Orientation is a free choice for the hero strengths and the rating gap.

**The intercept is the sole exception.** Its column is `1.0` regardless of orientation
(`odds.py:249`), so it does not negate - which is exactly why it can learn a blue-side
advantage, measured at 56.0% (662 left / 521 right on local decisive matches). It is the
one term a pooled row with an arbitrary orientation would corrupt.

So:

- Store pulled rows with `trio_1` as left, arbitrarily, and `y = trio_1 won`.
- Hero terms and the rating gap are correct as they stand - no special handling.
- **Exclude pooled rows from the INTERCEPT only.**

Every pooled comp stays in the fit, which is the point of pooling, and the only thing we
decline to claim is the one thing we genuinely do not know.

This does change `matches_for_fit`, which feeds the draft-time fit (`store.py:787` ->
`odds.py:818` -> `solstice_clash.py:1901`). The change is confined to how pooled rows
contribute to a single column; local rows are untouched, so a fit with no pooled data is
bit-identical to today's.

## Part 7 - reconciling the pairs already on disk

Four local/synced pairs currently share a `comps_key` with neither marked superseded, so
three of them are counted twice in the fit. They arose from pulling a match another
contributor pushed and then spectating it ourselves; the SC-41 backstop prevents new ones
but nothing reconciles those that exist.

A one-off pass, run at startup alongside the existing backfill:

- For each `comps_key` held by more than one ROW IN THE LOCAL DATABASE - not "more than
  one local row", which is zero groups, since every real pair is one `local` plus one
  `synced` - group rows whose `captured_at` fall within the **±2 minute** window.
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

Their `comps_key` does not change - identity is orientation-free - so a correction is an
update to an existing row, never a new one.

**How many rows reach the server is answered below, not here.** An earlier draft said all
76 must be pushed; that was written before we discovered `0006` had already corrected most
of them, and following it would re-corrupt roughly 70 repaired rows.

**This is the one step that mutates data the operator can see.** It runs only on an
explicit go-ahead, after a `pg_dump` and a local snapshot.

### The pool needs ~6 rows corrected, not 76

`0006` already corrected the rest. Both reviewers caught the first draft asserting
otherwise. What Part 8 must therefore do:

1. **Recompute the target set against the POST-0006 pool**, not against the client sidecar.
   The six known candidates are local ids 1108, 1313, 1316, 1472, 1474, 1476, but the set
   must be derived, not hard-coded.
2. **Express the correction absolutely, never as a flip.** The client states which trio
   won; the server SETS it. A toggle re-breaks any row already correct, which is exactly
   the failure mode a stale target set would trigger.
3. Both properties together make the operation idempotent - it can be run twice safely,
   which matters because the first run is against production.

### There is no mechanism to send a correction, on either side

The first draft called a re-push "an update to an existing row". It is not:

- **Client**: `pushable_matches` requires `pushed_at IS NULL` (`store.py:892`) and all
  76 are stamped. Nothing will ever re-send them.
- **Server**: an unchanged `comps_key` at the same `captured_at` routes through
  `assign` -> `_merge` -> `RowResult(status="duplicate")` (`matches.py:336-343`), and the
  corrected outcome is silently discarded.

So Part 8 needs a real mechanism at both ends: a way to mark a repaired row for re-send
that does not resurrect it as a new push, and a server path that accepts a corrected
`winning_trio` for an identity it already holds. Neither exists.

"From the contributor that originally supplied it" is also underdefined once a surviving
server row has absorbed another contributor's capture, which demonstrably happens - locals
1108, 1313 and 1316 were each answered `duplicate` against rows we later pulled back as
synced 1109, 1314 and 1317.

## Ordering

Review caught the first draft ordering Part 8 AFTER Part 4b, which cannot work: the repair
updates `match_hero.side` and `match.outcome` (`solstice_frame_side_audit.py:713`) and 4b
removes both. Worse, a 4b migration that ran first could not derive `blue_trio` correctly
for the 76 mirrored rows, because treating legacy `left` as blue is precisely what is
wrong with them.

**The ordering cannot be enforced by instructions, so it is not.** Round 3 found the
trap: 4b must migrate automatically on every launch, while 8a needs explicit approval
because it mutates data. On the contributor's Windows machine those cannot be sequenced by
asking - launching the new build would reshape first and derive `blue_trio` from a legacy
`left` that is wrong for exactly the 76 rows in question, irreversibly.

So **the local repair and the shape change become ONE atomic migration step**, and the
alternative dismissed in the first draft is adopted: the 4b migration consumes the
committed audit sidecar directly.

This is not vision evidence inside a migration. The sidecar is a committed, deterministic
lookup table keyed by `natural_key`, and server migration `0006` already did precisely this
and worked. Per row:

- **In the sidecar as `mirrored`** - set `blue_trio` to the trio the frame confirmed, not
  the one legacy `left` claims. The repair and the reshape happen in the same statement,
  so no intermediate state exists to be interrupted.
- **In the sidecar as `agree`** - `blue_trio` from legacy `left`, which the frame confirms.
- **Not in the sidecar at all** - `blue_trio = NULL` with a reason. Never audited, so we
  have no evidence for its orientation and will not invent one. It can be filled later by
  a further audit.

Nothing is left depending on a human running a step in the right order.

Revised sequence:

1. **Part 1** - the audit. Already run: `side-audit-2026-08-01.json`, committed.
2. **Part 4b** - repair-and-reshape, atomic, automatic on launch, sidecar-driven.
3. **Parts 1-4** - the recording fix, stopping new corruption.
4. **Part 5** - the server schema and the dual-version API phase.
5. **Parts 6, 7** - the fit and the reconciliation, both depending on 5.
6. **Part 8b - the pool correction** for the ~6 rows `0006` did not reach, using the
   absolute correction path. This one stays gated on explicit approval: it writes to
   production and no automatic path should.

Part 8a as a separate gated step no longer exists.

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
