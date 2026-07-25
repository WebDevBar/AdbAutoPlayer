# Solstice Clash — Project Index

**Start here.** This is the entry point for the Solstice Clash work. If you have no
context, read this file top to bottom and you will know where everything is, what is
proven, what failed, and what is still open.

Last substantive update: 2026-07-25.

---

## 1. What we are building

Solstice Clash is an AFK Journey PvP event: 3v3 battles from a randomized hero pool,
which other players can spectate and place in-game **Guess Tokens** on. Three modes,
all inside AdbAutoPlayer:

| Mode | Name | What it does |
|---|---|---|
| A | watch-me-compete | Passively observes the user's own matches, records comps and outcomes |
| B | Solstice Spectate | Auto-loops spectating to gather match data at volume |
| C | odds calculator | Shows calculated odds from history; the **player** decides the stake |

**Hard constraints from the user — do not violate these:**

- **Never auto-bet in v1.** Mode C displays odds only; the slider stays under player
  control. Auto-placing on high confidence is a possible v2, only once accuracy is proven.
- **Show "inconclusive"** when confidence is low. That means "sit this round out", and is
  a first-class outcome, not a failure.
- **It must be fast.** The player has seconds to react before odds are hidden.
- **SQLite first, always.** Postgres may be added later for sharing data with friends via
  the user's static-IP server, but SQLite must always work standalone and sync opportunistically.
  Postgres being unreachable must never block local operation.

---

## 2. The single most important discovery: the wiki has an open API

**`https://afk-journey.fandom.com/api.php` is NOT Cloudflare-protected.**

The wiki's normal HTML pages *are* protected — `WebFetch` gets `HTTP 402`, and
agent-browser gets stuck on `"Just a moment..."`. It is easy to conclude the wiki is
unreachable and reach for a stealth browser. **Do not.** The API answers plain
`curl`/`urllib` with a normal User-Agent, no stealth tooling of any kind.

This is the highest-leverage fact in the project. Before building any extraction,
scraping, OCR, or capture machinery for AFK Journey data, **check the API first.**

### What the API has already given us, for free

- Every hero's faction, class, damage type, range, rarity, title, gender, race
- **732 skills** with cooldown, energy, range, short and full descriptions
- **173 skins** mapped to **122 heroes** (51 heroes have more than one skin)
- The **entire Solstice Clash event page**: usable roster with per-hero stat
  adjustments, banned list, themes, guessing rules, token rates, schedule
- **Datamined combat icons** for every hero and every skin (see §3)

### Useful API recipes

```bash
# page wikitext (best for infoboxes and templates)
curl -s "https://afk-journey.fandom.com/api.php?action=parse&page=Sonja&prop=wikitext&format=json&formatversion=2"

# everything in a category (paginate via the "continue" field)
curl -s "https://afk-journey.fandom.com/api.php?action=query&list=categorymembers&cmtitle=Category:Heroes&cmlimit=500&format=json&formatversion=2"

# bulk wikitext, up to 50 titles per call — far cheaper than one call per page
curl -s "https://afk-journey.fandom.com/api.php?action=query&titles=Sonja|Lucca&prop=revisions&rvprop=content&rvslots=main&format=json&formatversion=2"

# resolve a File: page to a real image URL
curl -s "https://afk-journey.fandom.com/api.php?action=query&titles=File:Hero%20Sonja.png&prop=imageinfo&iiprop=url|size&format=json&formatversion=2"

# find files for a hero when you don't know the naming
curl -s "https://afk-journey.fandom.com/api.php?action=query&list=search&srsearch=Sonja&srnamespace=6&srlimit=40&format=json&formatversion=2"
```

**Gotcha:** `prop=extracts` (TextExtracts) is **not installed** on this wiki — it silently
returns empty strings. Use `prop=wikitext` or `prop=revisions` and parse the templates.
Brace-balanced parsing is required because templates nest; `build_hero_db.py` has a
working implementation (`templates()` / `fields()`).

---

## 3. Hero identification — SOLVED

### The finding

The wiki's `File:Hero <Name>.png` files are the **datamined in-game combat icons**
(180x248 with alpha). The in-game 96px circular roster icon is a fixed crop of that art:

```
scale  = 0.68          # applied to the 180x248 art, ASPECT PRESERVED
offset = (13, 35)      # crop origin, in the scaled image
mask   = circle(centre=(48,48), radius=44)   # ignore the plate corners
```

Skins have their own icon under `File:Hero <SkinName>.png`, so skin templates come from
the wiki too — no in-game capture needed for them either.

### Verification (measured, not assumed)

- **20/20** correct on hand-labelled Lightbearer roster icons, mean margin **+0.30**
- **8/8** correct when the candidate set is widened to **124 heroes**
- Faction constraint removes cross-faction errors (see §6)

### The mistake that nearly killed this idea

The first two attempts scored **5/20** and **0/20**, and were wrongly reported as
"the approach fails". The bug was resizing the 180x248 art into a **square**
`(96*s, 96*s)`, destroying the aspect ratio. The asset was correct the whole time.
The user pushed back and insisted it should work — they were right.

**Lesson worth keeping:** if wiki assets are datamined game files, a correct alignment
should score ~0.95+. A mediocre score means *your transform is wrong*, not that the
content differs.

### Known weak cases (need per-hero tuning)

| Hero | Score | Margin | Note |
|---|---|---|---|
| Sinbad | 0.510 | +0.039 | best local scale 0.61, not 0.68 |
| Hugin | 0.462 | +0.071 | |
| Temesia | 0.412 | +0.111 | best local scale 0.63, not 0.68 |
| Marilee | 0.458 | +0.243 | correct but low absolute — possibly a re-rendered asset |
| Atalanta | 0.446 | +0.152 | |

Per-hero scale/offset overrides are the intended fix. Store them alongside the hero
rather than tuning the global constant.

---

## 4. Where everything lives

### Committed (this repo — the durable copy)

| Path | Contents |
|---|---|
| `data/solstice_clash/heroes.sqlite` | The database (see §5) |
| `data/solstice_clash/build_hero_db.py` | Regenerates everything here. **Verified reproducible.** |
| `data/solstice_clash/hero_db_full.json` | Parsed hero records incl. skills |
| `data/solstice_clash/wiki_skins.json` | skin → hero, rarity, type |
| `data/solstice_clash/wiki_skins_by_hero.json` | hero → [skins] |
| `data/solstice_clash/solstice_roster.json` | Event usable/banned + stat adjustments |
| `data/solstice_clash/solstice_clash_wiki.txt` | Raw wikitext snapshot of the event page |
| `docs/solstice-clash/findings-log.md` | Running findings log, chronological |
| `docs/superpowers/specs/2026-07-25-solstice-clash-phase1-design.md` | Design spec (Codex-reviewed, 5 rounds) |
| `docs/superpowers/plans/2026-07-25-solstice-clash-phase1.md` | Implementation plan (Codex-reviewed, 3 rounds) |
| `.../templates/event/solstice_clash/anchors/ban_glyph_{red,blue}.png` | Ban-overlay glyph templates |

Refresh after a balance patch or event rotation:

```bash
cd data/solstice_clash && python3 build_hero_db.py
```

### Scratch (`/tmp/solstice`) — **VOLATILE, will not survive a reboot**

~4.5 GB of captures that are *not* in the repo and *cannot* be re-derived from the wiki:

| Path | Contents |
|---|---|
| `frames/` | 1110 spectate frames |
| `compete/` | 967 frames of the user competing |
| `labels/` | 133 frames of the stuck match with long-press popups open |
| `shots/` | On-demand roster screenshots (the good ones, taken at rest) |
| `rows/lightbearer/` | 20 confirmed, ordered Lightbearer roster icons |
| `seed_lib/` | 18 hand-labelled draft-card templates + manifest |

**These are the ground truth that validated everything else.** The user has a 4 TB disk at
`/mnt/vault` (note: `/mnt/vault`, not `/vault`). Moving the captures there is an
outstanding task — ask before doing it.

---

## 5. Database schema (`heroes.sqlite`, ~430 KB)

