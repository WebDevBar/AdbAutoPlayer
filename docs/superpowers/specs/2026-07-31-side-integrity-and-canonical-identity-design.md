# Side integrity and canonical match identity - design

Written 2026-07-31. Revised after peer-review round 1 (Codex and Fable, independently).
Covers two defects found while auditing the betting model, and the documentation debt
that audit created.

The betting work that started this is NOT part of this spec. It closed: the left-only
rule and the crowd-as-filter both failed their selection audits, and nothing about the
model changes here.

## The problems, with evidence

### P1 - the summary panel-to-side mapping flips; the header does not

Six pairs of rows in the client database are the same match recorded twice, 1 to 16
seconds apart. In four of them the hero teams AND the outcome are mirrored between our
row and the other contributor's:

```
1043 local   left: lorsan,sonja,valka    right: hepler,silven,thador   outcome left
1046 synced  left: hepler,silven,thador  right: lorsan,sonja,valka     outcome right
```

Both rows say the same three heroes won. `draft-1043.png` confirms our row is correct for
that match: MERLIN really is on the left at rating 4341.

**The mechanism is now located.** Round 1 established that the three groups of fields
have three different provenances:

| field | source | mirrored? |
|---|---|---|
| `left_player` / `right_player` | summary HEADER, read by x-position (`summary.py::_read_players`) | no - identical in both rows of all four pairs |
| `left_rating` / `right_rating` | the DRAFT screen (`solstice_clash.py:1198-1199`, `_draft_ratings`) | no |
| `match_hero.side`, `outcome` | summary PANELS (`_winner_by_panel_tint`, `cell_type='summary_hero'`) | YES |

`_winner_by_panel_tint` decides which *panel* won and explicitly "ignores left and right
entirely"; the panel is then mapped to a side through the cell config. That mapping is
the defect. The header, read by x-position, is unaffected - which is why names and
ratings stay put while heroes and outcome flip.

The audit still measures the rate and the correlates; what it no longer has to do is
guess where the flip lives.

### P1a - mirrored rows corrupt our own accuracy figures

`predicted_left` is set from the DRAFT screen (`solstice_clash.py:872`, from
`merge_screens(draft_reads, locked)`), before the fight. `outcome` comes from the summary
panels. So in a mirrored row the prediction and the outcome are recorded against opposite
frames, and every such row is scored backwards: a hit counts as a miss and vice versa.

Measured model accuracy is therefore understated by roughly twice the mirrored rate. This
is a second, independent reason to run the audit.

### P2 - identity does not collapse those duplicates

`natural_key` on client (`matchkey.py:62-71`) and server (`app/identity.py:104-136`)
hashes `outcome | sorted(left) | sorted(right) | 10-minute-bucket`. Two defects:

- **Orientation-sensitive.** Mirror a match and the first three fields all change, so one
  match earns two keys and both rows persist.
- **Bucket boundary.** `minute // 10` puts 08:09:53 and 08:10:02 in different cells. Ids
  1042/1044 are one match nine seconds apart, stored twice; 202/203 likewise.

Across the client database: every completed match has a distinct (winner-trio,
loser-trio) pair except the six known duplicate pairs, all 1-16 seconds apart. **There
are zero genuine collisions.** Uniqueness is not the failing property; stability is.

Exact counts are deliberately not pinned here - the database is live and grew during
review. See "Snapshot discipline".

### P3 - the ledger records two claims today's work falsified

`model-findings-ledger.md:278` states a side intercept of `+0.244` (now `+0.16` and
decaying); line 309 states that "the left/right labels mean the same thing on all three
machines", which P1 disproves. Neither closure decided today is recorded.

## Snapshot discipline

The client database and the frame corpus both grow continuously. Round 1 found the spec's
pinned totals already stale, and one frame (`draft-625.png`) whose row has only five
identified heroes.

