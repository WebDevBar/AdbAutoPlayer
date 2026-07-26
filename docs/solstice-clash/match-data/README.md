# Solstice Clash - manual match data

Screenshots and their parsed data live together in THIS folder. One summary screenshot per
match, named `match-NNN.png`, with its parsed block below carrying the same id. I read this
file and insert the rows into `data/solstice_clash/heroes.sqlite` via `MatchStore`.

Everything in a block is transcribed from the screenshot beside it, so any row can be
re-checked against the image it came from rather than taken on trust.

## Why the theme matters

Hero balance and the map change with the theme, so a match is only comparable to other
matches from the SAME theme. A row without a theme cannot be pooled with anything and is
close to useless for modelling.

**Current theme: `Converging Paths`** (read from the event screen 2026-07-26, showing
"Rotates in 2d 20h"). The previous theme was `Fierce Duel`. If you are entering matches
from a different theme, put that theme in the row - do not leave it to default.

## How rows are stored

- `source` is `manual_import` so these stay distinguishable from rows the mode collects.
  Only mode-collected rows have OCR-confirmed identities; imported ones are your word,
  which is fine, but analysis should be able to tell them apart.
- Duplicate protection uses the canonical `natural_key` (players, ratings, sorted hero
  slugs per side, theme, outcome, 30-minute time bucket). Re-importing the same file will
  not create duplicates.
- A row missing anything needed for the key is stored with `natural_key = NULL` and is
  excluded from any future cross-machine sync.

## Hero names

Use the in-game display name, e.g. `Atalanta`, `Smokey & Meerky`, `Granny Dahnie`. I resolve
them to slugs against the `hero` table and will tell you about anything I cannot match,
rather than guessing.

## Fields

| field | required | notes |
|---|---|---|
| `date` | yes | `YYYY-MM-DD HH:MM` local. Used for the time bucket in the dedupe key. |
| `theme` | yes | e.g. `Converging Paths` |
| `blue_player` / `red_player` | no | improves dedupe and lets us control for player skill |
| `blue_rating` / `red_rating` | no | the number beside the coin icon |
| `blue_rank` / `red_rank` | no | the `Rank: N` value |
| `blue_heroes` / `red_heroes` | yes | exactly 3 each, comma separated |
| `winner` | yes | `blue` or `red` |
| `screenshot` | yes | filename in this folder, e.g. `match-001.png` |

Blue is the LEFT player on screen. On the summary screen blue is the `Ally` panel when
spectating.

## Matches

Copy the block per match. Delete the example before I import - I will ignore any block whose
`date` is `EXAMPLE`.

```
id: EXAMPLE
screenshot: match-000.png
date: 2026-07-26 09:40
theme: Converging Paths
blue_player: leeler
red_player: Tamau
blue_rating: 4351
red_rating: 4294
blue_rank: 1
red_rank: 1
blue_heroes: Nerion, Arden, Berial
red_heroes: Brutus, Perseus, Tasi
winner: red
```

```
id: 001
screenshot: match-001.png
date:
theme: Converging Paths
blue_player:
red_player:
blue_rating:
red_rating:
blue_rank:
red_rank:
blue_heroes:
red_heroes:
winner:
```
