# Model findings ledger

What has been measured, by whom, on how much data, and what would re-open it.

The point of this file is that model questions get re-asked. Someone in three months will
wonder whether hero class matters, or whether the crowd should count - and without this
they will spend a day rediscovering that both were tested against 335 matches and lost.
Every entry says what would change the answer, because most of these are answers about a
sample size rather than about the game.

Started 2026-07-29. Append; do not rewrite history. A superseded row keeps its date and
gains a note.

## Revisit next

**The confidence threshold, once Flourishing Wilds has ~200 of its own matches.** The one
live question. See the threshold section below - the ordering is real, the line is not yet
earned, and the check runs itself as the new theme accumulates confident calls. No new
collection work, no decision needed before then.

**The rating step against its pre-registered challenger, at ~250 rated matches.** The
shipped step stopped confirming at 155 (see round 3). The challenger is written down in
advance so the next look is not another after-the-fact choice: "+0.25 log-odds to the
higher-rated side at any nonzero gap".

Rating evidence is scoped per EVENT, not per theme, because a player's skill does not
change when the battlefield does. So unlike the hero model, this survives a rotation.

~~Rank-weighted hero popularity~~ - closed 2026-07-29 at 155 rated matches. It was the
entry this file called the most promising untested signal. See round 3.

## Hero strength does NOT survive a theme rotation

2026-07-29, measured across the first real boundary: 365 Converging Paths matches against
70 Flourishing Wilds ones.

| | logloss | directional |
|---|---|---|
| carried strengths, cold | 0.6989 | **33/70 = 47%** |
| always 50% | 0.6931 | - |
| the old theme's base rate | 0.6944 | - |

Worse than a coin flip. And as a decayed prior mean - the operator's `w_meta` schedule,
which is the right shape for a transferable signal - it hurts at every k tried, from 5 to
1000, by 0.0137 to 0.0164 against local-only.

So the transferable-prior idea is dead for hero strength, and the per-theme scoping is
vindicated a second time: not only do the roster and the battlefield change, the strengths
measurably do not carry.

**The consequence is a real product limit, not a bug.** Bradley-Terry needs ~250-300
matches to say anything, every rotation resets it, and nothing can be carried forward. At
~18 matches an hour that is roughly the first 16 hours of each 3-day theme spent producing
a number worth nothing. Anything that fixes cold start has to be a signal that is not
learned from this theme's outcomes at all.

## The model is selective, not flat - the threshold finding

2026-07-29. The model looked useless: predictions clustered within ~5 points of even, and
57% directional on the new theme against a 57% base rate. That average hides two
populations. Walk-forward, out of sample, refitting before every match:

| threshold | Converging Paths, theme-locked | cross-theme fit | Flourishing Wilds, theme-locked | cross-theme fit |
|---|---|---|---|---|
| >=54% | 67% (66) | 55% (112) | 50% (8) | 51% (41) |
| >=55% | 68% (40) | 56% (61) | 60% (5) | 57% (30) |
| >=56% | **78% (27)** | 67% (36) | 100% (3) | 52% (23) |
| >=58% | 64% (11) | 77% (13) | 100% (3) | 67% (12) |

Pooled at >=56%: 24/30 = 80%. P(78% or better from a coin at n=27) = 0.00.

Two things fall out. First, a fifth of matches are ones the model reads well and four
fifths it cannot read at all - and the bubble has been showing both, which is why it felt
unreliable. If this survives honest testing the product is "show nothing until the number
clears the line". Second, the theme-locked fit beats the cross-theme fit at the thresholds
that matter on the mature theme, which is the transfer result again from a different angle:
the other theme's matches do not even help the model pick its spots.

### The verdict on it: the ordering is real, the line is not yet earned

Reviewed 2026-07-29. Three separate findings, and they do not all point the same way.

**The confidence ordering is real.** The rise is monotone across every threshold, and a
calibration regression of outcome on out-of-sample log-odds gives a slope of **2.07 (SE
0.96)** - positive at 2.2 sigma. The model does know which matches it can read. That is the
part to trust, and it is not a claim about any particular cell.