Therefore: **every script in this spec takes an explicit `--cutoff <ISO timestamp>` and
ignores anything captured after it.** No test asserts an absolute row count; tests assert
derived relationships ("the six known duplicate pairs merge", "distinct pairs + merges ==
completed rows").

Snapshots use whatever the database actually is, which differs by side and was got wrong
once already:

- **Client (SQLite)** - `sqlite3.Connection.backup` or `VACUUM INTO`. A filesystem copy is
  not consistent on a live WAL database.
- **Server (Postgres)** - `pg_dump` restored into a scratch database. `VACUUM INTO` is
  SQLite-only and has no Postgres equivalent; `app/config.py:15` defaults to
  `postgresql+psycopg://` and `docker-compose.yml` runs `postgres:17-alpine`. The final
  reviewer caught the spec telling an implementer to run a SQLite command against Postgres.

Counts quoted in this document come from the CLIENT database. The server pool holds a
different row set, and the migration in Part 4 targets the server. They are never assumed
equal.

## Scope

1. A read-only frame audit measuring how often our stored side disagrees with the draft
   frame, and what that correlates with.
2. A gated repair mode for our local rows.
3. Ledger corrections and the two new closures.
4. Canonical, orientation-invariant identity on client and server, with a proximity merge
   replacing the time bucket.

Out of scope: `odds.py`, the betting rule, the auto-bet settings, the model. The
Blue/Red/Both auto-bet control discussed separately is not part of this.

## Decisions taken

**D1 - pick order: CLOSED, moot.** The mode drafts in a fixed order - 1 Blue, 2 Red,
3 Red, 4 Blue, 5 Blue, 6 Red - confirmed by the operator and matching `draft-1043.png`,
whose plates read Blue 1/4/5 against Red 2/3/6. The plate number is therefore a constant
function of `(side, slot)` and needs no OCR at all; the table lives in
`draftlog.py::PLATE_NUMBER`.

This closes it as a MODELLING lead too, which was the reason it was open. Pick order was
on the table as the direct test of the first-pick explanation for the side effect - but a
deterministic function of side and slot carries no information the model does not already
have from side. It cannot explain the left bias because it *is* the left bias restated.
Recorded in the ledger rather than left as a lead.

**D2 - pool reconciliation: CLOSED, reconcile the server too.** The migration does not
merely dedupe; it also corrects orientation on the survivor, using the audit's
frame-confirmed verdict. This is why Part 1 must precede Part 4: without the audit the
migration falls back to survivor rule 3 (earliest capture), and in pair 1108/1109 the
earliest row is the MIRRORED one, so a rule-3 migration would enshrine the wrong
orientation in the pool.

Sequence, therefore: audit (Part 1) -> local repair (Part 2) -> server migration with
orientation resolved (Part 4). Running Part 4 before Part 1 is a specified error, not a
scheduling preference.

---

## Part 1 - the frame audit (read-only)

New script `src-tauri/src-python/scripts/solstice_frame_side_audit.py`.

**Input.** Files matching `draft-<id>.png` under `/mnt/vault/adbautoplayer/solstice-frames`
(overridable by `SOLSTICE_FRAME_DIR`), captured at or before `--cutoff`.

**Per frame:**

1. Read the draft screen's blue and red hero plates. The draft screen carries true
   blue/red plate colours, which is why it can adjudicate.
2. Compare the blue trio against the row's `side='left'` trio as a SET, ignoring slot.
3. Classify: `agree`, `mirrored`, `partial` (neither, some overlap), `unreadable` (frame
   not parseable with confidence), `incomplete` (the row has fewer than three identified
   heroes on a side, e.g. `draft-625.png`), `no_row` (no matching match id).

`partial`, `unreadable` and `incomplete` are separate on purpose. Collapsing them would
let a frame-reading failure or a known-bad row count as evidence about the summary reader.

**Output** `docs/solstice-clash/side-audit-<cutoff date>.md`:

- Counts and rate per verdict, with a Wilson interval on the mirrored rate.
- Whether mirroring correlates with `outcome`, hour of day, `left_rating > right_rating`,
  and whether the row has a known duplicate. Each a 2x2 with a two-proportion z. These
  distinguish "the panel order follows the winner" from "random" from "changed at a point
  in time".
- The estimated correction to scored accuracy implied by P1a.
- The per-match table for every non-`agree` verdict.
- An explicit statement of what the audit CANNOT establish: it compares our summary read
  against our draft read on one machine. It cannot say whether another contributor's rows
  are right - we do not have their frames.

The script opens the database read-only and writes nothing but that file.

### Testing

- One fixture test per verdict class, using committed frames, with the classification
  pinned.
- A test that the connection is read-only and a write raises.
- A test that `incomplete` and `no_row` are distinguished, using `draft-625.png` as the
  `incomplete` fixture.
- A test that `--cutoff` excludes later rows.

## Part 2 - repair mode (gated, off by default)

Same script, `--apply`. Default is dry-run: prints what it would change and exits
non-zero if anything would, so it doubles as a check.

With `--apply`:

1. Take a **consistent** snapshot using SQLite's own backup API (`sqlite3.Connection.backup`
   or `VACUUM INTO`), never a filesystem copy - the database is live and in WAL mode, so
   `cp` can miss committed pages. Verify the snapshot opens and its schema matches. Abort
   otherwise.
