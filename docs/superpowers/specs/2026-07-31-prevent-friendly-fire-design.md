# Prevent Friendly Fire - Arena and Supreme Arena

Do not attack friends or guild-mates. A per-mode checkbox, off by default.

Written 2026-07-31. Status: design, not yet planned or implemented.

## The problem

Both arena modes currently pick an opponent **positionally**, and neither reads who the
opponent is:

- **Arena** (`mixins/arena.py`, `_choose_opponent`) template-matches `arena/opponent.png`
  inside `CropRegions(right=0.6)` - the left 40% of the screen - and taps the single
  best-scoring match. It never looks at the middle or right cards. The comment
  "Target weakest opponent" is an assumption about the game's ordering, not a measurement.
- **Supreme Arena** (`mixins/supreme_arena.py`, `_sa_choose_opponent`) taps a hardcoded
  coordinate chosen by the `Opponent Position` setting: `Point(165, 950)`, `(540, 950)` or
  `(915, 950)`. It does not template-match the card at all.

So there is no filter to extend. Any friendly-fire guard has to add a READ step that does
not exist today, then a decision, then a fallback.

## Goal and non-goals

**Goal.** When the toggle is on, never initiate a battle against an opponent the game
marks as a Friend or a Guild Member.

**Non-goals.**

- Choosing the *best* opponent. This is a safety filter, not an opponent optimiser.
- Reading names, scores or power. Nothing here needs to know who anyone is.
- Maintaining a friends or guild-member list. The game already labels the cards; a local
  list would be a second source of truth that goes stale.
- Changing behaviour when the toggle is off. Off is the default and the current code path
  must be untouched in that case.

## What the game shows

Measured from frames captured 2026-07-31, archived at
`/mnt/vault/adbautoplayer/arena-friendly-fire/`:

| file | what it establishes |
|---|---|
| `01-friend-badge-middle-card` | Arena, Friend badge on the middle card, Refresh 7/7 |
| `02-guild-member-badge-middle-card` | Arena, Guild Member badge, same card, same y |
| `03-refresh-exhausted-x-button-friend-on-left` | Arena, badge on the LEFT card at a different y; refresh counter replaced by a bare X |
| `04-give-up-confirmation-dialog` | "Give up this challenge?" with X (cancel) and tick (confirm) |
| `05-supreme-arena-select-opponent-no-badges` | Supreme Arena baseline, no badges - the frame that proves a band reads empty |
| `06-supreme-arena-friend-badge-right-card` | Supreme Arena, Friend badge on the right card |

### Badges

Two badges, both a solid pill with an emblem and text, centred on their card:

- **Friend** - green, RGB approximately (60, 160, 110)
- **Guild Member** - cyan, RGB approximately (64, 185, 192), wider because the text is longer

When a player is both, the game shows **Friend**. That ordering is irrelevant to the
decision - both mean "do not attack" - and matters only for what the log says.

### Measured geometry

Arena, 1080x1920:

| badge | card | x | y |
|---|---|---|---|
| Friend | middle | 456-644 (w 188) | 954-993 (h 39) |
| Guild Member | middle | 421-679 (w 258) | 953-993 (h 40) |
| Friend | left | 93-281 (w 188) | 1013-1052 |

Both badges occupy the **same y band on the same card**; Guild Member is simply wider.
Cards are staggered vertically, so the band differs per card - the left card sits ~59px
lower than the middle. A fixed y band across all cards would miss badges.

Supreme Arena is a **different screen**, not a variant: angled banner cards, staggered
~100px per step, showing name / power / `Rank +N` / `Top N` with no score bar. Friend
badge on the right card measured at **x 786-922, y 985-1014** (w 137, h 29) - smaller than
Arena's.

### Refresh and give up

- Arena: **Refresh 7/7**. Supreme Arena: **Refresh 5/5**. Per match, not per day.
- When refreshes are exhausted the control becomes a **bare X** in the same position, with
  no counter.
- Tapping the X raises **"Give up this challenge?"** with a white X (cancel, x 555-710) and
  a green tick (confirm, x 795-950), both around y 1245.

**The X forfeits the challenge.** It is not a close button. The bot only reaches it as the
designed last resort, never incidentally.

## Detection

Two independent signals per card. **Either one firing flags the card.** A false positive
costs one refresh; a false negative attacks a friend. Those costs are not comparable, so
the union is deliberate.

### Signal 1 - colour run