**The specific line is not.** The 56% cell is n=17 to n=27 depending on protocol - the
contents are protocol-sensitive, which is what a fragile cell looks like - and after
accounting for scanning about six thresholds its effective P is nearer 0.03-0.05 than 0.00.
An in-theme time split validates only weakly: the first half chooses 0.52, and the second
half scores 59.5% there (P=0.05); higher thresholds are too thin to split at all.

**The cross-theme validation cannot be run yet, for a structural reason worth knowing:
zero Flourishing Wilds predictions cleared 55% all night.** Evidence damping keeps a cold
theme pinned near even, so the gate self-abstains on a new theme by construction. That is
the desired behaviour, and it also means the line can only ever be validated on a mature
theme. It will validate itself for free as the new theme fills - the predictions are
already being recorded.

**The gate needs no side-conditions.** This was the more valuable question and the answer
is negative: nothing recorded predicts which calls land, beyond what the confidence number
already contains. Confidence correlates with mean hero appearances (+0.54), evidence factor
(+0.49) and training size (+0.45) - mechanically, since damping is multiplicative - so the
number already encodes "well-seen comp, mature theme". The only feature with any direct
correlation to correctness is the appearance count of the least-seen hero in the match
(+0.138, n=334, ~2.5 sigma), which is weak and largely redundant with confidence. So the
gate stays a confidence line, not a condition.

**Honest expectation, and what to tell a user:** show nothing below ~54-55%, show the call
above it, and expect roughly **two in three** - not four in five - until the new theme's own
confident calls settle it. The 78-80% figure has the widest error bars in the round.

One loose thread, deliberately not acted on: the calibration slope of ~2 suggests the model
is UNDER-confident, meaning the true readable tail may be larger than the displayed spread
implies. Multiplying the log-odds by ~2 is an in-sample number and shipping it would be the
same error as the threshold. Registered, not applied.

**Standing requirement for all future tuning:** report accuracy AT THRESHOLDS, both
theme-locked and cross-theme, alongside logloss. A candidate that improves the tail is
worth more than one that improves the average, because the tail is the only part anyone
acts on.

## Recorded predictions are not tuning data

**When retuning, ignore `predicted_left` entirely.** What matters is the match outcome and
the model being tested: every experiment refits from scratch and predicts out of sample,
so a stored prediction is an artefact of whatever configuration happened to be running
when it was made, not evidence about any other one.

The stored predictions answer a different question - was the number we SHOWED people
calibrated - and that is worth keeping, but it is not the same question as "is this model
any good".

Which matters because of what happened on 2026-07-29. Between roughly 02:00 and 04:40
UTC, predictions were produced by a model that was: fitting on effectively zero matches,
because 14 matches had been filed under "unknown" after the rotation and cross-theme
weight was zero; and over-trusting the hero term, because `hero_evidence` was counting
appearances from matches weighted zero. Both are fixed and both fixes are retroactive -
the matches were re-filed and appearances is recomputed on every fit - so the DATA is
intact. Only the record of what was displayed in that window is not a fair sample of any
configuration, and should be excluded when the displayed number's calibration is next
assessed.

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

## Round 3, 2026-07-29, n=435 across two themes

Both reviewers ran it independently and agreed on every material point. This round was
mostly a graveyard, which is its value: four of the six most promising open ideas are now
closed, including the one this file called "the single most promising untested signal".

### Rank-weighted popularity is DEAD

Scoring a hero by the mean rating of the players who pick it. At 61 rated matches it
cleared the bar (+0.0137, 3.2x SE, 18/25) exactly as predicted, on the same matches where
plain popularity failed. At 155 rated matches: **-0.0078, 6 of 25 splits**, walk-forward
-0.0092, null on both themes separately, and negative transfer in both directions.

The diagnosis kills it rather than merely failing it: its correlation with the fitted
Bradley-Terry strength is **0.008**. It was never a strength proxy that needed
disentangling - the round-1 worry - it was noise that got lucky on 61 matches. Do not
re-open on sample size alone.