2. For every `mirrored` row, change **only**:
   - `match_hero.side`, swapping `left` and `right`
   - `outcome`, flipping `left` and `right`
3. **Do NOT touch** `left_player`/`right_player`, `left_rating`/`right_rating`, or
   `left_rank`/`right_rank`. Per P1 the header is side-correct and ratings are
   draft-derived; swapping them would move correct data onto the wrong side. This is a
   correction from round 1, where the spec had them swapped.
4. **Do NOT touch** `predicted_left`. Per P1a it is draft-relative and already refers to
   the correct side; the repair brings `outcome` onto the same frame. Flipping it would
   re-break the pairing. (Round 1 raised the opposite; the trace at
   `solstice_clash.py:872` settles it.)
5. The side swap must be **two-phase**: `match_hero` carries `UNIQUE(match_id, side,
   slot)`, and SQLite checks constraints per row, so a single
   `UPDATE ... SET side = CASE ...` fails mid-statement. Write a sentinel value first,
   then the target value.
6. Leave `partial`, `unreadable` and `incomplete` rows untouched, listing them for manual
   review.
7. Write a repair log beside the snapshot: every id changed, before and after.

Repair runs only on an explicit go-ahead after the report has been read. This spec does
not authorise running it.

### Testing

- Idempotence: run `--apply` twice; the second run classifies the repaired row as
  `agree` and must perform no write. (Round 2 caught that the original "apply twice and
  it returns to its original state" test is impossible - after one repair there is
  nothing left to mirror.)
- The low-level swap primitive, tested directly and separately, is its own inverse when
  applied twice to the same row.
- Assert `--apply` on a database with zero mirrored rows performs no write at all.
- Assert a failed snapshot aborts before any write.
- Assert `predicted_left`, player names, ratings and ranks are byte-identical before and
  after a repair.
- Assert the two-phase swap succeeds where a naive single UPDATE raises
  `IntegrityError` - the naive form is exercised in the test so the constraint is proven,
  not assumed.

## Part 3 - ledger corrections

Edits to `docs/solstice-clash/model-findings-ledger.md`:

1. Add to "Do NOT test these again": **the crowd as a betting FILTER**, distinct from the
   already-closed crowd-as-model-input. Evidence: 90-cell grid, best agreement gain +3.8
   points, conditional permutation p = 0.577, both cross-theme directions reverse;
   continuous forms (pool log-ratio, pool x crowd size, spectator calibration) all worsen
   held-out logloss with the pool coefficient flipping sign across themes (+0.146 /
   -0.156); spectator count carries no independent information (p = 0.16); `left_odds`/
   `right_odds` are redundant with the pools (R^2 = 0.70) and worsen logloss.
2. Add: **the left-only / never-stake-right betting rule.** Permutation p = 0.25 under
   three nulls (Fable) and 0.577-0.97 (Codex); reverses out of theme; its rationale was a
   base-rate error - against a 42.8% right base rate the model's right calls carry +5.3
   lift versus +2.6 for its left calls.
3. Correct `+0.244` (line 278) to the walk-forward trajectory: ~+0.10 early, ~+0.31
   mid-theme, +0.16 at the theme's end. Mark it as a moving quantity.
4. Strike the "all three machines" claim (line 309), replacing it with P1 and a pointer to
   the audit file.
5. Record that `install.instance_uuid` appears in `match.contributor_uuid` on rows the
   pool echoes back, so our own rows can be miscounted as an external collector. Note this
   invalidated an earlier confound test (p moved from 0.019 to 0.13 once corrected).
6. Record P1a: scored accuracy is understated while mirrored rows exist.

No claim enters the ledger that is not backed by a number in this spec or the audit output.

## Part 4 - canonical identity

### The comps key

Both implementations compute:

```
a, b      = sorted([",".join(sorted(side_a_slugs)), ",".join(sorted(side_b_slugs))])
comps_key = "sha256:" + sha256(event_slug + "|" + a + "|" + b)
```

**The event is part of identity.** Round 8 found that without it, two matches in
different events using the same six heroes within the window would coalesce. The server
already resolves `event_slug` on ingest and `Match.theme_id` belongs to an `Event`, so the
information is present and semantically load-bearing. The full server identity is
therefore `(event_id, comps_key, occurrence)`, and the occurrence lookup, its index, the
migration and the tests are all partitioned by event.

The event slug is used rather than the numeric id so the client can compute the same
`comps_key` without knowing server-side ids.

**Resolving the slug on the client is a defined, ordered fallback**, because
`match.event_id` is not always populated: 120 of the 1,200 keyed completed rows have it
NULL, all 120 recoverable through `theme_id`, none unrecoverable. Round 9 found the spec
assumed the slug was simply available. The chain is:

1. `match.event_id -> event.slug`
2. failing that, `match.theme_id -> theme.event_id -> event.slug`
3. failing both, **leave `comps_key` NULL** and skip the row. A NULL `comps_key` is
   already the provisional state, so such a row is simply not pushable until its event
   resolves - it is never given a guessed or partial key.

`finalise_identity()` uses the same chain and must use the exact slug later sent as
`event_slug` on push, so client and server hash identical input. Theme is deliberately NOT included: it is
resolved from the capture window server-side and can be backfilled later, which would
change the key retroactively - the same reason the current implementation excludes it
(`identity.py:94`).

The two trios, each sorted internally, then the two sorted against each other. **The
outcome is not in the key at all.**

Round 3 rejected the earlier winner-first form: it survives a disagreement about which
SIDE a trio was on, but not a disagreement about which trio WON. If one contributor's
`_winner_by_panel_tint` misreads the tint, the winner and loser halves swap and the two
captures earn different keys again - one match, two rows, the exact defect being fixed.
Sorting the trios against each other removes the outcome from identity, so any
disagreement about sides OR winner still lands on one key.

The outcome then lives only as row data, where two captures of one match may disagree.
That is a conflict to resolve, not an identity question, and the survivor rule below is
what resolves it. This is strictly better than hiding the conflict behind two rows.

No time. No names, ranks or ratings.

Rationale, each point measured:

- **Orientation- and outcome-invariant**, so mirrored captures collapse, and so does a
  capture that disagrees about who won. Merges all six duplicate pairs.
- **No time**, so no boundaries to straddle. Boundaries, not clock skew, split the two
  failing pairs - those captures are 9 and 12 seconds apart.
- **No names/ranks/ratings.** Ranks are NULL on every row. Names are OCR-fragile in a
  side-dependent way: the operator's profile art reads as `GAME` on one side and
  `GAMERETRO` on the other, and rows 1133/1136 read one player as `m` and `mn`.
- **Uniqueness is not sacrificed** - zero genuine collisions across the corpus. Dropping
  the outcome widens the merge class to "the same two trios, either result", and there is
  still no pair in the corpus that this merges wrongly.

`is_complete` is unchanged: three identified heroes per side, outcome in `{left, right}`.

### Client identity vs server identity

Round 1 showed these cannot be the same string, because the server's carries an occurrence
the client cannot compute. They are now defined separately:

- **The client's local identity is `comps_key` alone**, stored in a new column
  `comps_key`, indexed but **NOT unique**. It exists for the local dedupe backstop only.
- **`natural_key` on the client becomes nullable and is only ever the value the server
  returned.** It keeps its UNIQUE constraint - the server's keys are unique by
  construction. A row that has never been pushed has `natural_key IS NULL`, which the
  existing push gate already understands.
- **The server's `natural_key` is `comps_key + ":" + occurrence`** for every ACTIVE row -
  that is, every row with `superseded_by IS NULL` - with `comps_key` stored in its own
  indexed non-unique column.
- **A superseded row keeps whatever key it already had**, including a legacy
  pre-migration key. Superseded rows are tombstone targets, not identities: their only job
  is to let a client holding that key find the survivor. Round 5 caught the earlier
  wording, which claimed the canonical format for every row while also forbidding the
  rewrite of merged survivors - those two cannot both hold.
- The two forms cannot collide: an active key always carries a `:<occurrence>` suffix and
  a legacy key never does.

Consequences that must be handled, all raised in round 1:

- **The push gate must change.** `store.py::pushable_matches()` currently requires
  `natural_key IS NOT NULL`, so a nullable-until-pushed `natural_key` would make every
  new local row permanently unpushable - a row needs a server response to get a key, but
  needs a key to be sent. The gate becomes `origin='local' AND comps_key IS NOT NULL AND
  pushed_at IS NULL AND push_rejected_reason IS NULL`. `comps_key` is non-NULL exactly
  when `is_complete` held, so it carries the completeness meaning the old predicate had.
- **Local dedupe cannot live in `record_match()`.** Round 6 found the spectate flow
  creates the match row BEFORE its heroes exist (`solstice_clash.py:1186`), so `comps_key`
  is not computable at that point; identity is finalised later through `set_natural_key()`
  at line 1338. `record_match()` therefore keeps inserting a provisional row with
  `comps_key` NULL, which the push gate already excludes.
