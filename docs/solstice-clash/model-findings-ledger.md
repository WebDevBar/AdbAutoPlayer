# Model findings ledger

What has been measured, by whom, on how much data, and what would re-open it.

The point of this file is that model questions get re-asked. Someone in three months will
wonder whether hero class matters, or whether the crowd should count - and without this
they will spend a day rediscovering that both were tested against 335 matches and lost.
Every entry says what would change the answer, because most of these are answers about a
sample size rather than about the game.

Started 2026-07-29. Append; do not rewrite history. A superseded row keeps its date and
gains a note.

## How things are measured

25 seeded shuffle splits, 80/20, mean held-out logloss against the training-fold base
rate, reporting the paired standard error and how many of the 25 splits were won. From
round 2 onward, temporal walk-forward validation is reported alongside and is the number
that counts - shuffle splits leak the future into any fitted prior.

**The bar:** beat the base rate by more than its paired SE, on more than 17 of 25 splits,
and win a majority of forward blocks.

Three independent implementations run each round: this project, Codex, and Fable. Round 1
proved why - a reimplementation of Bradley-Terry with different regularisation produced a
false null that two other runs contradicted.

## Confirmed working

| finding | measured | when | re-open if |
|---|---|---|---|
| **Bradley-Terry on hero identity** clears the bar | 0.6884 vs 0.6952 base, SE 0.0020, 22/25; temporal 8/9 forward blocks. Three implementations agree | 2026-07-29, n=335 | it is the incumbent - watch it does not decay as the roster changes |

Note the history: the same model was measured as having NO edge at 245 matches
(0.6967 vs 0.6993, 15/25). That null was correct when made. The edge appeared between 250
and 300 matches, as 93 hero parameters became learnable. **This is the single most
important lesson in this file: a null at one sample size is not a null.**

## Confirmed dead, with the reason

| finding | measured | when | re-open if |
|---|---|---|---|
| **Post-match stats** (sword/heart/shield) as a predictor | ~12 variants: raw, share-of-team, log, class-centred, outcome-adjusted, several shrinkages. All at or below base rate; all DILUTE BT when added | 2026-07-29, n=335 | untried: stats as BT's shrinkage target rather than an added term. Round 2 tests it |
| **Class / faction composition** | 0.7116-0.7438, the worst family tested, 3-6/25 | 2026-07-29, n=335 | strongly negative, not merely null - would need a reason to revisit |
| **Class-vs-class counter matrix** | 0.7073-0.7324, 2-3/25 | 2026-07-29, n=335 | a real counter system would show here; it does not |
| **Race composition** | 0.7343, 4/25 | 2026-07-29, n=335 | - |
| **Player identity / player win-rate prior** | 0.7343-0.6975, worst single candidate. A player's spectated win rate does not even correlate with their ladder rating (r=-0.11, n=101) | 2026-07-29 | more matches per player; currently a median of 9 |
| **Faction synergy** (same-faction bonuses) | +0.0020, 11/25 | 2026-07-29, n=335 | - |
| **The crowd's betting split** | 0.7008-0.8307 depending on form. Separately, on 54 scored predictions: flat across its own confidence, and a noisy echo of the rating gap (correlation 0.475, same pick 40/51) | 2026-07-29 | it is a market and markets usually beat models - so re-test when the player base or pool sizes change materially |

### Why the stats fail, since it is not obvious

They are real measurements of 2,000 hero-performances, which is far more information per
match than one win/loss bit. They still lose, and both reviewers reached the same
diagnosis independently: **their variance is dominated by role.** Mages deal damage,
Tanks absorb it, Supports heal - so the numbers mostly say what kind of hero someone
picked, and role composition is the worst-performing family tested. Class-normalising them
("does more than peers in the same role") is also null.

## Round 2: the best combination is no combination

2026-07-29, frozen at 340 matches, both reviewers independently. Every stack of features
onto Bradley-Terry scored WORSE than Bradley-Terry alone, and the full combination scored
worse than the base rate. 340 matches cannot estimate stacking weights.

| candidate | vs base (shuffle) | splits | forward blocks |
|---|---|---|---|
| BT alone, sigma 0.20 | +0.0086 | 19/25 | 8/9 |
| BT alone, sigma 0.15 | +0.0070 | 20/25 | 8/9 |
| BT + popularity | +0.0044 | 15/25 | 6/9 |
| BT + rating gap | +0.0004 | 16/25 | 4/9 |
| BT + slot | -0.0029 | 11/25 | 5/9 |
| everything combined | -0.0036 | 13/25 | 4/9 |

