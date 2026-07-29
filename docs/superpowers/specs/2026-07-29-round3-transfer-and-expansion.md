# Round 3: does anything transfer, and is there more in the data?

2026-07-29. Frozen at `match.id <= 435`. Two themes now exist, which makes the question
answerable rather than theoretical.

## The problem this round exists to solve

Last night's 69 predictions on the new theme scored **57% directional against a 57% base
rate**, and logloss 0.6916 against 0.6846 for always guessing the base rate. No edge.

The explanation is not that the model is broken. `CROSS_THEME_WEIGHT` is 0.0, so at
midnight Flourishing Wilds started from nothing, and Bradley-Terry has a measured learning
curve: no edge at 150, 200 or 250 matches, and it switched on between 250 and 300.

| first N matches | gain over base | splits won |
|---|---|---|
| 150 | -0.0007 | 10/25 |
| 200 | -0.0013 | 14/25 |
| 250 | -0.0015 | 13/25 |
| 300 | -0.0065 | 21/25 |
| 340 | -0.0086 | 19/25 |

So every prediction last night came from a model below its own threshold. At ~18
matches/hour that is roughly **16 hours of every 3-day theme spent producing a number
worth nothing** - and it repeats at every rotation, forever.

## The data

Local sqlite at `~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite`, read-only,
`WHERE match.id <= 435`.

| theme | matches | rated |
|---|---|---|
| converging-paths | 365 | 86 |
| flourishing-wilds | 70 | 69 |

Note the ratings: only 86 of the old theme carry them (they began mid-theme), but **69 of
70** on the new one do. Rating-dependent ideas are far better supplied than they were.

Per match: outcome, six `hero_slug`, `theme_id`, `event_id`, both ladder ratings, player
names, `predicted_left`/`predicted_source` (see the warning below), and in `match_odds`
the betting pools, displayed odds and spectator count. Per hero in `match_hero`:
`stat_sword`, `stat_heart`, `stat_shield` - **post-match** damage dealt, healing done and
damage taken. `hero` carries faction, class, damage type, attack range, rarity, race.

## Rules that make results comparable

1. **Freeze at id <= 435.** The database is live; last round it grew mid-experiment by
   more than some of the effects being measured.
2. **Ignore `predicted_left` entirely.** It records what was DISPLAYED by whatever
   configuration was running, not evidence about any other one. Refit and predict out of
   sample; the outcome is the only label.
3. **The stats are POST-MATCH.** Using this match's own sword/heart/shield to predict this
   match is measuring, not predicting. They may only be used to learn a hero prior from
   OTHER matches, fitted on the training fold. A leaked model scores ~0.32 logloss; if you
   see anything near that, you have leaked.
4. **Protocol:** 25 seeded shuffle splits, 80/20, mean held-out logloss, paired SE against
   the training-fold base rate, splits won. AND a temporal walk-forward - train on earlier,
   test on the next block of 20 - which is the honest number, because shuffle splits leak
   the future into any fitted prior.
5. **Bar:** beat the base rate by more than its paired SE, on more than 17 of 25 splits,
   and win a majority of forward blocks.
6. **Call the shipped `fit`/`predict`** from `services/solstice/odds.py` for the
   Bradley-Terry baseline. A reimplementation with different regularisation produced a
   false null in an earlier round.

## Question 1: what transfers across a theme boundary?

The centrepiece. We now have a genuine before/after.

- **Do the hero strengths carry?** Fit on converging-paths, predict flourishing-wilds
  cold. If that beats the flourishing-wilds base rate, hero strength survives a rotation
  and discarding it is costing us the first 300 matches of every theme.
- **As a decayed prior rather than a blend.** The operator's design: a transferable signal
  is most valuable when local data is thinnest, so the weight should be a function of
  local evidence, not a constant.
  ```text
  w_meta  = 1 / (1 + n_local / k)     high when n_local is small
  w_local = n_local / (n_local + k)
  ```
  Feed the previous theme's thetas in as the PRIOR MEAN of the new fit rather than as an
  additive term - a prior has no blend weight to estimate, and local evidence overrides it
  naturally. Round 2 found that stacking weights cannot be estimated at this sample size.
- **What value of k?** Sweep it. Report the cold-start curve: cumulative logloss over the
  new theme's first 20, 50, 70 matches, with and without the carried prior.
- **Does anything else transfer?** Rank-weighted popularity was the operator's candidate
  precisely because "which heroes strong players choose" is a claim about the game rather
  than about one theme's outcomes.

## Question 2: is there more in what we already collect?

Ranked by what has NOT been tried. Round 1 and 2 killed the additive stat families and the
stats-as-shrinkage-target route; do not re-run those without a new mechanism.

- **Rank-weighted hero popularity, now properly supplied.** Score a hero by the mean
  rating of the players who picked it. On 61 rated matches it cleared the bar (+0.0137,
  3.2x SE, 18/25) where plain popularity failed (-0.0111, 12/25) - the sign flipped
  between them. There are now 155 rated matches. Does it hold, and does it transfer?
- **Team balance rather than sums.** Every stat test so far summed or ratio'd. None asked
  whether a comp is complementary - three glass cannons and three tanks can have identical
  totals and very different outcomes.
- **Damage divided by opposing tanking.** The closest anything has come: gain exceeded its
  SE at 16/25, just under the bar. Worth one more look with more data.
- **Stats per rating point.** 5M damage from a 4200 player is a different claim than 5M
  from a 4500.
- **Consistency rather than average.** Every test used means; none used variance.
- **The rating step, re-checked.** Now "nothing under 100 points, then a flat +-0.25
  log-odds", measured on 95 rated matches at 7.6x SE and 23/25 - but chosen as the best of
  13 candidates, so some of that margin is selection. 155 rated matches is the confirming
  sample, and the far end (gaps above 150) was effectively unobserved.

## Question 3: what does the wider world know?

Web research is in scope. Draft-based win prediction is a studied problem in MOBAs and
auto-battlers - hero embeddings, synergy/counter matrices, sequential draft models,
Bayesian hierarchical pooling. Bring back anything that survives contact with 435 matches
and 97 heroes, and say plainly what needs 10,000 and is therefore irrelevant here.

## What to report

Most useful first, in plain language:

1. Does the transferable prior work? One number: cold-start logloss on the new theme with
   and without it.
2. Anything that clears the bar, with its numbers.
3. Anything that failed, so nobody re-runs it.
4. The honest ceiling, if you think there is one.

Failures are as valuable as successes here - about 58 configurations have already lost,
and the ledger records them so they are not retried.