- A new transactional **`finalise_identity(match_id)`** replaces `set_natural_key()`,
  called once the heroes and outcome are recorded. It computes `comps_key`, looks for an
  existing local match with the same `comps_key` whose occurrence bounds are within the
  window, and either:
  - **no match** - stores `comps_key`, initialises the bounds, and the row becomes
    pushable; or
  - **one or more matches** - widens the bounds of the EARLIEST of them, marks the new row
    and any other bridged rows `superseded_by` that earliest row, and re-points any
    existing `superseded_by` references at it so the chain stays one level deep. Bridging
    several local occurrences at once is the case round 7 found missing.
    Hero rows, the saved draft frame and the odds sample stay on the superseded row rather
    than moving: they are evidence of a second observation, and Part 1 needs them to
    adjudicate orientation.

**A locally superseded row is still PUSHED.** Round 7 found the opposite rule creates a
real divergence: if the capture that bridges two local occurrences is withheld, the server
never receives the evidence that they should coalesce, and the two sides cluster
differently forever. The server is the sole deduplicator - it answers `duplicate`, the
client adopts the survivor's key, and `pushed_at` is set so nothing loops. Local
supersession exists only to stop local analysis double-counting, which is a separate
concern from identity.

The push gate is therefore `origin='local' AND comps_key IS NOT NULL AND pushed_at IS NULL
AND push_rejected_reason IS NULL` - completeness and not-yet-sent, with no supersession
term.
- `adopt_canonical` (`sync.py:188`) continues to write the server's `natural_key`, and no
  longer breaks the backstop, because the backstop now looks up `comps_key`, which never
  changes.
- `upsert_synced` (`store.py:711-715`) matches on the server's `natural_key`, which is
  correct for pool echoes and unchanged.
- The pinned-digest tests assert that client and server compute the same **`comps_key`**.
  The round-1 claim of byte-identical `natural_key` is withdrawn as impossible.

### Occurrence assignment - one rule, both places

Round 1 found the migration's chained walk and the ingest's pairwise check disagree
(captures at t, t+90s, t+180s chain into one occurrence in the migration but not
necessarily at ingest). The rule is therefore stated once and used by both:

> An incoming capture joins **every** occurrence with the same `comps_key` that has any
> capture within the window. If it bridges more than one, those occurrences are
> **coalesced** into the lowest-numbered of them, and the coalescing is written to the
> merge log. If it bridges none, it starts a new occurrence.

Coalescing is what makes this arrival-order independent, and round 2 showed that
attaching to the *nearest* occurrence is not enough. Counterexample with a 120s window:
captures at t, t+181, t+91. Chronologically they chain into one occurrence; arriving as
t, t+181, t+91 the first two open occurrences 0 and 1, and a nearest-only rule leaves
them split forever because existing occurrences are never revisited. Under coalescing,
t+91 bridges both and merges them, reaching the same end state by either route.

**A merged capture must still extend the occurrence's time bounds.** Round 4 found that
ingest currently rolls the duplicate insert back (`matches.py:83`) and keeps nothing, so
the bridging evidence is lost: captures at t, t+100s, t+200s give one occurrence in the
migration but two at ingest, because t+100s left no trace for t+200s to attach to.

Each occurrence therefore carries `captures_min_at` and `captures_max_at`, and the
proximity test is against those bounds rather than against retained rows. A capture
accepted as a duplicate updates the bounds in the same transaction that returns
`duplicate`. Bounds are sufficient for single-linkage: the nearest point of a cluster to
any new capture is always one of its two extremes.

The resulting CLUSTERING is single-linkage on the window, which is order-independent by
construction. The migration applies the identical rule while inserting in `captured_at`
order.

**Occurrence NUMBERS are not order-independent, and are not required to be.** Round 6
found that two disconnected captures six hours apart get `:0` then `:1` in one arrival
order and the reverse in another. That is inherent to any counter assigned on arrival.
It is harmless because the two processes never run on the same data: the migration runs
once over history and its assignment becomes authoritative, and every capture afterwards
goes through ingest, which only ever appends. Clients adopt whatever key the server
returns and never compute it.

The tests therefore assert **cluster membership** equality across arrival orders, never
key equality. A test asserting identical keys across orders would be asserting something
the design does not promise and does not need.

The documented limitation is unchanged in kind: a chain of captures each within the window
of the next merges matches arbitrarily far apart in time. Observed same-match gaps are
1-16 seconds against a 120-second window, so this needs a pathological pattern, and the
merge log makes it visible if it happens.

### Concurrency

Two simultaneous inserts of genuinely different matches sharing a `comps_key` would both
mint the same occurrence; the loser hits the UNIQUE constraint. The existing
`IntegrityError` branch (`matches.py:124-138`) currently returns `duplicate`, which would
**silently drop a real match**. It must instead re-run the occurrence lookup once and
retry, returning `duplicate` only when the proximity rule genuinely matches an existing
row.