| Table | Rows | Purpose |
|---|---|---|
| `hero` | 153 | slug (PK), name, faction, class, damage type, range, rarity, title, gender, race, combat_icon |
| `hero_skill` | 732 | hero_slug, type, name, range, cooldown, energy, summary, detail |
| `hero_skin` | 173 | hero_slug, skin_name, combat_icon |
| `solstice_roster` | 118 | 95 usable + 24 banned; faction, status, adjustment, hp_pct, atk_pct |

**Identity rule:** `slug` is the immutable primary key. Positional numbering
(`roster_index`, "#1–#20") is a **throwaway labelling aid only** and must never reach the
database — the roster changes between events and every stored row would rot.

If a datamined universal hero ID is ever found, add it as an optional `external_id`
column. Never make it the primary key.

**Match-data tables** (`match`, `match_hero`, `match_odds`) are specified in the design
doc but **not built yet** — they are Phase 1 implementation work.

---

## 6. Event facts (from the wiki event page)

- Event window: **2026-07-20 → 2026-08-03**
- **20 heroes are randomly drawn per match** from the pool for the **current theme** —
  this is why a single match only ever shows 20 of the 95 usable heroes
- **5 rotating themes**, each with a battlefield effect: Tranquil Grounds, Fierce Duel,
  Converging Paths, Flourishing Wilds, Tactical Grounds
- Each side **bans 1** hero and picks **3 unique** heroes in a set sequence
- All heroes are locked to the **same progression level** — no account-power confound,
  which is what makes this data clean and worth modelling
- **Odds are hidden once both sides confirm lineups.** This is a hard timing constraint
  on Mode C.
- Guess Tokens accrue hourly by collection progress (100–400/h)

**Usable roster: 95 heroes** — Lightbearer 20, Mauler 20, Wilder 23, Graveborn 19,
Hypogean 5, Celestial 5, Dimensional 3. Independently corroborated: the user's own
tracking spreadsheet contains 95 hero names.

**75 of the 95 carry stat adjustments** (e.g. `Cassadee ATK +50%`, `Hugin HP -40% ATK -40%`),
parsed into integer `hp_pct` / `atk_pct` columns. **These adjustments are the balance patch**
— they are what the spec's `balance_epoch` field exists to track. When the wiki's Change
History changes them, that is a new epoch and older match data becomes less predictive.

**User's skin rule, important for template selection:**
the 5x4 selection grid shows heroes **unskinned**; a **locked pick renders the owner's skin**.
So: draft grid → base art; locked pick → base *or* skin art.

---

## 7. Screen geometry (measured on 1080x1920)

AdbAutoPlayer forces this resolution (`games/afk_journey/base.py:57` sets
`base_resolution = 1080x1920`; `game/_screenshot_mixin.py:110-111` calls
`device.set_display_size()`), so these coordinates are portable across devices.

```python
# Draft grid, 5 columns x 4 rows of hero cards
ROWS = [665, 900, 1135, 1370]
COLS = [155, 315, 475, 635, 795]
CARD = (150, 190)                  # w, h
PORTRAIT = slice(45, 165), slice(20, 130)   # within a card

# "Usable Heroes" roster screen — circular icons
ROW_Y  = [997, 1128, 1258, 1388, 1518, 1648]   # one row per faction
ICON_R = 48
```

Faction row order on the roster screen: Lightbearer, Mauler, Wilder, Graveborn,
Hypogean, Celestial, Dimensional.

**Spectate screens do NOT share the compete grid layout** — best anchor match was 0.763.
Mode B needs its own geometry; the plan currently assumes shared, and that is wrong.

---

## 8. Things we tried that FAILED — do not repeat these

