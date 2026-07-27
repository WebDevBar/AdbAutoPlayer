# Client ↔ API Communication

**Status:** APPROVED - 2 review rounds, final NO ISSUES FOUND
**Date:** 2026-07-27
**Companion to:** `2026-07-27-gameretro-adb-sync-api.md` here, which defines the server
(the same document is `2026-07-27-sync-api-design.md` in the `gameretro-adb-api` repo). This document
defines how the AdbAutoPlayer client actually talks to it.

---

## 1. Why this is a separate document

The sync design specified the API and the storage on both sides, and the implementation plan
built `SyncClient.push()` / `pull()`. But nothing said where the endpoint URL comes from, where
the fork key lives, when sync runs, or what happens when the server is unreachable while a
collection run is in progress.

That gap is the difference between a library and a working feature.

## 2. The governing rule

**Sync must never cost a match.**

Collection is the valuable thing; sync is an optimisation on top of it. A dead endpoint, a
timeout, an expired certificate, a rate limit, a 500 - none of them are a reason to stop
gathering data, and none of them may propagate into the collection loop.

Every consequence in this document follows from that rule.

## 3. Configuration

| setting | env override | default | notes |
|---|---|---|---|
| base URL | `ADB_SYNC_URL` | `https://gameretro.net/adb` | trailing slash stripped |
| fork API key | `ADB_SYNC_KEY` | build-time constant | never logged |
| enabled | `ADB_SYNC_ENABLED` | `true` | `false` disables all network calls |
| timeout | `ADB_SYNC_TIMEOUT` | `15` seconds | per request |

The env overrides let a contributor point at a staging instance, or turn sync off entirely,
without a rebuild - which matters because rebuilding is not something a friend running the tool
can do.

**A non-default URL requires an explicit key.** If `ADB_SYNC_URL` is set to anything other than
the default and `ADB_SYNC_KEY` is not also set, sync is DISABLED and logs `[SC-36]`. Otherwise a
single mistyped or malicious env var ships the real fork key, the install UUID, and everyone's
match data to an arbitrary host - the key is baked in, so the client would happily authenticate
to the wrong server. Pairing the two removes that entirely.

**The instance UUID is not configurable.** It is read from `install.instance_uuid` and generated
on first migrate. Making it settable would let two installs claim the same identity, which
breaks attribution and revocation.

**The fork key is a build-time constant and the spec is explicit that this is not real
authentication.** It ships inside a binary handed to people. Do not add machinery that implies
otherwise.

## 4. When sync runs

| trigger | action | rationale |
|---|---|---|
| collection mode starts | pull | seed the local pool before collecting |
| after a successful push | pull | pick up what other contributors added while we were collecting |
| end of each collection cycle | push the backlog | keeps the pool current without a separate scheduler |
| manual | push then pull | for a contributor who wants to sync without collecting |

The post-push pull matches the server spec's "autopull, on mode start and after each successful
push". A pull is cheap when nothing is new - it is one request returning an empty page - and it
is what keeps several collectors running simultaneously from drifting apart for a whole session.

### Where exactly the push hook goes

Not simply "after `_run_one_match()` returns". That call can durably record a match and *then*
fail during back-navigation - the `[SC-21] recorded, but the cycle ended badly` path - so
hooking the normal return would skip pushing exactly the matches most worth pushing.

The hook goes **after the try/except accounting block and after the protected recovery block**,
at the bottom of the `_collect_forever` loop body, guarded by "a match was recorded this cycle,
or a backlog exists". That way every path through the cycle - clean, recorded-then-failed, and
outright failed with an older backlog - reaches the same place.

### Pull re-requests a small overlap

`pull` sends `since = max(0, pull_cursor - PULL_OVERLAP)` with `PULL_OVERLAP = 50`, not the bare
cursor.

A sequence cursor is better than a timestamp, but it does not fully solve exactly-once delivery:
`seq` is assigned at INSERT, before COMMIT, so a transaction holding `seq=100` can become visible
*after* a client has already read `seq=101` and advanced its cursor. That row would then be
skipped forever - silently, which is the expensive kind.

Re-requesting an overlap closes it, and costs nothing: pull upserts by `natural_key`, so a row
seen twice is a no-op. Discovered while implementing the server schema, where the same property
is documented on `Match.seq`.

### Chunking: the backlog can exceed one batch