### An accepted limitation, stated plainly

Two genuinely different matches **in the same event** with identical trios captured within
the window are **indistinguishable** by any field we store, and will be merged. No retry,
discriminator or ordering rule can avoid this - the information is simply not present.
Matches in different events are distinguishable and are separated by the event component
of the key.

It is accepted because no two distinct matches in the corpus share a comps key within the
window - the closest such pair, ids 1 and 45, is 31.6 hours apart and is separated by the
occurrence rule. The merge log records every merge, so if it ever occurs it is detectable
after the fact rather than silent.

### The proximity window

**±2 minutes**, a named constant on both sides with its reasoning in a comment: observed
same-match capture gaps are 1-16 seconds, a 7x margin at the far end, while no two
genuinely distinct matches in the corpus share a comps key **within the window**.

The unqualified form of that sentence was false and is corrected here. Ids 1 and 45 DO
share a canonical key - identical trios in identical orientation, opposite winners - 31.6
hours apart. Dropping the outcome from the key widened the merge class by exactly that one
pair, and the occurrence rule is what keeps them separate. It is therefore load-bearing,
not a theoretical safeguard.

### Which row survives, and which orientation it carries

These are two separate questions, and round 3 showed that conflating them creates a key
collision: if the survivor is chosen from a higher occurrence and then rewritten to the
lower occurrence's key, it clashes with the row already holding that key.

**Identity survivor - mechanical, never rewrites a key.** The row holding the
lowest-numbered occurrence's key keeps that key and stays active. Every other row in the
coalesced group is marked `superseded_by` that row and **retains its own existing key**,
so no key is ever reassigned and no collision is possible.

**Orientation - a data question, resolved separately.** The active row's `outcome` and
hero sides are set from, in order:

1. The row whose orientation was **confirmed against a draft frame** by Part 1.
2. Failing that, the row from the contributor with the lower measured mirrored rate, once
   the audit has established one.
3. Failing that, the row's existing values are left alone and the group is flagged
   `orientation_unresolved` in the merge log.

Rules 1 and 2 need the audit, which is why Part 1 precedes Part 4 - see D2. Running the
migration before the audit would fall back to rule 3, and for pair 1108/1109 the earliest
capture is the mirrored row, so that ordering would write the wrong orientation into the
pool. Any group that still lands on rule 3 is flagged `orientation_unresolved` in the merge
log for a later correction pass.

Nothing is deleted. Superseded rows are retained with their keys intact.

### Migration (server)

Against a snapshot, in one transaction:

1. Add `comps_key` (indexed, non-unique), `occurrence`, `superseded_by` (nullable FK to
   the surviving match), and the occurrence bounds `captures_min_at` / `captures_max_at`.
   All of them exist before any step uses them - round 3 caught `superseded_by` being
   written before it was added, and round 5 caught the bounds missing entirely.
2. Backfill `comps_key` for every match from its stored hero compositions. The outcome is
   not an input.
3. Assign occurrences by inserting in `captured_at` order under the **coalescing rule
   defined above** - the same single-linkage rule ingest uses, not the nearest-capture
   rule that round 2 rejected. Round 3 caught this step still naming the rejected rule.
   Initialise each occurrence's bounds from its first row and widen them as rows join, so
   the migration leaves the bounds in exactly the state repeated ingest would have.
4. Where a group holds more than one row, apply the identity-survivor rule, set
   `superseded_by` on the others, resolve orientation per the rules above, and record
   every id, contributor, old key and the orientation verdict in a new `match_merge_log`
   table.
5. **Rewrite `natural_key` to `comps_key:occurrence` for every ACTIVE row**, and leave
   every superseded row's key untouched. Because the suffix distinguishes the two forms,
   and because a key is never moved between rows, no rewrite can collide. Superseded rows
   keep the key clients already know, which is what makes the tombstones resolvable.

**Pull semantics must change with it.** `GET /v1/matches` currently selects every match
above a sequence with no filter, and the client's `upsert_synced` dedupes only by
`natural_key`. Without a change the pool would keep serving both rows under two different
keys and the migration would reconcile nothing - a round-2 finding. Therefore:

- The pull EXCLUDES rows with `superseded_by` set from the normal result set.
- Tombstones are delivered on **their own cursor**, not the match `seq` cursor. Marking an
  old row superseded does not advance its `seq`, so a client whose cursor is already past
  it would never see the notice - a round-3 finding. A `match_supersession` table with its
  own autoincrementing `seq` is written by the migration and by every subsequent merge,
  and the client tracks a second cursor against it.
