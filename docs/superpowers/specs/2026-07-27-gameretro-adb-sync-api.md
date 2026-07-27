# gameretro-adb-api - Match Data Sync Layer

**Status:** design, awaiting review
**Date:** 2026-07-27
**Repo (to create):** `webdevbar/gameretro-adb-api` (private)
**URL:** `https://gameretro.net/adb` (unlisted, `noindex`)
**Depends on:** the local schema v4 in this repo (`data/solstice_clash/schema.sql`)

---

## 1. Goal

Let several people run our AdbAutoPlayer fork, pool their collected match data into one
PostgreSQL database, and pull the pooled set back down to improve local accuracy.

More contributors is the entire point: the odds model needs a few hundred matches per theme
and one machine collects ~11/hour, so three collectors turn a two-day job into most of a day.

**Non-goals for v1:** a UI, user accounts, public access, real-time streaming, or serving the
odds model itself. This is a data pipe.

## 2. The problem that shapes the design

**Two collectors spectating the same live match will both submit it.** Solstice Clash matches
are public and anyone spectating sees the same result screen, so overlap is not an edge case -
it is the expected outcome of running two collectors during the same hours. Stored twice, that
match counts twice in the model, which is exactly the kind of silent corruption the odds spec's
gates exist to prevent.

So deduplication is not a nice-to-have; it is the reason this needs a design rather than a
`POST /rows`.

### `natural_key` exists but is never set

`match.natural_key` is declared `TEXT UNIQUE`, `record_match()` does
`ON CONFLICT(natural_key) DO NOTHING`, and `set_natural_key()` exists - but **nothing calls
it**. Verified 2026-07-27: 0 of 31 collected matches have one. The mechanism was built for
exactly this and left unwired.

**Wiring it is a prerequisite of this project, not part of it.** See section 8.

## 3. Identity: what makes a match the same match

The key must be computable from what BOTH collectors observe, and stable under OCR noise.

```
natural_key = sha256(
    theme_slug | outcome |
    sorted(left_hero_slugs) | sorted(right_hero_slugs) |
    hour_bucket(captured_at)
)
```

Reasoning per component:

- **Hero slugs, sorted per side** - the strongest signal. They come from image matching, which
  measured 0.81-0.95 with zero unidentified slots across 186 hero reads. Sorting removes any
  dependence on slot order.
- **Sides kept separate** (not one merged set) - left and right are meaningful and both
  collectors see the same orientation on the summary screen.
- **Outcome** - cheap, and two different matches with identical comps rarely split the same way.
- **Hour bucket, not exact timestamp** - two collectors will not agree to the second, and clocks
  drift. An hour bucket tolerates that.
- **Player names are deliberately EXCLUDED.** They are the least reliable field we have: reads
  of `GAME` and `【kru` are on record, and a name that differs between collectors would defeat
  the whole key. They are still stored, just not identifying.

**Known limitation, stated rather than hidden:** an hour bucket has edges. Two collectors
straddling :59 and :00 produce different keys for one match. Accepted for v1 because the cost is
one duplicate, not corruption, and the alternative - fuzzy time matching server-side - is real
complexity for a rare case. If duplicates show up in practice, the fix is a server-side
near-duplicate sweep, not a cleverer key.

## 4. Trust model

Contributors are friends, not adversaries - but "not adversarial" is not "always correct". A
contributor running a stale build, a mistuned template, or a modified client can submit wrong
data, and wrong data is worse than no data.

Contributor count is open-ended, so identity is **self-registering** rather than hand-issued.
That splits the job in two, because one credential cannot do both:

- **A shared fork key** (`X-API-Key`), baked into our build, answers *"is this our client?"* It
  gates the endpoint.
- **A per-install UUID** (`X-Instance-Id`), generated once on first run and persisted in the
  local database, answers *"which install is this?"* It carries attribution and revocation.

On first sight of an unknown instance id the server creates a `contributor` row automatically -
no manual issuance, which is what "unknown number of contributors" requires.

**Be honest about what the shared key is worth:** it ships inside a binary we hand to people, so
it is extractable by anyone willing to look. It raises the bar past drive-by traffic and nothing
more. The real protections are that the endpoint is unlisted, the data is low-value, and every
row is attributable and revocable. Do not let this key's existence create the impression the
endpoint is authenticated in a meaningful sense - if that is ever needed, it needs per-contributor
keys issued out of band.

- **Every row records `contributor_id`.** Non-negotiable: without it, bad data cannot be
  quarantined or removed after the fact, only guessed at. With a self-registering model this
  matters more, not less, because we no longer vet anyone up front.
- **Revocation is per install.** Setting `is_active = 0` on one contributor stops that install
  without touching anyone else or rotating the fork key.
- **Client identifies its build.** `X-Client-Version` and the local `schema_version`. The server
  rejects a schema it does not know rather than coercing it.
- **Server validates, does not trust.** Exactly 3 heroes a side, hero slugs must exist in the
  server's roster, `outcome` in the allowed set, `captured_at` parseable and not in the future.
  A row failing validation is rejected with a reason, not silently dropped.