Within the card's x window, find a horizontal run of at least ~60 pixels matching either
badge colour. **A run, not mere presence:** on the Supreme Arena baseline frame, naive
"is this green anywhere" matches 134 stray pixels from the title lettering and artwork,
while the run test scores 115 on a real badge and **0** on all three empty cards.

The rule must accept **green or cyan**. A green-only rule sails straight past Guild Member.

Language-independent, costs microseconds, needs no template file.

### Signal 2 - OCR

OCR a crop of the **whole card**, and flag on "Friend" or "Guild Member".

**The two signals are scoped differently on purpose.** If both were restricted to the same
narrow band, a wrong band would defeat both and the redundancy would buy nothing. Colour
runs over a generous per-card band; OCR covers the entire card. A position surprise is
caught by OCR; a localised client is caught by colour.

OCR cost is acceptable here because it reads one card crop, not a full frame - unlike the
long-press OCR that was shelved in Solstice Clash for exactly that reason. **The mode has
no timer**, so a second confirming screenshot before acting is free.

## Selection algorithm

Per attempt, with the toggle on:

1. Screenshot the select-opponent screen.
2. Evaluate **cards 1 and 2 only**. The right card is never considered - it is routinely
   outside the player's power bracket. This is a deliberate product decision, not a
   detection limit.
3. Take the first unflagged card, preferring card 1.
4. If both are flagged: tap Refresh, take a fresh screenshot, and re-evaluate.
5. Repeat until a card is taken **or the refresh control has become the X**. Do not count
   refreshes: the limit differs per mode (7 and 5) and could change in a patch. Exhaustion
   is a visual fact.
6. When exhausted with both still flagged: tap the X, then confirm on
   "Give up this challenge?".

Before any tap that commits to a battle, take a second screenshot and require both reads
to agree. Being slow is free; being wrong is not.

## Settings

One field per mode, so the two stay independently controllable as they are today:

```
Prevent Friendly Fire - do not attack friends or guild-mates in this mode
```

`bool`, default **false**. Added to `ArenaSettings` and `SupremeArenaSettings` in
`games/afk_journey/settings.py`. Pydantic generates the UI, so no frontend work.

Off is the default because this changes which opponent gets attacked, and an upgrade must
not silently alter behaviour for anyone who has not asked for it.

## Frame collection

Every evaluated select-opponent frame is written to
`/mnt/vault/adbautoplayer/arena-friendly-fire/`, named with mode, timestamp and the
detection outcome.

Two reasons this is in scope rather than a nice-to-have:

- The Supreme Arena **Guild Member** badge has never been observed. Its geometry is
  assumed (see Open assumptions) and only a real sample can confirm it.
- A frame where the two signals **disagree** is the most informative artefact this feature
  can produce: it means one detector is wrong, and it shows which.

## Error handling

| situation | behaviour |
|---|---|
| Screenshot fails or screen is not the select-opponent screen | Fall through to existing behaviour and log. Never tap blind. |
| OCR raises | Treat as no-signal, rely on colour, log at warning. OCR failure must not abort the mode. |
| The two signals disagree | Flag the card - safe side - and archive the frame prominently. |
| Refresh tapped but the screen does not change | Re-screenshot once, then treat as exhausted rather than tapping repeatedly. |
| Give-up dialog does not appear after tapping X | Log and stop the mode. Do not tap coordinates hoping. |
| Toggle off | Existing code path, untouched, no screenshots, no OCR. |

## Testing

- Pure-logic tests over the six archived frames: each must produce the expected per-card
  flags. The baseline frame must produce **no** flags on any card - that is the false
  positive guard.
- A test that the colour rule accepts cyan as well as green, since a green-only rule is the
  obvious regression.
- A test that the run-length threshold rejects the 134 stray pixels in the baseline frame.
- Decision-table tests for the selection algorithm: card 1 clean; card 1 flagged and card 2
  clean; both flagged with refreshes left; both flagged and exhausted.
- No device is required for any of the above; frames are fixtures.

## Open assumptions

1. **Supreme Arena's Guild Member badge is assumed** to sit in the same band as its Friend
   badge, styled like Arena's Guild Member badge. Never observed, because guild-mates who
   are also friends display as Friend. Mitigated by the OR rule and by frame collection.
2. **Arena card 3 and Supreme Arena card 3 are never evaluated**, so no geometry was
   measured for them. If the product rule ever changes, that work is outstanding.
3. **Badge bands were measured at 1080x1920 only.** Other resolutions are unverified.
4. **The left-card band in Supreme Arena is inferred** from the right card plus the
   observed stagger, not measured.