- Each tombstone carries `{natural_key, superseded_by_natural_key}`.
- Pulled rows are additionally deduped by `comps_key` and the proximity rule, not by
  `natural_key` alone, so a survivor re-delivered inside `PULL_OVERLAP` cannot be inserted
  twice.
- The client, on seeing a tombstone, deletes its row for the old key **only if that row's
  `origin` is `synced`**. A `local` row is never deleted by a pull - it is this install's
  own observation with its own hero evidence.
- For a `local` row it instead clears `natural_key`, `pushed_at` AND `push_rejected_reason`
  in one transaction. Round 3 caught that clearing `natural_key` alone leaves the row
  permanently unpushable, because `adopt_canonical` had already set `pushed_at` and the
  gate still requires it to be NULL. The row is then re-pushed once and re-identified; the
  server answers `duplicate` with the survivor's key, adoption records it, and the cycle
  terminates. A test asserts it terminates rather than looping.

**`adopt_canonical` must become origin-aware.** Today (`store.py:677-682`) it deletes ANY
row already holding the server's key, on the documented assumption that such a row can
only be a synced copy. Under an orientation-invariant key that assumption fails: both rows
of a LOCAL mirrored pair resolve to one key, so the second push would delete the first -
possibly the frame-confirmed one, with its hero evidence. The branch becomes: delete only
when the clashing row's `origin` is `synced`; when it is `local`, mark it superseded
locally and keep it. This contradiction with the design's "nothing is deleted" intent was
found in round 2 by the second reviewer.

This is separate from the orientation question in D2: that decides which reading is
right, this is about not serving two rows for one match. Both are in scope; they are
just different steps.

Client startup migration: add `comps_key` (indexed, non-unique), `superseded_by`
(nullable, referencing another local match), and the same `captures_min_at` /
`captures_max_at` bounds, backfill `comps_key` and the bounds, and leave existing
`natural_key` values alone.

The client needs the bounds for the same reason the server does: `record_match()`
currently discards a duplicate observation outright, so without stored bounds three local
captures at t, t+100s and t+200s would split into two occurrences locally while the server
produced one. The local dedupe path updates the bounds when it recognises a duplicate,
exactly as ingest does. `natural_key` is already nullable and UNIQUE
(`schema.sql:151`), so no constraint change is needed there.

`superseded_by` is required, not optional. Round 4 found the client had no representable
state for a superseded local row: two local rows can now resolve to one server key, and
because client `natural_key` stays UNIQUE, only one of them can hold it. The second row
must be recorded as superseded rather than left unadopted, or it stays pushable and loops.

Three things follow, and all three are part of this spec:

- **The push gate does NOT exclude superseded rows** - see the finalisation rules above.
  It is `origin='local' AND comps_key IS NOT NULL AND pushed_at IS NULL AND
  push_rejected_reason IS NULL`. `comps_key IS NOT NULL` carries the completeness meaning
  the old `natural_key IS NOT NULL` predicate had.
- **`adopt_canonical`, on a `local` clash**, keeps the existing holder of the key, sets
  `superseded_by` on the row being adopted, sets its `pushed_at` so it is not retried, and
  leaves its `natural_key` NULL. Its hero rows are untouched.
- **Tombstone retirement skips rows that are already superseded locally**, so the
  re-push cycle cannot restart on them.
- **Every analysis query excludes superseded rows.** Round 7 found that without this the
  duplicates remain full observations: `MatchStore.matches_for_fit()` (which feeds
  `load_matches()` and therefore the model) and `MatchStore.scored_predictions()` (which
  feeds every accuracy figure) both select on outcome and heroes alone. Both gain
  `AND superseded_by IS NULL`, as do the two audit scripts written today. This is part of
  the same change, not a follow-on: leaving it out would preserve the double-counting the
  design exists to remove.

Because `comps_key` is not unique, the six local duplicate pairs coexist without violating
anything - they are reconciled by the repair and by the server, not by the local migration.

### Ordering

The proximity merge and the occurrence scheme must ship **before or with** the key change,
never after. Until every client upgrades, an old client computes a stale key, fails to
recognise its own pooled row, and re-pushes; the proximity merge is what absorbs that. The
client's own key algorithm may lag safely, because the server's identity is authoritative
(`app/identity.py`, opening docstring).

### Testing

- Client and server produce identical `comps_key` for the same input (pinned digest).
- The two orientations of 1043/1046 produce one `comps_key`.
- Captures at 08:09:53 and 08:10:02 merge.
- Identical comps six hours apart produce two occurrences. The real pair ids 1/45, 31.6
  hours apart with opposite winners, is used as the fixture - it is not hypothetical.
