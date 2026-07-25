# Solstice Clash - findings log (working notes)

## ⚠️ RISK: hero skins change portrait art
Players can apply **custom skins**, so a hero's card art is NOT guaranteed to match the
default portrait template. This directly threatens the hero-identification pipeline that
everything else depends on.

Mitigations to design in:
1. **Multiple templates per hero** (default + each known skin), matched as a set - best score wins.
2. **Confidence floor + flag.** If no template clears threshold, do NOT guess - mark the match
   `hero_uncertain` and exclude it from training rather than poisoning the dataset.
3. **Self-improving template library (the good one):** the *replay screen shows hero NAMES as
   text* for one side. When OCR gives a name but the portrait matched nothing, we have
   (unknown portrait + known hero name) = a labelled new skin template. Auto-harvest it.
   The farm loop therefore grows its own skin coverage over time.
4. Cut templates from **Solstice screens specifically**, not the roster screen - framing,
   borders and badges differ.

## Navigation (verified by automated run, 1080x1920)
outworld -> events icon (1010,1160) -> Battle Modes
        -> Solstice Clash (195,1130)
        -> Fortune Picks (130,1590)     [⚠ Join at (640,1610) - never tap]
        -> Spectate Live (540,1400)
        -> live match (joins at ANY draft stage)
        -> ... -> result -> Back (755,1815) -> outworld
History path: event page -> History (985,455) -> tabs: Records | My Bets | Custom Match

## Result screen variants (5)
| Variant | Banner | "You Lost" | Rewards | Spectators/Prize Pool |
|---|---|---|---|---|
| win (rewards) | ✅ | ❌ | 3 cards | ✅ |
| win (post-cap) | ✅ | ❌ | fewer/none | ✅ |
| loss | ✅ | ✅ | 1 card | ✅ |
| **no bet** | ✅ | ❌ | ❌ | ❌ **absent** |
| timeout | ❌ no screen at all | - | - | - |

**Only universal anchor = banner text.** Parse `(BLUE|RED)\s+(WINS|LOSES)`:
`X WINS` -> X won; `X LOSES` -> other side won. Unambiguous, independent of which side we backed.

## Odds are NOT pure parimutuel
| pools | odds shown | 0.9*T/pool predicts |
|---|---|---|
| 100 / 100 | 1.80 / 1.80 | 1.80 / 1.80 ✅ |
| 143893 / 122630 | 1.68 / 1.94 | 1.67 / 1.96 ✅ |
| 10628 / 19077 | **2.93** / 1.43 | 2.52 / 1.40 ❌ |
| 72547 / 140737 | 2.55 / 1.41 | ? |

Blue at 2.93 exceeds even a **zero-rake** parimutuel (2.80) - the visible pools cannot fund it.
=> there is a **house-funded subsidy** (consistent with `Prize Pool` >> spectator pools).
Implication: effective rake < 10%, possibly negative on the underdog side -> wider +EV window.
**Needs proper derivation from farmed data before any EV claims.**

## Draft mechanics
- Each side bans 1; pick order 1-2-2-1: Blue1, Red2, Red3, Blue4, Blue5, Red6
- Pool = random subset (~15 shown) of the usable roster
- Picked cards get number badge + lock; banned get circle-slash; border colour = team
- **Late-draft (after pick 5) is the last moment with near-complete info AND visible odds**

## Model-relevant statics to snapshot per session
Usable Heroes (~44 in grid, adjustments list longer - reconcile), Banned Heroes,
Hero Adjustments (ATK/HP/DEF/SPD % per hero - large spread, list appears sorted by power),
current Theme + rotation timer.

## Themes (4, rotate ~8h) - materially change hero value
- Fierce Duel: standard, highly complex terrain
- Converging Paths: standard, special terrain
- Flourishing Wilds: **HP +100%**, -15 Vitality at 30s then every 10s (stacks), standard terrain
- Tactical Grounds: same buffs, special terrain
=> ratings must be theme-aware; hierarchical model with shrinkage (data splits 4 ways)

## Compete-side screens (mode A) - differ from spectate
Flow: event page -> Join -> [Ranked | Custom] -> matchmaking (1-10s, variable)
      -> BANNING (4x5 grid = 20 heroes, 5s, "Select a Hero to Ban")
      -> drafting -> HERO PLACEMENT on map -> battle -> result -> **Play Again** (no overworld)

Result screen (compete): banner = **VICTORY / DEFEAT** (not colour+verb),
1 reward card, **Rank Progress: points + delta e.g. 4053 (+58)**, District Ranking, `Play Again`.
-> per-match rating delta is a much stronger player-strength signal than binary W/L.

## Hero card geometry (BANNING screen, 1080x1920) - VALIDATED
ROWS=[665,900,1135,1370]  COLS=[155,315,475,635,795]  card 150x190
portrait sub-region [45:165, 20:130]; frames identical (levels locked) -> clean matching.
Search-based matching (template searched in padded card): **median 1.000**, 92.6% >= 0.90.
Roster templates from AdbAutoPlayer: UNUSABLE (max 0.63 even with optimal crop sweep).

## MODEL: predict THREE outcomes, not two
Timeouts are compositional, not random: two high-sustain kits (e.g. Zorya + Tasi, both with
heals + invulnerability windows) can neither close -> clock expires -> draw/timeout.
=> multinomial target {blue win, red win, timeout}; high timeout probability is itself a
   "sit this one out" signal, and an explainable one.

