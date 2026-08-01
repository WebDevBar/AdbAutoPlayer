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
assignment is a pure function of the heroes. **But round 7 caught that "pure function" was
an intention, not an enforced property** - nothing in the constraint list below stopped a
writer storing the lexicographically SECOND composition as `trio=1`. Every constraint
would pass while `predicted_trio_1`, `blue_trio`, `winning_trio` and the pooled ratings all
silently referred to the other trio. That is defect 1476 again, one level down.

So canonical ordering is itself a constraint, listed with the others below and carrying its
own violation test.

### Storing it once is necessary but not sufficient

Review round 3 showed the "cannot contradict" claim still did not hold: nothing stopped
six heroes being tagged `trio=1` while `winning_trio=2` pointed at a composition that does
not exist. One source of truth removes the DISAGREEMENT; it does not by itself make the
reference valid.

The shape therefore carries constraints, enforced in the schema where SQLite allows and in
a single validated write boundary where it does not:

- **Canonical ordering**: `sorted(trio 1 heroes) < sorted(trio 2 heroes)` lexicographically.
  SQLite cannot express a cross-row comparison as a table `CHECK`, so this is enforced at
  the validated write boundary and verified in bulk - the migration refuses to declare
  itself done while any canonical row violates it, and the same query runs as an assertion
  in the test suite over the real database. Its violation test writes a deliberately
  inverted pair and asserts the boundary rejects it.
- `match_hero.trio IN (1, 2)`.
- **`UNIQUE(match_id, hero_slug)` for identified heroes** - a hero appears once in the
  whole MATCH, not merely once per trio. Round 4 caught the weaker per-trio form: it
  permits the same hero in both trios and therefore two identical `(A,B,C)` trios, at which
  point the canonical sort cannot tell trio 1 from trio 2 and both pointers stop naming a
  composition. The game's shared exclusive pick pool makes this impossible on screen, which
  is precisely why a misread must not be able to represent it.
- **`UNIQUE(match_id, trio, slot)` AND `slot IN (1, 2, 3)`** - the trio-space equivalent
  of today's `UNIQUE(match_id, side, slot)`, plus a domain. Uniqueness alone lets two
  heroes share a slot while another is absent. A domain alone lets a canonical row hold
  slots `(1, 2, 99)` in each trio - round 8's counterexample, which passes uniqueness and
  the three-distinct-heroes count while plate 3 does not exist. Both are needed before
  "plate numbers are fully recoverable" is true rather than hoped for.
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

**Predictions are stored canonically too, for the same reason the compositions are.**
Round 5 caught the earlier wording: a pulled row has `blue_trio = NULL` and the API returns
`predicted_trio_1`, which cannot be written into a column meaning "P(blue wins)" - trio 1
is not blue, and blue is unknown.

So the stored column is **`predicted_trio_1`**, meaning "P(trio 1 wins)", valid for every
row whatever its origin. `predicted_left` becomes a DERIVED view:

```
predicted_left = predicted_trio_1        if blue_trio = 1
               = 1 - predicted_trio_1    if blue_trio = 2
               = NULL                    if blue_trio IS NULL
```

Scoring uses `predicted_trio_1 >= 0.5` against `winning_trio == 1`, which needs no side at
all and works for pulled rows too. The blue-relative view exists only for humans reading a
log, and it is NULL exactly when we have no right to it.

**The migration of existing predictions is NOT mechanical for every row, and round 6
caught the claim that it was.** Both `record_heroes` call sites are on the summary screen
(`solstice_clash.py:603`, `solstice_clash.py:1344`), so the stored `match_hero.side` is
panel-derived - it is the thing that mirrors. A row absent from the sidecar therefore has
no orientation evidence at all, and `predicted_left` cannot be pointed at a trio.

What separates cleanly, and is the reason the canonical shape is worth having:

| value | needs orientation? | why |
|---|---|---|
| `winning_trio` | **NO** | the winner and the heroes are read from the SAME panel pair in the same pass. "The trio in this panel won" survives a swap intact. |
| `blue_trio` | yes | it ties the summary back to the draft, which is exactly the link mirroring breaks. |
| `predicted_trio_1` | yes | the prediction was made on the draft-left trio. |

So the outcome data - the part the model trains on - migrates for every complete row with
no sidecar at all. Only the blue-relative values need a verdict:

- **`agree` (846 rows)** - legacy left is blue. `blue_trio` and `predicted_trio_1` follow.
- **`mirrored` (76 rows)** - legacy left is red. Both invert.
- **absent from the sidecar (~150 decisive complete predicted rows), plus `partial` (4),
  `unreadable` (2), `incomplete` (1)** - `blue_trio = NULL` and `predicted_trio_1 = NULL`.
  Their trios and winner still migrate and still train the model; only their contribution
  to local self-accuracy is lost. **We do not guess.** 8% of audited rows were mirrored, so
  a guess would be wrong roughly one row in twelve, silently.

### Every side-relative local column is mapped, not just the prediction

Round 10 caught "nothing local is lost" resting on a mapping given for `predicted_left`
alone. Three more local columns are side-relative and become uninterpretable the moment the
side labels go:

| column | becomes | orientation needed? |
|---|---|---|
| `predicted_left` | `predicted_trio_1` | yes |
| `left_rating` / `right_rating` | `trio_1_rating` / `trio_2_rating` | yes |
| `left_rank` / `right_rank` | `trio_1_rank` / `trio_2_rank` | yes |
| `match_odds` (side-relative) | trio-relative | yes |

All four need a verdict for the same reason, and it is the reason 1476 was found at all:
**ratings and ranks come from the HEADER, the odds from the DRAFT, and the heroes from the
PANELS.** Header and draft are one frame of reference, the panels another, and mirroring is
exactly the two disagreeing. So none of these can be attached to a trio without knowing
which frame maps to which - unlike `winning_trio`, where winner and heroes come from the
same panel pass and travel together.

The rule is therefore uniform across all of them, and identical to the prediction's:

- **`agree`** - the left-hand value belongs to the trio the legacy `side='left'` heroes form.
- **`mirrored`** - it belongs to the other one.
- **no verdict** - the trio-relative columns are NULL. Not guessed.

`match_odds` is local market data and gets the same treatment: rows that can be oriented are
rewritten trio-relative, rows that cannot keep their raw form in the snapshot and are
excluded from anything that reads odds by trio.

`left_player` / `right_player` are NOT mapped. Names are unreliable by 1476's own evidence
and carry no weight in the fit, so they stay as free-text provenance on the row exactly as
they are, belonging to no trio and claimed for none.

**Nothing is destroyed.** Before the columns are dropped, the migration copies
`(match_id, legacy_side_of_each_hero, outcome, predicted_left, left_rating, right_rating,
left_rank, right_rank, and the side-relative `match_odds` rows)` verbatim into
`legacy_side_snapshot`, an append-only table it never reads again. If better orientation
evidence ever appears - retained frames, a re-push, a contributor's copy - those 150 rows
can be resolved later from the snapshot rather than being gone. It also makes the
migration auditable after the fact, which a destructive reshape otherwise is not.