**The stats question is closed.** The shrinkage-target route - the last untried mechanism,
and the only one that put them where BT is weakest - returned an exact null: -0.0000,
SE 0.0006, 12/25. The map from stats to hero strength exists and predicts approximately
zero. Both additive and prior mechanisms are now exhausted; do not re-run under ~1,000
matches.

**Popularity is subsumed rather than wrong.** It predicts on its own and survives temporal
validation, but adding it to BT degrades BT on both protocols. It is a low-resolution
proxy for what BT already knows.

**Applied from this round:** `SIGMA_THETA` 0.15 to 0.20, `W_CROWD` 0.70 to 0.0,
`W_HEROES` 0.50 to 1.0. The two reviewers diverge on sigma above 0.20 - Codex prefers
0.30, Fable measures 0.25 as missing the splits bar - so 0.20 is the value both support.
Re-sweep at each rough doubling of the data; the optimum has been drifting up as the
corpus grows and is not converged.

## Promising, not established

| finding | measured | when | needs |
|---|---|---|---|
| **Hero pick popularity** | clears the bar alone; BT+popularity is the best model tested (-0.0102). But the solo edge falls to -0.0023, 5/9 under temporal validation | 2026-07-29, n=335 | honest temporal validation in combination. Part of the shuffle-split edge is hindsight about what became popular |
| **Draft slot** | 0.6940 vs 0.6952, 18/25 - marginal | 2026-07-29, n=335 | more data; spends ~248 sparse parameters for 0.0012 |
| **Rating gap** | -0.0007 to -0.0091 depending on form. BT+rating was round 1's second best | 2026-07-29, n=56 rated | ratings only exist from 2026-07-28. This is the thinnest evidence in the file |
| **Rank-weighted popularity** - the mean rating of the players who picked a hero, rather than how often it was picked | On the 61 rated matches: **+0.0137, 3.2x SE, 18/25 - clears the bar**, where plain popularity FAILS on the same matches (-0.0111, 12/25). On all 340 the sign flips and it fails (-0.0054, 10/25) | 2026-07-29, operator's idea | ~200 rated matches. 61 gives ~12 test matches per split, and the two subsets disagree. The direction is exactly as predicted - "picked by strong players" beats "picked often" - which is why this is worth returning to rather than filing as null |
| **Damage ÷ opposing tanking** (ratio, not sum) | +0.0032, gain exceeds SE, but 16/25 - below the bar | 2026-07-29, n=333 | the only additive-stat variant showing anything. Ratio form matters: as a sum it is worthless |

## Settled by measurement, not opinion

- **Rating formulas are interchangeable at this sample size.** Stated nudge table 0.6764,
  Elo curve at scale 600 0.6754, win-rate odds ratio 0.6752. The Elo form is preferred for
  having one parameter instead of nine, no band cliffs, and sensible extrapolation - not
  for scoring better, because it does not.
- **The ladder is Elo-shaped but shallow.** Measured from 38 back-to-back observations of
  the same player: wins give about +21, losses about -20, with only ~3 points of variation
  across a 160-point gap swing. So a 157-point gap is roughly eight net wins of
  difference, not a strength ratio - which is why the rating predicts as weakly as it does.
- **The left side wins 44% of 335 matches.** Noise, not a side advantage; the model already
  fits an intercept.
- **Theme influence is treated as nothing** (operator, 2026-07-28): a theme applies
  modifiers that hit every hero equally. `CROSS_THEME_WEIGHT` went 0.35 to 1.0 and the
  display gate counts the event. `theme_id` is still recorded on every match so this can be
  tested properly once two themes each hold a few hundred matches.

## Methodology traps already paid for

- **Do not reimplement Bradley-Terry to test it.** Call the shipped `fit`/`predict`. A
  reimplementation with different regularisation produced a false null contradicted by two
  other runs.
- **Freeze the data.** The database is live and grew mid-experiment by more than some of
  the effects being measured.
- **Shuffle splits leak the future** into any training-fold statistic. Temporal
  walk-forward is the honest test and popularity is measurably flattered without it.
- **The paired SE is optimistic.** 25 splits of the same 335 matches are correlated; the
  splits-won criterion is what carries the weight.
- **Multiple comparisons.** ~58 configurations have been tested against a 1-SE bar. Only
  results at 3+ SE that replicate on fresh seeds and temporally deserve belief.
- **The leakage yardstick:** using this match's own sword stat scores 0.3230. Any hero-stat
  model reporting anywhere near 0.32-0.55 has leaked, whatever its write-up says.