| Attempt | Result | Why |
|---|---|---|
| Reusing the general hero-roster templates for Solstice | max 0.63 | Different rendering. Cut fresh templates from Solstice screens. |
| `matchTemplate` on same-size images | 0.770 | Same-size input gives a 1x1 result — **no alignment search**. Pad the base so the template can slide. |
| Screen classification by brightness heuristics | 1/5 | Use template anchors instead. |
| Ban detection by colour cast (`red - green > 28`) | false-positived 10/20 cards | Red-haired heroes trip it. Use the glyph templates. |
| Wiki icons resized to a **square** `(96*s, 96*s)` | 5/20, then 0/20 | **Aspect-ratio bug.** Preserve 180:248. This one nearly killed a correct idea. |
| `File:<Name>.png` (infobox art) as the icon | 0/20 | That is full splash art, up to 4565x1840. The combat icon is `File:Hero <Name>.png`. |
| `prop=extracts` for page intros | empty strings | TextExtracts is not installed. Parse wikitext. |
| `WebFetch` on wiki HTML | HTTP 402 | Cloudflare. Use `api.php`. |
| agent-browser on wiki HTML | "Just a moment..." | Cloudflare. Use `api.php`. |
| Continuous-scroll panorama stitching | 15, then 11, then 7 (truth: 20) | Motion blur + brittle alignment. **Take discrete screenshots at rest with a guaranteed 1-hero overlap.** That gave a clean 20/20. |
| `pkill -f grab.py` | killed its own shell (exit 144) | The pattern matched the invoking shell. Use `pkill -f "python.*gr[a]b\.py"`. |

Also: `adb exec-out screencap -p` on this Waydroid device prefixes **57 bytes** of
`/vendor/etc/hwdata/amdgpu.ids: No such file or directory` before the PNG. Seek to the
PNG magic `\x89PNG\r\n\x1a\n` before decoding.

---

## 9. Open items

**Ready to do:**
1. **Uniqueness constraint** on row identification — a row cannot contain the same hero
   twice, but independent argmax produced `Harak` twice in the Hypogean row. A one-to-one
   assignment (Hungarian) over the score matrix should fix that and likely also rescue the
   weak `Lyca 0.33` / `Bonnie 0.23` cases. **scipy is not installed** in `/tmp/solstice/.venv`.
2. **Locked-pick verification** — identification is proven for the *roster icon* only.
   The locked pick is the variant that actually matters at runtime, and it is where skins
   appear. Test frames already exist in `compete/`.
3. **Move `/tmp/solstice` captures to `/mnt/vault`** before they are lost. Ask first.
4. **Third ban-glyph variant** — the committed red/blue pair missed Tilaya's ban overlay
   (scored 0.279/0.241, below the 0.60 threshold), so it leaked into the library as a
   phantom hero. Cut a third glyph.

**Plan corrections needed before execution:**
- Task 6's library builder auto-numbers slugs `hero_001…NNN`. Obsolete — real names now
  come from the wiki.
- "2 images per variant" is wrong: **51 heroes have multiple skins**, so it is N templates
  per hero.
- The plan assumes spectate and compete share geometry. They do not (§7).
- Codex plan-review **round 4 was never completed** (interrupted twice). Rounds 1–3 are done.

**Unresolved questions:**
- **Odds formula.** Observed blue 2.93 exceeds the zero-rake parimutuel cap of 2.80, so
  the house subsidises. Rake measured at exactly 10%. The subsidy is uncharacterised.
- Spectate shows 15 cards where compete shows 20 — pick slots are the primary signal, so
  this does not block, but it is not understood.

---

## 10. Working agreements

- **Discrete screenshots beat continuous capture** for anything scrolled. Ask the user to
  reposition with a guaranteed overlap, take one shot, confirm, repeat. Three shots gave a
  perfect 20-hero row after three failed stitching algorithms.
- **Show the user the image before claiming a result.** Two contact sheets were shipped
  that were mostly junk (popup fragments, ban glyphs) because they were not looked at first.
- **Verify against the code, not memory.** `base_resolution` and the `grayscale` matcher
  parameter were both confirmed by reading the source before relying on them.
- The user corrects errors quickly and is usually right when they push back. On both the
  roster count and the wiki-icon idea, they were right and the initial analysis was wrong.

---

## 11. Cell registry and per-art transforms (2026-07-25, session 2)

### Verified cells (all at 1080x1920, stored in `cell_registry`)