**Rows that cannot form two complete trios do not enter the canonical shape at all.**
Row 625 is the case: decided, `predicted_left=0.47748`, but five identified heroes. It
cannot have a `winning_trio` because one of its trios does not exist. It goes to
`legacy_side_snapshot` and its `match` row is marked `canonical_state='unrepresentable'`,
excluded from fitting, scoring and sync. That is also what stops the migration predicate
looping on it forever (round 2's first blocker) - "done" means every row is either
canonical or explicitly unrepresentable, never "still waiting".

`scored_predictions` (`store.py:770-781`) currently selects every decisive row with a
prediction, synced included. Under the new column that is CORRECT rather than a bug - a
pooled prediction is another contributor's call and scoring it is meaningful - but it is a
behaviour change and must be a deliberate one, so it is named here.

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

A shape change needs a "not done yet" predicate. Rounds 2 and 7 between them killed two
attempts at one, and the reason both failed is worth stating because it constrains the
answer: a predicate written in terms of the LEGACY columns cannot survive the migration
that removes them. "Decided outcome AND three identified heroes per side" loops forever on
row 625; "decided and `trio` unassigned" reads `outcome`, which no longer exists on the
second launch, and fails with a missing-column error.

**The predicate is therefore expressed purely in terms of what SURVIVES:**

```sql
SELECT EXISTS(SELECT 1 FROM match WHERE canonical_state IS NULL)
```

The migration's contract is that it classifies EVERY legacy match atomically - each one
ends as `canonical_state='canonical'` or `canonical_state='unrepresentable'`, never NULL.
Once it commits, the predicate is false forever, whatever the rows contain and whatever
columns have been dropped. Nothing is skipped: row 625 is not passed over, it is
classified `unrepresentable`, which is what makes it terminal rather than perpetually
pending. (An earlier passage describing 625 as "skipped by both the migration and the
predicate" was the contradiction round 7 flagged; skipping is exactly what caused the loop.)

`canonical_state` is added by the same migration, before any classification, and is the one
column the predicate depends on - so the check is `canonical_state` absent OR any row NULL.

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

**LOCALLY there is no separate repair step, and round 9 caught this section still
describing one.** `scripts/solstice_frame_side_audit.py --apply` mutating `match_hero.side`
and `match.outcome` cannot happen: those columns are gone by the time anything the operator
runs could touch them, and the reshape is automatic on launch. The sidecar's 76 verdicts are
consumed INSIDE the atomic migration, which is the only thing that ever acts on them. The
script keeps its classify/report role and loses `--apply` entirely.

One correction it does still need: its documented assumption that summary-header names are
"side-correct" (lines 722-724) is contradicted by 1476 and must be removed. Names are never
repaired.

**What remains under Part 8 is the POOL, and only the pool** - the server rows `0006` did
not reach. `comps_key` does not change there either (identity is orientation-free), so a
correction is an update to an existing row, never a new one.

**How many rows reach the server is answered below, not here.** An earlier draft said all
76 must be pushed; that was written before we discovered `0006` had already corrected most
of them, and following it would re-corrupt roughly 70 repaired rows.

**That pool correction is the one step that mutates data the operator can see.** It runs
only on an explicit go-ahead, after a `pg_dump`.

### The dropped columns must leave the migration machinery too

Round 9 found the concrete way this bites. `ADD_COLUMNS` in `data/solstice_clash/migrate.py`
still declares `("match", "predicted_left", "REAL")` at line 46. That list exists to upgrade
databases predating a column - so the launch AFTER the reshape re-adds `predicted_left` as an
empty column, and every later launch sees a schema that looks current while the value is
gone. The reshape must delete the dropped columns from `ADD_COLUMNS` and from `schema.sql`
in the same change, and a test must assert that running `migrate.apply()` twice leaves no
removed column present.

**Every shipped consumer of the dropped columns is migrated or retired in the same binary**
- the "one release" rule covers the app, and these are not the app:

| consumer | disposition |
|---|---|
| `scripts/solstice_side_audit.py:65` | rewrite onto `trio` / `winning_trio` |
| `scripts/solstice_crowd_agreement.py:74` | rewrite onto `predicted_trio_1` / `winning_trio` |
| `scripts/solstice_frame_side_audit.py:260` | reads legacy `side` to CLASSIFY; it must read `legacy_side_snapshot` after the reshape, since that is where the pre-migration sides now live |
| `scripts/solstice_walkforward.py` | check and rewrite - it scores predictions |
| `scripts/dry_run_draft_log.py` | check; draft-side only, likely unaffected |
| `data/solstice_clash/migrate.py:46` + `schema.sql` | drop the obsolete entries, as above |

None of these run on a user's machine, so they cannot break a contributor's install - but
they are how every question in this document was answered, and a reshape that silently
breaks all five leaves no way to check its own work.

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
4. **`0007` handles these rows by binding the draft-relative group to the opposite trio.
   It does not touch their heroes or their outcome.** Rounds 11 and 12 together settled
   this, and round 12 killed a wrong answer of mine that would have created the very
   contradiction it was meant to prevent.

   The situation: `0006` swapped only heroes and outcome
   (`0006_backfill_identity.py:318, 326, 347`), leaving predictions, ratings, ranks, pools
   and odds alone because they are draft-relative. The ~6 rows it never reached therefore
   still have **summary-relative heroes and outcome that agree with each other**, and a
   draft-relative group that does not agree with them.

   Only one thing is actually wrong with those rows, and it is not the winner. Heroes and
   outcome come from the same panel pass, so "the trio in this panel won" is true whichever
   panel we call left - the same orientation-freedom that makes `winning_trio` migrate
   without a verdict everywhere else in this document. Inverting the winner would BREAK a
   correct fact. Inverting the winner and the draft-relative group while leaving heroes
   alone - my round-11 wording - makes the winner contradict its own trio outright.

   So the correction is the minimal one: canonicalize heroes and outcome exactly as for any
   other row, and attach the draft-relative group to the OTHER trio than a naive left-to-trio
   mapping would give. Nothing is swapped, nothing is mutated in place; the only thing that
   changes is which trio those values are recorded against. That is also the same rule the
   local migration applies to `mirrored` rows, so both sides of the system end at the same
   canonical truth by the same reasoning - the ~70 rows `0006` already swapped map naively,
   these ~6 map inverted, and the results agree.

   Rows with no verdict are canonicalized on outcome and heroes alone and get NULL for the
   draft-relative group - the same honest rule, for the same reason.

   This puts an operator-visible mutation inside a migration, which is acceptable here only
   because server migrations are already manual and gated (`docs/DEPLOY.md`): `0007` IS the
   approved step, run after a verified `pg_dump`. It must not be made to run automatically.

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

### Everything client-side ships in ONE binary

Round 5 caught the sequence being read as a release order, which cannot work.
`_ensure_schema` runs inside `MatchStore` construction (`store.py:257, 297`), before any
recording happens. So the moment a user launches the new build, the database is reshaped -
and every reader and writer in that same binary must ALREADY speak the new shape.

There is no release in which the migration has run and the code has not. Parts 1-4, 4b, 6
and 7 are one client release. The list below is the order of WORK, not of shipping:

1. **Part 1** - the audit. Already run: `side-audit-2026-08-01.json`, committed.
2. **Part 4b** - repair-and-reshape, atomic, sidecar-driven, automatic on launch.
3. **Parts 1-4** - the recording fix.
4. **Parts 6, 7** - the fit and the reconciliation.
5. **Part 5 - the server schema and the dual-version phase. DEPLOYED AND LIVE before
   step 6.** Round 6 caught the order stated backwards, and round 8 caught the numbering
   still contradicting the prose after it was fixed in words only. Production accepts only version 4 today (`gameretro-adb-api/app/config.py:24`),
   so a v5 client shipped first would fail every push against a server that has never heard
   of the trio contract. The server accepting BOTH 4 and 5 is what makes the client release
   independent - the window has to exist before the client needs it, not after.

   **Pull must also become versioned, and today it is not.** `sync.py:264` sends no schema
   version on the GET at all, so "pull returns the shape matching the requested version"
   has no mechanism behind it. The client sends its version as a query parameter; a request
   without one is treated as version 4, which is exactly what every already-installed
   client is. No negotiation and no fallback: the client asks for the one shape it can
   parse, and old clients keep getting the old shape until they are replaced.

6. **Ship the client** - steps 2, 3 and 4 as one binary. Nothing in them may reach a user
   alone, and none of them may reach a user before step 5 is live.
7. **Part 8b - the pool correction** for the ~6 rows `0006` did not reach. Stays gated on
   explicit approval: it writes to production and no automatic path should.

Part 8a as a separate gated step no longer exists.

## Risks

| risk | mitigation |
|---|---|
| The resolver refuses too often and matches go unrecorded | Unresolved rows are still stored with everything side-neutral; only the outcome is withheld. The margin rule is tested against one and two misreads |
| `0007` lands on a pool that had `0005` and `0006` hours earlier | `pg_dump` first, restore-verified, and the migration is mechanical: trios are derivable from `match_hero`, which is unchanged |
| The reconciliation collapses a genuine rematch | The ±2 minute window is the test, not `comps_key` alone. Ids 1/45 are the committed fixture |
| Repairing the 76 corrupts good rows | Dry-run default, `legacy_side_snapshot` written before any drop, names untouched, and the same two-phase swap the repair already uses |
| A pulled row's missing side breaks the model fit | `matches_for_fit` moves to trio membership; the model never used side |

## What this does not do

It does not change how a match is played, watched, predicted, displayed or bet on. It does
not change any PREDICTION - `predicted_left` is re-expressed as `predicted_trio_1` and
inverted where the sidecar says the row was mirrored, but the probability the model
assigned to a given trio is preserved exactly, and the ~157 rows with no orientation
evidence keep theirs verbatim in `legacy_side_snapshot`. It does not attempt to explain why row 1476 stored
`left_player = Rocky` when the header showed Guicts - both reviewers failed to account for
it, no summary frame exists from that window, and Part 3 exists so the next such question
is answerable.
