# Converging Paths - match data

Screenshots and their parsed data live together in this folder. One details-screen
screenshot per match, `match-NNN.png`, with the block of the same id below.

Compete and spectate matches are recorded TOGETHER on purpose. The claim under test is that
the details screen is identical in both modes; separating them by folder would hide the
evidence either way. Each block records its `mode` so the two can be compared later.

Blue is the LEFT player. On this screen that is the `Ally` panel in both modes observed so
far.

---

## match-001

![match-001](match-001.png)

```
id: 001
screenshot: match-001.png
mode: compete
date: 2026-07-26 09:45
theme: Converging Paths
blue_player: GameRetro
red_player: Sisif
blue_rating:
red_rating:
blue_rank:
red_rank:
blue_heroes: Solise, Valka, Kafra
red_heroes: Laios, Callan, Silven
winner: red
```

Per-hero stats read from the same screenshot. Columns are named for their ICONS, because the
shield column's meaning is still unconfirmed:

| side | hero | sword | heart | shield |
|---|---|---|---|---|
| blue | Solise | 0 | 1,960K | 4,187K |
| blue | Valka | 1,994K | 0 | 5,799K |
| blue | Kafra | 1,486K | 0 | 6,785K |
| red | Laios | 2,639K | 3,228K | 4,123K |
| red | Callan | 245K | 0 | 4,823K |
| red | Silven | 5,022K | 0 | 253K |

**Parse notes.** All six heroes identified by image match at 0.80-0.91 with margins
0.21-0.42, so this compete screenshot works with the cells measured on a SPECTATE frame -
first direct evidence for the identical-screen claim. The winner and all eighteen stat
numbers parsed automatically.

`blue_player` came back from OCR as `GAME`, not `GameRetro`: the account badge overlaps the
start of the name on this screen. Corrected by hand above. Worth knowing before player names
are used for anything that matters, such as the dedupe key.

Ratings and ranks are blank because the details screen does not show them - they are on the
pre-match screen. Leave them empty rather than guessing.

---

## Deadline

`Converging Paths` rotates on **2026-07-28** (event screen showed "Rotates in 2d 20h" on
2026-07-26). Matches are only comparable within a theme, so anything not collected under this
theme before the rotation cannot be recovered afterwards - it belongs to a different
generating process. Collection needs to be running before then.