| cell_type | size | aspect | count | screen |
|---|---|---|---|---|
| `locked_pick` | 100x85 | 1.18 | 6 | pre-match locked teams |
| `draft_locked_pick` | 100x74 | 1.35 | 6 (Red 6 always empty) | draft |
| `draft_card` | 110x120 | 0.92 | 20 | draft 5x4 grid |

`locked_pick` x-origins: 62, 206, 349 (blue) / 635, 779, 922 (red), y 1495-1580.
Verified stable across 6 different matches - 36 crops, zero drift.

`draft_card` lattice is exact: column pitch 160, row pitch 235, every column sharing one
(x0,x1) and every row one (y0,y1). Verified from the DB, not assumed.

**Three different aspect ratios means three different transforms.** Do not reuse one recipe
across cell types.

### Matching recipe

Do NOT fix the crop offset. Fix the scale, pad the cell, and let `matchTemplate` find the
offset - the alignment search is free and the correct offset varies per hero. Fixing the
offset dropped Temesia from 0.978 to 0.408.

Scale fallback chain, accept at >= 0.90: **1.01, then 0.95, then 1.08**. Measured cost
0.37 ms per candidate, ~0.09 s for 252 candidates per cell.

`draft_card` scale clusters tightly at **1.19**.

### Accept rule (measured on 54 cells from 9 matches)

    accept if score >= 0.70 AND margin >= 0.10   -> 47/54 accepted, zero bad ones let through

Score alone is not enough. Every visually-wrong match had a *collapsed margin* (0.01-0.04)
while plenty of correct ones sat at 0.70-0.80. Keep both conditions.

### Two-tier candidate strategy (user's design)

1. Capture the 5x4 grid in the first ~3 s of draft, before any ban or pick - all 20 cells
   unbanned and unskinned. Trigger: selection anchor present AND zero ban glyphs.
2. Match locked picks against **that match's identified pool** (<= 20 candidates) first.
3. Fall back to the full library only if tier 1 fails.

This constrains 124 candidates down to 20, and self-validates: a pick that is not in the
pool is a detected error rather than a silent wrong answer.

### CRITICAL: never filter candidates by roster status

Filtering to the wiki's "usable" list caused 100% failure on two cells. Both were **Zorya**,
who matched at 0.918/0.917 (margin +0.52) once included. Match against all heroes; keep
`usable`/`banned` as metadata only.

### Skin rendering rule (corrected by measurement)

The 5x4 grid shows heroes **unskinned until selected**. Once a hero is picked, that grid cell
re-renders with the owner's skin. Confirmed: on a frame with 5 locked picks, exactly the
selected heroes scored low against base art - Rowan 0.654 -> "Son of Mithril" 0.965, Lily May
0.609 -> "Silent Guillotine" 0.977. So a calibration frame with picks already made is TAINTED.

### Data-quality traps found

- **Never assume icon dimensions.** There are 8 distinct base-icon sizes. Hardcoding 180x248
  squashed `Sword of Misarte` (184x248) and cost ~0.15 of match score. `icon_w`/`icon_h` are
  now stored per image.
- **94 of 128 "skin" icons are byte-identical to the base icon** - the wiki uses
  `File:Hero <SkinName>.png` as a placeholder. Only 34 are genuinely distinct art.
- **A hero's *title* can be parsed as a skin.** `Symmetric Sin` is Reinier's title, not a skin.
- **Zorya appears in BOTH the usable and banned lists.** `INSERT OR REPLACE` on a UNIQUE(name)
  key let the banned row clobber the usable one. Usable must win.

### Assets that will not reach 0.90 (genuine wiki drift)

Reinier (~0.63) and Eironn (~0.79) match poorly at every scale and offset. Verified by cutting
the crop by hand and by exhaustive search - the region was never the problem. Their in-game
render differs compositionally from the wiki art. Both still identify **correctly**, because
base and skin art map to the same `hero_slug`.

## 12. Datamining the installed game - PARTIALLY SOLVED, ASSETS ARE BAKED IN

**Correction:** an earlier version of this doc called this a dead end. That was wrong, three
times over. The hero art IS shipped locally. The mistake each time was concluding from a
truncated listing - most damningly, running `find` that reported **10,935 images** and then
declaring "no hero icons" after reading only the first 40 lines (all of which happened to be
`iconCache`).

