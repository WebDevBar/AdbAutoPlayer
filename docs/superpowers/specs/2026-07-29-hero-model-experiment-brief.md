# Hero model - experiment brief

2026-07-29. A shared brief for three reviewers to propose and test predictors. Nothing
here ships; the deliverable is a measured answer to "does any formula beat the base rate."

Companion to `2026-07-29-odds-calibration-review.md`, which established that the current
displayed number has no edge, that the crowd is a noisy echo of the rating gap, and that
the hero term contributes nothing.

## The prediction problem

Predict P(left wins) for a 3v3 draft match, using only what is knowable BEFORE the fight,
in time to bet. Scored by logloss against the base rate on held-out data.

## THE LEAKAGE RULE - read before proposing anything

`stat_sword`, `stat_heart` and `stat_shield` are **post-match** figures read off the
summary screen. Their meaning is now confirmed empirically from 1,986 rows joined to
hero class:

| class | mean sword | mean heart | mean shield | % heart > 0 |
|---|---|---|---|---|
| Mage | 3.8M | 0.2M | 3.7M | 21% |
| Marksman | 3.1M | 0.1M | 3.4M | 15% |
| Warrior | 2.5M | 0.9M | 8.0M | 66% |
| Rogue | 2.1M | 0.2M | 5.0M | 23% |
| Tank | 1.4M | 1.2M | 10.0M | 75% |
| Support | 1.2M | 3.2M | 4.8M | 81% |

sword = damage dealt, heart = healing done, shield = damage taken. The orderings are the
DPS, healer and tank orderings exactly.

**They describe the fight that already happened.** A model that uses this match's stats to
predict this match's winner is measuring the result, not predicting it, and will score
brilliantly and be worthless.

They are legitimate for exactly one thing: **learning a hero's strength from OTHER
matches, and using that learned strength as a pre-match feature.** Any use of them must
respect the train/test split - a hero prior fitted on the training fold only.

## Why they are worth the trouble

Bradley-Terry learns from one bit per match: who won. 331 matches over 93 heroes is about
3.5 bits per parameter, which is why it has no edge.

The stats give six continuous observations per match - roughly 2,000 of them - about how
much each hero actually contributed. That is orders of magnitude more information per
match about hero strength, from data already collected.

## The data

Local sqlite at `~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite`.

- `match` - `outcome` ('left'/'right', never 'draw' - draws are censored by the game),
  `left_rating`/`right_rating` (4-digit ladder, present from 2026-07-28 only),
  `theme_id`, `event_id`, `captured_at`, `predicted_left`, `predicted_source`.
- `match_hero` - six rows per match: `side`, `slot`, `hero_slug`, and the three stats.
- `hero` - static, known before the fight: `faction` (8 values), `hero_class` (6),
  `damage_type`, `attack_range`, `rarity`, `race`.
- `match_odds` - one row per match: `left_pool`, `right_pool`, `left_odds`, `right_odds`,
  `spectators`.

331 matches are complete on both sides and decisive. 52 also carry ratings.

## Pre-match features available

- Hero identity, six per match (93 seen, 153 known)
- faction, hero_class, damage_type, attack_range, rarity per hero - **8 factions and 6
  classes means a composition model spends ~14 parameters where identity spends 93**
- The ladder rating gap, on 52 matches
- The crowd's pool split (measured as a noisy echo of the rating; available but suspect)

## The protocol - identical for every candidate

Deviating from this makes results incomparable, which is the whole point of a shared brief.

1. 25 shuffle splits, 80/20, seeded 0-24.
2. Metric: mean logloss on the held-out 20%.
3. Baselines to beat, both computed on the same folds: the training-fold base rate, and
   the current regularised Bradley-Terry.
4. Report mean logloss, the paired standard error against the base rate, and how many of
   the 25 splits the candidate won. A candidate that wins on mean but 13/25 splits has
   not shown anything.
5. Anything fitted - hero priors, shrinkage constants, weights - is fitted on the
   TRAINING FOLD ONLY, refitted per split.

## Candidate families

Proposals are not limited to these.

1. **Composition**: logistic on class counts and faction counts per side (~14 params).
2. **Damage-share prior**: per hero, mean share of their team's sword/heart/shield across
   training matches; team score is the sum; logistic on the difference.
3. **Counter matrix**: class-vs-class, 36 cells, heavily shrunk.
4. **Contribution-weighted Bradley-Terry**: same BT structure, but each match contributes
   per-hero weight from the stats rather than one binary outcome.
5. **Rating blended with the best of the above**, weight fitted on the training fold.

## What a positive result requires

Beating the base rate by more than its paired standard error, on more than 17 of 25
splits. Anything less is a direction to collect more data on, not a model to display.

Stated before the experiments run, so the bar cannot move afterwards.