## Known skinned heroes (confirmed by user, 2026-07-25)
- **Talene** - blue-hair skin (default is red-haired). Captured: keep_skin2_*.png / skin_talene.png.
  Verified: still matched its own grid card at rank 1 because matching is grayscale (recolour).
- **Igor** - skin confirmed by user, frame not captured (transient).
- **Ulmus** - appeared skinned in a battle-screen capture (skin2.png).
Implication: recolour skins are handled free by grayscale matching. Only silhouette-changing skins
need their own library entry, which the unknown-path handles automatically.
- **Lily May** - skin confirmed by user 2026-07-25 (4th known skin).

## Long-press popup = complete labelling oracle (2026-07-25)
Long-pressing a pool card opens a detail panel containing: hero NAME, power (e.g. 506K),
FACTION (Hypogean), CLASS (Rogue), damage type (Magic), RANGE (1), a description, and 6 skill
levels. It also names the skin when one is applied ("Skin: Son of Mithril").
=> hero identity, skin identity and ability metadata are all obtainable without a wiki.
**Popup position is deterministic**: it renders ABOVE the long-pressed row and shifts RIGHT with
the column, so an automated pass can crop the name from a predicted offset.
Names captured opportunistically: Berial, Smokey & Meerky, Odie, Pang, Rowan, Sinbad, Thador,
Lily May, Lyca, Sonia, Tilava.

## LABELLED SEED LIBRARY BUILT + VALIDATED (2026-07-25)
From the stuck-match frame (lilymay.png), user supplied the full grid mapping:
  row 0: Indris, Viperian, Dionel, Laios, Reiner
  row 1: Berial, Smokey & Meerky, Odie, Pang, Rowan
  row 2: Sinbad, Nerion, Parisa, Thador, Lily May
  row 3: Granny Dahnie, Lyca, Cryonaia, Sonja, Tilaya
Locked picks that match: Blue1=Dionel, Blue4=Rowan, Blue5=Lily May, Red2=Reiner, Red3=Pang.

18 labelled templates written (Berial and Tilaya skipped - ban overlay obscured them in
that frame). Validation on a DIFFERENT popup-free frame of the same match: **18/18 correct**.
On a frame where a popup covered row 0, the 4 covered cards correctly returned **unknown**
(score ~0.30) rather than being mis-identified - the accept threshold + margin rule works.

Seed artefacts: /tmp/solstice/seed_lib/{*.png, hero_library.json}

## PARKED IDEA: datamined hero IDs + high-res art (2026-07-25)
Worth pursuing AFTER the roster naming pass, not during.

Web check so far (inconclusive - no authoritative ID table found yet):
- dotgg.gg/afk-journey/datamined-unreleased-characters/ - datamining community IS active
  (chibi/full/skeletal 3D models, unreleased heroes surfaced pre-launch), so extracted
  asset sets exist somewhere; no public hero-ID table located yet.
- playafkjourney.com/heroes/ - full hero list w/ skills+stats; possible name/slug source.
- Leaks circulate mainly via Discord + X, which are poor for stable machine-readable IDs.

BETTER LOCAL SOURCE (untried, read-only): the game is installed in Waydroid. Hero art
assets are usually named with the internal hero ID. Listing the APK/OBB asset tree over
adb would give real IDs AND potentially high-res source art - no scraping, no guessing.
Deferred so it doesn't compete with the capture loop for ADB.

DECISION ALREADY MADE: canonical identity stays the immutable name slug. Any datamined
ID becomes an OPTIONAL `external_id` column, never the primary key. Sequential #1-#44
numbering is a throwaway labelling aid for the naming session only.

## BREAKTHROUGH: wiki combat icons match in-game icons (2026-07-25)
User's idea, verified: `File:Hero <Name>.png` on the Fandom wiki IS the in-game combat icon.

My first two attempts scored 5/20 and 0/20 and I wrongly called the approach dead. The bug was
mine: I resized the 180x248 art into a SQUARE (96*s, 96*s), destroying the aspect ratio.

Aspect-preserved + circular mask, transform recovered per hero from 20 known pairs:
    scale 0.68, crop offset (13, 35)   -- consistent for 17/20 (outliers still matched)
Applying that ONE fixed transform: **20/20 correct identification**, mean margin +0.30.

Thin margins needing per-hero tuning: Sinbad +0.039, Hugin +0.071, Temesia +0.111.
Low absolute scores: Marilee 0.458, Atalanta 0.446, Temesia 0.412 (still correct).

CONSEQUENCES
- The hero library can be built ENTIRELY from the wiki API. No in-game capture grind.
- api.php is NOT Cloudflare-protected (the wiki HTML is). No stealth browser needed.
- 173 skins -> 122 heroes; 51 heroes have MORE THAN ONE skin (Cassadee has 2).
  So the schema needs N templates per hero, not the "2 per variant" originally assumed.
- Skins have their own `File:Hero <SkinName>.png` -> skin templates also come from the wiki.

USER'S SKIN RULE (important for template selection):
  the 5x4 selection grid shows heroes UNSKINNED (pre-ban/pre-pick);
  the LOCKED pick renders the owner's skin. So: grid -> base art, locked pick -> base OR skin art.