**The lesson costs nothing to learn twice:** this file already records that a null at one
sample size is not a null. The converse is the same statement and is easier to forget - a
PASS at one sample size is not a pass. 61 matches produced a 3.2x-SE result from a feature
now measured to contain nothing.

### The rating step did not confirm

At 95 rated matches the shipped rule - nothing under a 100-point gap, then a flat +-0.25
log-odds - measured +0.0092 at 7.6x SE, 23 of 25 splits. At 155 rated matches it adds
**+0.0015, 1.6x SE, 15 of 25**. Codex measured the same thing at +0.0003.

Its premise half-broke too. Round 2 measured sub-100 gaps as worthless (higher-rated side
won 47%); at 155 the higher-rated side wins 58.6% of them - but almost all of that lives in
the new theme (64.6%, n=48) against 53.2% (n=62) on the mature one, so it is either
population drift or 48-match noise. Gaps over 150 are still effectively unobserved (13
matches).

**Left in place, not changed.** A pre-registered challenger is now on record so the next
look is not another after-the-fact choice: **"+0.25 log-odds to the higher-rated side at
any nonzero gap"** - simpler, no band structure. It currently measures better than the
shipped step (rating-only +0.0170 at 3.1x SE, walk-forward 5/6) but was chosen after seeing
the band table, which is the exact sin that inflated the step at 95 rated matches. Decide
at ~250 rated matches, on this pre-registration.

### Four more shapes for the stat data, all null

Team balance / role dispersion; damage against opposing tanking; stats per rating point;
consistency (variance rather than mean). Best of them is stats-per-rating at 1.5x SE and
17/25 - short of the bar on both counts. Damage-vs-opposing-tanking, round 2's near-miss,
moved AWAY from the bar as data arrived, which is what noise does.

With this the stat data is comprehensively closed: roughly 16 shapes across three rounds,
none clearing the bar.

### Transfer, confirmed a second and third way

Both reviewers reproduced 33/70 = 47% cold. Two additions worth keeping: the **reverse**
direction is also null (fit the new theme, predict the old: -0.0017), so it is symmetric
rather than a property of one theme; and the failure is **not** roster mismatch - the old
fit knew 5.6 of the 6 heroes in an average new-theme match. The strengths are known and
uncorrelated with what wins. `CROSS_THEME_WEIGHT = 0.0` stands.

### Method trap: never stack a feature onto an in-sample BT eta

Fitting a logistic on [BT eta, new feature] scores catastrophically (~-0.05) because the
eta is in-sample on the training fold and the stacker inflates its coefficient. Those cells
say nothing about the feature. Same family as round 2's unestimable stacking weights.

### Literature, briefly

Both reviewers checked. Everything the field uses for draft prediction - pairwise
synergy/counter matrices, hero embeddings, factorisation machines, GNNs, attention over
draft order - is a 10k-to-1M-match method; published draft-only accuracy sits near 58%.
Hierarchical pooling across themes is the one idea that would transfer, and the decayed
prior experiment above is exactly that idea, falsified for this game. There is no technique
being withheld by ignorance; the constraint is 435 matches.

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
- **Theme influence is real, and the model is scoped per theme.** Briefly treated as
  nothing on 2026-07-28 - a theme was thought to apply modifiers hitting every hero
  equally - and reverted the next evening on two observations from the game itself:
  Aurora was pickable in a live Flourishing Wilds match while the Converging Paths roster
  snapshot had her banned, and the in-game Themes screen distinguishes "Standard terrain"
  from "Special terrain". Roster and map both change, so hero value does not carry.
  `CROSS_THEME_WEIGHT` went 0.35 then 0.0 on the operator's read of the first rotation -
  the borrowed evidence hurt more than the thin data it was meant to cushion. Another
  theme's matches are now DISCARDED, not discounted. The display gate counts the theme. The cost is accepted:
  the model is thin again at every rotation, which is the honest position when the thing
  being modelled genuinely changed.

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