- The t / t+90s / t+180s case produces the same result in all six arrival orders.
- Concurrent inserts of two genuinely distinct same-comps matches **whose captures are
  outside the window** yield two rows, not one row and a dropped match. Inside the window
  they are indistinguishable and merging is the specified behaviour - see "An accepted
  limitation".
- A capture that bridges two existing occurrences coalesces them, and the merge log
  records it.
- Two matches with identical six heroes in DIFFERENT events, captured seconds apart, get
  different `comps_key` values and never coalesce.
- The client backfill resolves the event slug through `theme_id` for the 120 rows whose
  `event_id` is NULL, and produces the same `comps_key` as it would from `event_id`.
- A row with neither relation resolvable keeps `comps_key` NULL, is skipped by the
  backfill, and is not pushable - it is never given a partial key.
- The slug `finalise_identity()` hashes is byte-identical to the `event_slug` the push
  payload carries. Asserted for the t / t+100s / t+150s case in all six arrival orders, which
  is the case a nearest-capture rule splits.
- Two captures of one match that DISAGREE about which trio won still produce one
  `comps_key`, and the conflict surfaces as an orientation verdict rather than a second row.
- After a merge, exactly one row in the group has `superseded_by IS NULL`, that row's key
  ends in `:<occurrence>`, and every superseded row still holds the key it held before.
- Post-migration, no active row holds a legacy-format key and no superseded row holds a
  canonical-format one.
- Cluster MEMBERSHIP after the migration is identical to that produced by replaying the
  same captures one at a time through ingest, in any arrival order. Occurrence numbers and
  therefore keys may differ between the two, and the test asserts membership only.
- A tombstone is delivered to a client whose match cursor is already past the superseded
  row, proving the separate supersession cursor works.
- A `local` row retired by a tombstone is re-pushed exactly once and then settles - the
  loop terminates.
- `adopt_canonical` deletes a clashing `synced` row but never a clashing `local` one; the
  local clash is marked superseded, its `natural_key` stays NULL, and its hero rows survive.
- A superseded local row IS returned by the push gate until it has been pushed, and is
  excluded only once `pushed_at` is set. (Round 8 caught this test still asserting the
  pre-reversal behaviour, which the algorithm now contradicts.)
- `finalise_identity` on a second observation of a match already recorded locally marks
  the new row superseded, widens the bounds, and leaves the first row's hero evidence and
  draft frame intact on both rows.
- A provisional row whose heroes never arrive keeps `comps_key` NULL and is never pushed.
- A local capture bridging TWO existing local occurrences supersedes both against the
  earliest, re-points their `superseded_by` chains one level deep, and still pushes.
- `matches_for_fit()` and `scored_predictions()` return one row per match after a local
  supersession, not two - asserted against a fixture containing a known duplicate pair.
- Captures at t, t+100s and t+200s produce ONE occurrence at ingest as well as in the
  migration, proving the bounds update survives the duplicate rollback.
- Migration against a frozen snapshot: assert the six known duplicate pairs merge, that
  `distinct comps_key occurrences + merges == completed rows`, and that every superseded
  row retains its old key. No absolute totals.
- A client with a stale key algorithm re-pushing an already-pooled match receives
  `duplicate` and adopts the canonical key.

## Risks

| risk | mitigation |
|---|---|
| The audit's draft reader is itself wrong, so it condemns correct rows | Pinned fixture frames per verdict; `partial`/`unreadable`/`incomplete` kept separate from `mirrored`; a mirrored rate near 50% is treated as a reader bug, not a finding |
| Repair corrupts good rows | Dry-run default, SQLite-native snapshot, round-trip test, explicit assertions that names/ratings/`predicted_left` are untouched |
| The migration merges two genuinely different matches | Merge log records everything; superseded, never deleted; single-linkage coalescing; derived-count assertions |
| The wrong row survives a merge | Survivor rule prefers frame-confirmed orientation; rule-3 fallbacks are flagged in the merge log for a later pass |
| Old clients re-push under stale keys | Proximity merge ships first and absorbs them |
| Live database drift invalidates the work | `--cutoff` on every script, a snapshot appropriate to each database (SQLite `VACUUM INTO`, Postgres `pg_dump` into a scratch database), no pinned totals in tests |

## What this does not do

It does not decide where side comes from in future. The audit measures the defect; the
capture-path fix is specced separately once the rate is known. It does not touch the model,
the betting rule, or the auto-bet settings.