## 5. API

Base `https://gameretro.net/adb`. All JSON. All authenticated except `/health`.

| method | path | purpose |
|---|---|---|
| GET | `/health` | liveness, no auth |
| GET | `/v1/reference` | events, themes with date windows, hero roster |
| POST | `/v1/matches` | bulk upsert, idempotent on `natural_key` |
| GET | `/v1/matches?since=<cursor>&limit=` | pull pooled matches newer than a cursor |

### POST /v1/matches

Batched - a client uploads a night's collection in one call, not 200 calls.

```json
{
  "client_version": "12.9.24-wdb1",
  "schema_version": 4,
  "matches": [{
    "natural_key": "sha256:...",
    "source": "spectate",
    "captured_at": "2026-07-27T01:43:05Z",
    "event_slug": "solstice-clash",
    "theme_slug": "converging-paths",
    "outcome": "left",
    "left_player": "KONTROL", "left_rating": 4202, "left_rank": 4,
    "right_player": "Elithes", "right_rating": 4288, "right_rank": 1,
    "heroes": [
      {"side":"left","slot":0,"hero_slug":"aliceth","stat_sword":1994000,
       "stat_heart":0,"stat_shield":5799000}
    ]
  }]
}
```

Response reports per-row outcomes so a client learns what was rejected:

```json
{"accepted": 27, "duplicates": 3, "rejected": [{"natural_key":"...","reason":"..."}]}
```

**Idempotent by construction.** Re-uploading the same batch is a no-op, so a client that
crashes mid-sync simply retries. `ON CONFLICT (natural_key) DO NOTHING`.

### GET /v1/matches

Cursor is `(created_at, id)`, not an offset - offsets skip or repeat rows when data is inserted
between pages, which it constantly will be here.

## 6. Storage

PostgreSQL, mirroring local schema v4 so the two stay comprehensible together:

- `event`, `theme` (with `starts_at` / `ends_at` windows) - the same shape as local
- `match` - plus `contributor_id`, `received_at`, `client_version`
- `match_hero`
- `contributor` - id, `instance_uuid` UNIQUE, label, `is_active`, `first_seen`, `last_seen`,
  `matches_submitted`, `last_client_version`

The contributor row doubles as the connection log: `last_seen`, `last_client_version` and
`matches_submitted` are updated on every authenticated request, so "who is contributing, from
what build, and when did they last check in" is answerable without a separate audit table.

**The shared fork key is stored hashed** in server config, never in plaintext, and never logged.
Instance UUIDs are not secrets - they are identifiers - so they are stored as-is.

The server resolves `theme_slug` to its own `theme.id`; it does not trust a client's local
theme ids, which are per-database autoincrements and mean nothing across machines.

## 7. Deployment

Dokploy on the existing host, autodeploying from the private repo. Two services in one compose,
following the documented `capped-v1` tier:

```yaml
services:
  api:
    build: .
    restart: unless-stopped
    expose: ["8000"]
    environment:
      DATABASE_URL: postgresql://adb:${POSTGRES_PASSWORD}@db:5432/adb
    deploy:
      resources:
        limits: { cpus: "0.30", memory: 384M }
  db:
    image: postgres:17-alpine
    restart: unless-stopped
    volumes: [ "/opt/www/gameretro-adb/pgdata:/var/lib/postgresql/data" ]
    deploy:
      resources:
        limits: { cpus: "0.40", memory: 512M }
```

**Stack:** Python + FastAPI. Chosen because the client is Python - the row shapes, validation
rules and hashing can be shared or mirrored without a second language's worth of drift.

**Routing** is the one genuinely fiddly part. `gameretro.net/` is already served by a static
nginx container; `/adb` must route to this service instead. Traefik `PathPrefix('/adb')` with a
higher priority than the static router's `/` rule, plus `StripPrefix` so the app sees `/v1/...`.
Both routers must be on the same Traefik network and the static site must not be disturbed.

**Unlisted:** `X-Robots-Tag: noindex, nofollow` on every response, `/adb/robots.txt` disallowing
everything, no links from anywhere, and no directory listing. This is obscurity, not security -
the API key is the security.

## 8. Prerequisites

**P1. Wire up `natural_key` in the client.** Nothing can dedupe without it (section 2). It must
be computed and stored at capture time, and backfilled for the 31 existing matches, which is
possible because every component is already recorded.

**P2. Decide what a client does with pulled data.** Local rows are the client's own
observations; pulled rows are other people's. They should be distinguishable - a
`origin` column (`local` / `synced`) - or a contributor cannot tell their own data from the
pool, and cannot recover by re-syncing after a local wipe.

## 9. Open questions for the user

1. **Contributor count.** Two or three friends, or wider? It changes nothing structurally, but
   it decides whether key issuance stays manual (a SQL insert) or needs tooling.
2. **Does the client pull automatically, or on request?** Auto-pull keeps everyone current;
   manual is more predictable and cannot surprise someone mid-collection.
3. **Retention/backup.** The host already has backup conventions; this database is small but it
   is the only copy of pooled data once contributors wipe local DBs.