### WHERE THE HERO ART ACTUALLY IS

    ~/.local/share/waydroid/data/data/com.farlightgames.igame.gp/files/data/ui/icon/

Reachable from the HOST (Waydroid's Android fs is on the host filesystem); needs sudo. 430MB.

| Directory | Files | Contents |
|---|---|---|
| `ui/icon/hero` | 593 | `spui_herohead_<ID>.png` - **hero head icons keyed by numeric ID** |
| `ui/icon/heroskin` | 153 | skin variants |
| `ui/icon/heroult` | 208 | ultimate icons |
| `ui/icon/duelicon` | 169 | duel-mode icons (Solstice Clash is a duel mode) |
| `ui/icon/avatar` | 1726 | player avatars |
| `ui/icon/item` | 1434 | items |

Copied to `/mnt/vault/solstice/gamefiles/ui/`.

**The numeric ID in `spui_herohead_<ID>.png` is the game's own hero ID** - the universal
identifier we wanted for the `external_id` column.

### BLOCKER: the .png files are not PNGs

They are a Lilith-custom container. Magic is `41 53 54 b4` (`"AST"` + 0xb4), NOT standard
ASTC (`13 AB A1 5C`). Measured on `spui_herohead_1000.png` (15302 bytes):

- no (header offset, block size, dimensions) combination yields a size-exact raw ASTC payload
- no zlib stream in the first 64 bytes
- body entropy 7.49 bits/byte -> compressed or block-packed

Decoding needs reverse engineering of that header. This is now a **bounded, well-defined
target**: one container format, one file to crack, 593 hero icons behind it.

### Also unmapped: hero ID -> name

`assets/Config/AutoGenConfigBytes/LanguageDbEN.bytes` (23MB, in `split_BinaryAssets.apk`)
contains every hero name in plain text, but the key/value structure needs parsing to link
IDs to names. Note we would not strictly need it: with confirmed cell labels we can identify
a decoded icon by matching it against cells we have already named, and the ID follows.

### What these sources are NOT (checked, do not re-check)

| Source | Contents |
|---|---|
| `split_UnityDataAssetPack.apk` (718MB) | 112 bundles, 18 textures, 1 hero (`thornknight`) |
| `files/data/*.lpak` (196 bundles, 69MB) | terrain virtual-texture tiles (`VT` = Virtual Texture) |
| `files/iconCache/ASTC` (844 files) | **player avatars** - decodes fine, wrong content |
| `files/Share/` | gacha pull share-screenshots (filenames are timestamps, NOT hero IDs) |
| `LpakCatalog.Android.bin` (292k strings) | world/NodeCanvas assets; no `spui_`, no `ui/` |
| `igame.pkg` (134MB) | custom format, magic `ffffffff 74050600`, undecoded |

### Technique notes

- `.lpak` files are standard `UnityFS` bundles with the version string stripped. UnityPy reads
  them with `UnityPy.config.FALLBACK_UNITY_VERSION = "2021.3.30f1"`.
- Standard ASTC decodes via `texture2ddecoder.decode_astc(data[16:], w, h, bx, by)`; header is
  magic `0x5CA1AB13`, block dims at bytes 4-6, then 24-bit LE x/y/z. Unity textures come out
  **vertically flipped** (origin is bottom-left).
- `adb root` is NOT supported in Waydroid and knocks the connection offline; `adb connect` to
  recover.
- A shell glob over a root-owned directory silently matches nothing - the glob expands
  unprivileged. Wrap it: `sudo sh -c '...'`.
- No CDN involved: a 60s DNS capture on `waydroid0` during icon loading produced nothing.

## 13. Storage

**Anything large goes on `/mnt/vault` (3.7T), never `/tmp`.** `/tmp` is a 16G tmpfs and a
recursive copy of the game data dir (13G) filled it completely, breaking tool output.

`/mnt/vault/solstice/` holds the captures (`frames`, `compete`, `labels`), `apk`, `lpak`, and
`gamefiles`; `/tmp/solstice` symlinks to them so existing paths still work.