`pushable_matches()` returns every unpushed local keyed row, and the server rejects a batch over
500 with a 422. A client returning from two offline nights would therefore submit 600 rows, get
422, mark nothing, and **submit the same oversized request forever** - a livelock that gets
worse the longer it lasts.

So the client chunks: send at most `BATCH_LIMIT = 500` per request, adopt and mark that chunk,
then send the next. Up to 3 chunks per cycle, so catching up is bounded but a large backlog does
not monopolise a collection cycle. The remainder goes out next cycle.

**Push happens after the match is durably recorded**, never before. A sync failure must not be
able to lose a row that was already collected.

## 5. Failure handling

Every sync call is wrapped. Nothing it can do reaches the collection loop.

| failure | client behaviour |
|---|---|
| connection refused / DNS / timeout | log `[SC-30]`, continue; rows stay unpushed |
| 401 / 403 | log `[SC-31]`; disable sync for this run **after 3 consecutive** auth failures |
| 429 | log `[SC-32]`, skip this cycle, try next |
| 5xx | log `[SC-33]`, continue; rows stay unpushed |
| row rejected (per-row `status: rejected`) | record the reason, mark it non-retryable, continue |
| malformed response | log `[SC-34]`, treat as a failed push - do NOT mark anything pushed |

Two distinctions worth being explicit about, because getting them wrong is silently expensive:

- **A rejected row is not a failed push.** The request succeeded; the server refused that row.
  Retrying forever accomplishes nothing, so it is marked and counted.
- **A failed push is not a rejection.** The rows are still good. They must stay `pushed_at IS
  NULL` and go out next cycle. Marking them pushed on a timeout would silently drop them from
  the pool forever.

**Auth failures need a threshold, not a hair trigger.** A revoked instance or a wrong key will
never fix itself by retrying, so giving up is right - but disabling on the *first* 401 means a
proxy hiccup or a server restart mid-deploy silently kills sync for a twelve-hour overnight run,
and the contributor finds out the next morning. Three consecutive failures distinguishes "this
credential is dead" from "the network coughed". The counter resets on any successful request.

**No retry loop inside a cycle.** The next cycle is the retry, and it arrives in a few minutes.
An in-cycle retry only turns a slow endpoint into a slow collection loop, which violates section
2.

## 6. What the client sends and adopts

Per the sync spec: the client sends match facts and `theme_ocr`, never `natural_key` and never a
theme slug. It adopts the canonical identity from the response - `natural_key`, `theme_id`
resolved from the canonical `theme_slug`, and `theme_resolved_by` - in one transaction, before
setting `pushed_at`.

Adoption handles the collision case (a previously pulled copy already holds the canonical key)
by keeping the local row, promoting it, and deleting the synced duplicate.

## 7. Privacy

A contributor is sending gameplay data to a server. Stated precisely, what leaves the machine is:

- match facts - capture time, event, the OCR-read theme name, outcome
- both sides' hero picks and their three per-hero stat values (`stat_sword`, `stat_heart`,
  `stat_shield`)
- **other players' in-game names, ratings and ranks.** This is spectate collection, so these are
  the two people whose match was watched - not the contributor. They are public in-game display
  names shown on a screen any spectator sees, but they are other people's, and a spec that says
  "player names" without saying whose is not being straight about it.
- the install UUID

**Not sent:** screenshots, frame paths, crop paths, match scores, template geometry, or anything
under `identification_audit` / `hero_screen_transform`. Those are machine-specific evidence and
the sync spec already excludes them - worth restating here because a contributor deserves a
plain answer to "what does this upload?"

`ADB_SYNC_ENABLED=false` is the complete opt-out.

## 8. Observability

The contributor should be able to tell what sync is doing without reading a database:

```
[SC-35] sync: pushed 27, duplicate 3, rejected 0; pulled 41 new
```

One line per sync, in the existing GUI log. Plus a one-time line at mode start reporting the
resolved base URL and whether sync is enabled - so "is it even talking to the server?" is
answerable at a glance rather than by inference.

## 9. Open questions

1. Should a contributor be able to see their own totals (matches contributed, last sync)? Useful,
   but it is a new endpoint and v1 has none.
2. Should pull be bounded on first run? A new install pulling a full theme's pool is one request
   of a few hundred rows today, but that grows with contributors.
