# Model findings ledger

What has been measured, by whom, on how much data, and what would re-open it.

The point of this file is that model questions get re-asked. Someone in three months will
wonder whether hero class matters, or whether the crowd should count - and without this
they will spend a day rediscovering that both were tested against 335 matches and lost.
Every entry says what would change the answer, because most of these are answers about a
sample size rather than about the game.

Started 2026-07-29. Append; do not rewrite history. A superseded row keeps its date and
gains a note.

## Do NOT test these again

Closed by at least two independent implementations, usually three (this project, Codex,
Fable), on the sample sizes shown. Re-opening one of these needs a REASON - a game change,
a new data source - not simply more matches. Detail for each is further down.

| route | why it is closed | last measured |
|---|---|---|
| **Carrying hero strengths across a theme rotation** | 47% directional cold, worse than a coin. Symmetric: null in the reverse direction too. Not a roster problem - the old fit knew 5.6 of 6 heroes. Hurts as a decayed prior at every k from 1 to 1000 | 2026-07-29, 365 vs 70 |
| **Rank-weighted hero popularity** | Correlation with fitted hero strength is 0.008 - it measures nothing. Its 61-match pass was luck; at 155 it is -0.0078, 6/25 | 2026-07-29, n=155 rated |
| **Plain hero popularity** | Bandwagon count, null throughout | n=335 |
| **Post-match stats (atk/heal/tank) in any shape** | ~16 variants across 3 rounds: raw, share, log, class-centred, outcome-adjusted, several shrinkages, team balance, damage-vs-opposing-tanking, stats-per-rating, consistency. None clears the bar; most dilute Bradley-Terry. Their variance is dominated by ROLE, and role composition is separately the worst family tested | 2026-07-29, n=435 |
| **Class / faction / race composition, and class-vs-class counters** | 0.707-0.744 logloss, 2-6 of 25 splits. Strongly negative, not merely null | n=335 |
| **Faction synergy** | +0.0020, 11/25 | n=335 |
| **Player identity / personal win-rate prior** | Worst single candidate. A player's spectated win rate does not even correlate with their ladder rating (r=-0.11) | n=101 players |
| **The crowd's betting split** | Uninformative in every form; a noisy echo of the rating gap (r=0.475). `W_CROWD = 0.0` | n=335 + 54 scored |
| **Rank as a corrective ON TOP of a confident hero call** | Where the two disagree the hero model wins 11-6. Re-weighting would overturn more correct calls than it rescues | 2026-07-29, n=453 |
| **Stacking a feature onto Bradley-Terry with a fitted weight** | Not a feature result - 435 matches cannot estimate stacking weights. Every stack scored worse than BT alone; the full stack scored worse than the base rate | rounds 2 and 3 |
| **Synergy matrices, hero embeddings, GNNs, attention over draft** | Literature methods needing 10k-1M matches for thousands of pair parameters. Published draft-only accuracy is ~58% anyway. Not an option at 435 | web research, both reviewers |

## Round 4, 2026-07-30, n=714 across two themes (634 predictions)

The first round run against the pre-registered list rather than against a fresh idea.
Walk-forward, refitting before every match, theme-locked, `predict` called with band
evidence exactly as the mixin calls it. Four triggers had fired: Flourishing Wilds at 345
of its own matches, 421 rated matches, 62 at a gap of 150 or more.

Three of the four came back null. The fourth is the only lead this round produced.

### Band-evidence damping: NULL, and the "74% vs 61%" does not survive a paired test

This was the file's highest-value open item. Weakening the damping does move the headline
numbers in the direction the earlier measurement suggested:

| BAND_PRIOR_STRENGTH | logloss | all | >=0.56 | >=0.62 |
|---|---|---|---|---|
| 20 (shipped) | 0.6770 | 361/634 = 57% | 136/204 = 67% | 48/72 = 67% |
| 100 | 0.6757 | 367/634 = 58% | 117/168 = 70% | 24/36 = 67% |
| 400 | 0.6758 | 370/634 = 58% | 95/130 = 73% | 21/30 = 70% |
| no damping | 0.6763 | 371/634 = 59% | 93/128 = 73% | 21/25 = 84% |

And then it evaporates under a PAIRED test on the same 634 matches, which is the only
fair comparison because each setting selects a different subset at any threshold:

| comparison | mean logloss difference | SE | t |
|---|---|---|---|
| shipped(20) vs weak(100) | +0.00126 | 0.00156 | **+0.81** |
| shipped(20) vs no damping | +0.00064 | 0.00320 | **+0.20** |

Nothing there. The apparent gain at a threshold is the same model making FEWER calls, not
better ones: 128 predictions clear 56% undamped against 204 shipped. Fewer, more selective
calls score higher per call while saying less overall - which is what a threshold table
rewards and a proper scoring rule does not.

**The selectivity itself is real for both settings.** Permuting outcomes 2,000 times and
rebuilding the table produced nothing as good as either, p = 0.000 both ways. So the model
does know when it knows; the damping is simply not what decides that.

**Verdict: no change. Closed.** Re-open only with a mechanism, not a bigger sample - the
paired difference is a fifth of its own standard error.

### CORRECTION, same day: two of the four claims above were WRONG

Caught by Codex and Fable, run independently against the harness and the model, and each
verified against the code before being accepted. **Read this section before believing the
two that follow it.** The band-evidence verdict survives; the rating verdicts do not.

Three defects, all in the harness rather than the model:

**1. `RATING_NUDGE` is doing two jobs, and changing it changed both.** It defines the
proposed rating curve AND the bins `band_evidence` tallies into. So a 25-band ramp did not
test a graded curve against a flat one - it tested "graded curve + evidence fragmented
into 25 starved bins" against "flat curve + two well-populated bins". Per-band shrinkage is
`seen/(seen+20)`, so bins holding 1-43 matches carry weights of 0.05-0.68 and inject noise
where the shipped two-bin version had a stable estimate. **This is a design defect in the
model, not only in the test:** the calibration bins should not be defined by the thing
being calibrated.

**2. The "no rating term at all" arm never deleted the rating term.** `((0, 0.0),)` with
evidence ON collapses every gap into band 0, and `blended_nudge` then refills the zero
prior from the observed higher-rated win rate at weight 384/(384+20) = 0.95. Measured
effective nudges: shipped 0.038/0.099 by band, "deleted" 0.038/0.091. It was the shipped
model wearing a different table, which is why it scored identically.

**3. The harness scoped rating evidence to one theme; production pools across themes.**
`solstice_clash.py:1861` passes every stored match and scopes by EVENT, because rating is
theme-agnostic. The harness passed theme-only history. Fixed.

### Corrected results - evidence handling held FIXED across variants

Prior-vs-prior, band evidence off on both sides, paired on the same 641 predictions.
Positive t favours the variant.

| variant | logloss | all | >=0.56 | paired t vs shipped |
|---|---|---|---|---|
| shipped step (flat above 100) | 0.6777 | 373/641 = 58% | 95/133 = 71% | - |
| **rating term genuinely DELETED** | 0.6825 | 363/641 = 57% | 74/103 = 72% | **-2.16** |
| challenger (0.0622 at any gap) | 0.6764 | 367/641 = 57% | 136/201 = 68% | +0.44 |
| graded (pre-registered) | 0.6765 | 372/641 = 58% | 108/150 = 72% | **+0.81** |
| fitted to the round-4 bands | 0.6740 | 371/641 = 58% | 156/236 = 66% | +0.79 |

**What actually changed:**

- **The rating term earns its place.** Deleting it is worse at t = -2.16 - the only result
  in this round that clears its own standard error. The uncorrected run claimed the
  opposite, on an arm that had not deleted anything.
- **The graded nudge is NOT worse.** It flips to t = +0.81, and the version fitted to the
  round-4 bands to +0.79. Both are still short of significance, so the honest verdict is
  **indistinguishable from flat at this sample size**, not dead. The earlier "every variant
  is worse" was the confound in 1 above.
- **Band evidence contributes nothing either way.** Prior-only against band evidence, on
  the corrected cross-theme scope: t = -0.06. So the round-4 damping conclusion stands, and
  for a cleaner reason than the one first given - there is no damping effect to weaken
  because the whole mechanism is inert at this sample size.
- With evidence ON, graded reads t = -0.75 and fitted t = -0.43. That is the shared-bins
  defect reappearing, and it is why every curve comparison from here on holds the evidence
  handling fixed.

**The lesson, which is the same one this file keeps learning:** an arm that scores exactly
like the control usually IS the control. Three variants agreeing to four decimal places
was the tell, and it was read as "the term does not matter" instead of "the patch did not
take".

### The rating step vs its challenger - SUPERSEDED, see the correction above

Kept for the record. The "no rating term" arm in this table did not delete the rating
term, so the conclusion drawn from it was wrong; the corrected table above replaces it.

The pre-registered challenger was "+0.25 log-odds to the higher-rated side at ANY nonzero
gap", no band structure.

| variant | logloss | all | >=0.56 | >=0.62 |
|---|---|---|---|---|
| shipped step (nothing under 100) | 0.6770 | 57% | 136/204 = 67% | 48/72 = 67% |
| challenger (any gap) | 0.6772 | 56% | 154/241 = 64% | 56/80 = 70% |
| no rating term at all | 0.6772 | 56% | 136/211 = 64% | 41/54 = 76% |

Identical to three decimal places. The challenger does not beat the incumbent, and neither
beats deleting the rating term. **Keep the shipped step** - not because it was shown to
work, but because nothing displaced it and it is the one already carrying the band
evidence. The honest position is that the rating term is worth approximately nothing at
the level it is currently applied, which the next entry explains.

### Large rating gaps: the one real finding - the effect is MONOTONIC

This one survived review intact, with the sample re-checked independently: 34/49 = 69% at
150-250 (p = 0.005) as the database grew, against the 34/48 = 71% first recorded.

| gap | n | model right | higher-rated side won | p |
|---|---|---|---|---|
| 0-50 | 141 | 56% | 52% | 0.368 |
| 50-100 | 112 | 59% | 58% | 0.054 |
| 100-150 | 74 | 64% | 61% | 0.040 |
| **150-250** | **48** | **73%** | **71%** | **0.003** |
| 250+ | 11 | 55% | 45% | 0.726 |

The pre-registered question was "does more gap mean more edge past 100". **It does**, and
the shipped table cannot express it: one flat +0.25 log-odds applies to everything above
100, so a 160-point gap and a 110-point gap are treated identically when the data says the
first is worth much more. Survives Bonferroni across the five bands.

The 250+ reversal is 11 matches and means nothing yet.

**This is a lead, NOT a change to ship.** The band boundaries above were read off the
result, which is exactly the selection error this file exists to prevent. The next round
tests a pre-registered graded nudge, and it is written into the table below before anyone
looks again.

The first attempt to measure that nudge was confounded - see the correction above. On the
corrected path it reads t = +0.81, meaning "not yet distinguishable from flat", so the
pre-registered test below stands unchanged and still needs matches it has never seen.

### The dissenting cell is gone

Round 3 recorded "a 100+ gap contradicting a confident call went 1/5". On the current path
that situation now arises exactly ONCE in 634 predictions, and the model was wrong. The
cell was an artefact of a weaker fit disagreeing with ratings more often; there is no
longer a disagreement to study.

## Round 5, 2026-07-30, n=732 (652 predictions) - Codex, independently

Asked the open question directly: is Bradley-Terry on hero identity still the only thing
that works, and do the OTHER recorded columns hold anything? Run by Codex against the
walk-forward harness and a read-only snapshot, theme-locked, refit before every match,
production `band_evidence`. Positive t favours the candidate.

| candidate | logloss | paired t | n | verdict |
|---|---|---|---|---|
| online out-of-sample calibration (fitted slope) | 0.67875 vs 0.67779 | -0.57 | 652 | NULL |
| fixed 2x log-odds calibration | 0.82884 vs 0.73655 | -1.49 | 18 | NULL, leaning dead |
| delete the fitted intercept | 0.68144 vs 0.67779 | -1.53 | 652 | **DEAD** |
| identification quality as a signal | - | - | 500 | NULL |

### Calibration is ANSWERED, and the answer is no

The trigger had fired at ~600 predictions. The under-confidence is real - the online slope
settles at 1.31, mean 1.34 over the last hundred - and **acting on it makes unseen-match
predictions worse.** The candidate produces more confident calls without producing better
ones, which is the same shape as every threshold-table result in this file.

The aggressive 2x variant was graded ONLY on the 18 predictions after round 4's frozen
634, because those 18 are the only observations that did not generate the hypothesis. It
lost badly. Small, but pointing the wrong way, and there is no case for re-opening.

### The intercept stays - and this settles the left-side skew

Deleting the fitted intercept RAISES raw directional accuracy, 376/652 against 365/652,
and destroys the confident tail:

| confidence | shipped | no intercept |
|---|---|---|
| >=56% | 138/207 | 122/202 |
| >=62% | **46/71** | **16/27** |

A cleaner demonstration than any argument that directional accuracy cannot overrule paired
logloss. The collector-dependent left-side skew (63% on our Flourishing Wilds matches, 34%
on another contributor's from the same theme, 50/50 on Converging Paths) does NOT justify
removing the intercept. Keep it fitted and theme-scoped; it is absorbing something real.

### Identification quality carries nothing

Correlation between the vision layer's confidence and the model's per-match error, across
500 predictions carrying scores: minimum margin -0.045, mean margin -0.035, minimum score
-0.063, mean score -0.020. All inside noise.

`runner_up_score` has ZERO usable coverage, so the score-minus-runner-up measure cannot be
tested at all until that column is populated. The `identified_by=image` versus legacy split
is chronologically confounded and is not evidence of anything.

Worth keeping in mind for the displayed INTERVAL - a comp read with poor margins arguably
deserves a wider one - but that is presentation, not a better forecast.

### Stats as a shrinkage target was NOT re-run, correctly

Round 2 already closed the exact experiment: gain -0.0000, SE 0.0006, 12/25 splits, with
instructions not to re-run below ~1,000 matches. The round-4 write-up called it untried,
which was wrong; the closure binds.

### Where this leaves the model

Nothing tested across five rounds has beaten regularised Bradley-Terry on hero identity
with the shipped rating path and a fitted intercept. That is now the answer to "is there
something else in the data" as well as "is there a better model" - the other recorded
columns have been looked at directly and they are empty.

## Re-open when the data arrives - pre-registered

Each of these is written down BEFORE looking again, so the next round is a test and not
another after-the-fact choice. That discipline exists because rank-weighted popularity
passed at 3.2x SE on 61 matches and turned out to measure nothing.

| route | trigger | what will be tested, decided in advance |
|---|---|---|
| ~~Band evidence in the prediction path~~ | ~~now~~ | **CLOSED round 4** - null on a paired test, t = +0.81. See round 4 |
| ~~The rating step vs its challenger~~ | ~~250 rated~~ | **CLOSED round 4** - challenger does not beat the incumbent; neither beats deleting the term |
| ~~Large rating gaps~~ | ~~30 at gap >= 150~~ | **ANSWERED round 4** - yes, monotonic to 250. Replaced by the graded-nudge entry below |
| **A GRADED rating nudge** | next round, once Flourishing Wilds holds ~450 of its own matches OR a third theme opens | Pre-registered NOW, before looking again: replace the flat `(100, 0.0622)` with `(150, 0.124), (100, 0.0622), (0, 0.0)` - double weight above 150, unchanged between 100 and 150. Decided on the round-4 monotonic result, so it must be confirmed on matches that result never saw. Judged on PAIRED logloss against the shipped step, not on a threshold table. A gain smaller than its own SE is a null, however good the >=56% row looks. **Hold the evidence handling FIXED across both arms** - `RATING_NUDGE` also defines the `band_evidence` bins, and letting it move re-partitions the estimator being compared |
| **Separate the nudge curve from the evidence bins** | before the graded test is run | `RATING_NUDGE` currently defines both the rating curve and the bins `band_evidence` tallies into, so no curve can be tested without disturbing its own calibration. Give `band_evidence` its own fixed coarse bins. This is a prerequisite, not an improvement - the graded test cannot be run cleanly in production code until it exists |
| **The confidence threshold** | Flourishing Wilds at ~200 of its own matches | Whether a line exists at all under the PRODUCTION path, and where. Validates itself as the new theme fills; no collection work needed. Round 4: selectivity confirmed real (permutation p = 0.000) but the specific line is still unearned |
| ~~Calibration / under-confidence~~ | ~~600 predictions~~ | **CLOSED round 5** - trigger fired, tested online and out of sample, t = -0.57. The under-confidence is real and acting on it is worse. See round 5 |
| **Bradley-Terry decay as the roster changes** | ongoing | The incumbent. Watch it does not rot |

Rating evidence is scoped per EVENT, not per theme, because a player's skill does not
change when the battlefield does. So unlike the hero model, that scoping survives a
rotation - which is why the rating entries above trigger on rated-match count rather than
on a theme boundary.

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

The theme-locked fit beats the cross-theme fit at every threshold that matters, which is
the transfer result from a different angle: the other theme's matches do not even help the
model pick its spots. Re-measured unfrozen at 453 matches, cross-theme at >=56% collapses
to 52% on Converging Paths against 78% theme-locked. `CROSS_THEME_WEIGHT = 0.0` is right
for a third independent reason.

### CORRECTION, same day: that table is NOT the production code path

Caught by Codex on review. The table above calls `predict()` **without** `evidence`. The
live path passes `_band_evidence` (`solstice_clash.py:1769`), which damps by how much
rating evidence each band carries - and that changes the result materially:

| at >=56%, walk-forward | production path (with band evidence) | the variant above (without) |
|---|---|---|
| Converging Paths | 23/36 = 64% | 21/27 = 78% |
| Flourishing Wilds | 17/30 = 57% | 8/12 = 67% |
| **pooled** | **40/66 = 61%** (95% CI 49-72) | 29/39 = 74% |

**So the honest figure for what actually ships today is ~61% on ~15-17% of matches, not
80%.** Under the production path the threshold does not survive its own selection audit:
permutation p = 0.246, and picking the threshold on one theme scores 53% on the other.

This is a straightforward measurement error on my part - benchmarking a call signature the
product does not use - and it is recorded rather than quietly fixed because the shape of the
error is instructive: **every experiment must call `predict` exactly as the mixin calls it,
band evidence included.** Added to the methodology traps below.

What survives the correction, and it is the interesting part: **the selective edge is
larger WITHOUT band-evidence damping than with it.** That makes "drop or weaken band
evidence in the prediction path" the single most promising open candidate in this file - it
is not a bug to fix but a hypothesis to test, since band evidence was added for a reason.
It measures 74% at >=56% on 39 matches, with a threshold-selection permutation p of 0.037.
Thin, and chosen after the fact. Pre-registered for the next round rather than shipped.

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

**Honest expectation, and what to tell a user:** on the code as it ships, **~61% correct on
the ~15% of matches that clear 56%** (95% CI 49-72). Both reviewers landed within a point
of each other on this once the production path was used. Not four in five; roughly three in
five, with the upper end of the interval reaching two in three. The 78-80% figure belongs to
the no-band-evidence variant and is not what the product does.

**Does the ladder rating add anything on top of a confident call? No.** Measured unfrozen
on all 453 matches, hero-only calls so the rating is genuinely external. At >=56% the calls
where the rating AGREES score 7/8 and where it DISAGREES 3/3 - the disagreement cell is if
anything better. And directly: across the 17 confident calls where the two contradict each
other, **the hero model is right 11 and the rating 6.** Re-weighting a confident call by
rank would overturn eleven correct calls to rescue six. Consistent with everything else
here: the rating is a weak signal and the hero model at high confidence is a stronger one.

One dissenting cell, logged because it is the only evidence anywhere for the opposite: when
the rating disagrees AND the gap is 100+, the model went 1/5. Five matches. Watch it.

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

## Hero-vs-hero counters, 2026-07-29 - two very different answers

The pairing question had never been asked directly. Class-vs-class counters died in round
1; hero-vs-hero was untested. It turns out the ANSWER DEPENDS ENTIRELY ON THE ESTIMATOR,
which is the most useful thing this round produced.

First, what the data can carry. 93 heroes is 4,278 possible pairs against 3,285 cross-pair
observations, and each observation is one win/loss bit shared across the nine pairs in that
match:

| pairs seen | Converging Paths | Flourishing Wilds |
|---|---|---|
| never | 55% | 83% |
| exactly once | 1,087 | 602 |
| 5 or more times | 42 | **0** |

The most-seen pair in 365 matches has met 8 times.

### FITTED counter models are dead, and not marginally

A rank-2k antisymmetric interaction - each hero gets a "threat" vector u and a
"vulnerability" vector v, counter effect u_i.v_j - u_j.v_i, so 186 parameters at k=1
instead of 4,278, and a hero can counter one it has never faced. Fitted jointly with the
strengths so it could not be handed an in-sample eta. Swept over prior width and rank:

| counter term size (mean abs) | vs shipped BT |
|---|---|
| 0.00 (crushed by the prior) | identical to BT |
| 0.65 | -0.0279, 6/25 splits |
| 2.22 | -0.5583 |
| 8.93 | -3.7334 |

There is no setting where it helps. It is inert while the prior holds it at zero and
destroys the model in a straight line the moment it is free to speak - the signature of
pure overfitting, not of a weak signal. Rank 2 behaves identically to rank 1. **Do not fit
a counter term at this sample size.**

### The observed-tally estimator, after both reviewers attacked it

Reviewed the same day. The verdict is neither "works" nor "dead" - it is SMALL, and the
two reviewers disagreed in a way that is itself the finding.

**They contradict each other on the central test, because they swept different settings:**

| 25 shuffle splits, Converging Paths | |
|---|---|
| Fable, unfloored (k=1, w=0.25) | -0.0054, 8/25 - fails badly |
| Codex, floored (min_n=3, k=5, w=0.25) | +0.00093, 19/25 |

So "fails the bar" applies to the version already known to be unsafe on a fresh theme, not
to the floored one. Both numbers are right.

**Three corrections to what is written above, all mine:**

1. **"All 30 settings gain" was one observation, not thirty.** Per-match gains across
   settings correlate +0.967 to +0.995 - the sweep re-scales a single noisy quantity. It
   read as robustness and was a single bet counted thirty times.
2. **"The pair version gains twice the per-hero control" does not hold at matched
   parameters.** Codex measured the reverse on Converging Paths: hero control +0.00374
   (22/25) against the pair layer's +0.00093 (19/25). My comparison used different settings
   for the two. The per-hero version does collapse on the fresh theme (-0.0047) where the
   pair version stays flat, so the floor is preventing damage rather than revealing
   counters.
3. **The shuffle SE is optimistic here, as this file already warns.** Codex's +4.67 SE comes
   from 25 correlated resamples of the same 365 matches. The per-match paired SE on
   genuinely held-out data is **0.8 SE**: +0.0065 +- 0.0077 over the 92 second-half matches
   where the layer fired, helping 52 and hurting 40 (P = 0.126).

**No counter structure is detectable beyond Bradley-Terry.** Direct existence test, no
layering formula involved: if counters are real, pair records must be more SPREAD than BT
alone implies. Null simulated by redrawing outcomes from BT's own per-match probabilities,
so the nine-pairs-per-match correlation and uneven pair frequencies are present in the null
too. Converging Paths, 118 pairs: observed 112.7 against null 117.4 +- 14.1, **z = -0.33**,
p = 0.625. Not merely non-significant - the observed spread is slightly BELOW chance. The
power of this test at 365 matches is under review; "no trace at this sample size" is not
"proven absent", and Bradley-Terry itself looked dead at 245.

**What the gain probably is, since it is probably not counters.** Both reviewers found it
concentrated LATE in the theme (blocks: -0.0017, -0.0010, +0.0044) and found recency
weighting the strongest thing in the whole sweep. That points at within-theme
nonstationarity - recent matches predicting better than old ones - which a pair tally picks
up incidentally because recent pairs dominate a sparse record. **Time-decayed Bradley-Terry
is the direct attack on that: one parameter, shipped machinery, no 4,000 sparse cells.**
Registered as the more promising lead.

**Tail accuracy does not improve**, which is what the product actually shows. On the mature
theme the layer pushes far more matches above the display threshold at the same ~64%
accuracy - it manufactures confidence without manufacturing accuracy. On the fresh theme it
slightly worsens the threshold table.

**Status: not shipped, not discarded.** The case for a forward test is that the downside is
bounded and measured - on the live theme it fires on 11 matches and scores +0.0003, because
almost nothing has 3 matchups yet - so the realistic outcomes are "small gain" or "nothing"
rather than "small gain" or "regression". Pre-registered settings if it is ever wired in:
theme-scoped, cross-side raw tally, prior matches only, `min_n=3`, shrink `n/(n+5)`,
`weight=0.25`, `eta = logit(BT) + weight * sum(pair_scores)`. Acceptance: positive paired
logloss delta and non-negative accuracy at >=0.55 and >=0.60; disable if the first 75 scored
matches on a theme are worse by more than 0.001. No tuning on that theme, ever.

### The OBSERVED-TALLY estimator is the round's one positive result

Nothing fitted: each pair carries its own record, the nine are summed, the sum is added to
the Bradley-Terry log-odds. Damped per pair by n/(n+k) so a pair seen once cannot shout
down one seen eight times. Walk-forward on Converging Paths, tallies built only from
earlier matches:

| weight | BT + PAIR tally | BT + HERO tally (the control) |
|---|---|---|
| 0.05 | +0.0030 | +0.0010 |
| 0.10 | +0.0051 | +0.0018 |
| 0.25 | **+0.0064** | +0.0027 |
| 0.50 | -0.0071 | -0.0006 |

**The control is what makes this interesting.** Both reviewers predicted the tally would
merely re-measure team strength - all nine pairs move together, so a weak hero on a strong
team goes positive against everyone - and that objection is right in principle. The test
for it is to run the identical estimator on per-HERO records instead of per-pair. The pair
version gains about TWICE the hero version at every weight, so the pairing structure is
carrying something beyond team strength. For scale, Bradley-Terry's own edge is +0.006 to
+0.010, so this is the same order of magnitude.

**Not established, and the reasons are the usual ones.** The weight and the damping were
swept and the best cell reported - the exact procedure that made rank-weighted popularity
look like a 3.2x-SE winner before it turned out to measure nothing. The optimum is narrow
(good at 0.25, harmful at 0.50). One theme, n=245. It has NOT been through the bar.

Pre-registered for the next round, before looking again: k and weight fixed at k=3,
weight=0.10 - deliberately NOT the winning cell, since a value chosen off this sweep cannot
also test it - then 25 shuffle splits with a paired SE, temporal blocks, and a replication
on Flourishing Wilds. Report accuracy at thresholds as well as logloss.

The `hero_matchup` view (`data/solstice_clash/views.sql`) exposes exactly the per-pair
record this estimator reads, so the next round needs no new plumbing.

## The pair programme is closed - all six shapes, 2026-07-29

Six estimators over hero pairings, three rounds, two independent implementations. All null.
Recorded together because the pattern matters more than any single result: the failures are
not about which pairing or which estimator, they are about a few hundred matches per theme
against thousands of possible parameters.

| shape | verdict |
|---|---|
| cross-side (counter) raw tallies | gains on one half of one theme; fails shuffle splits, temporal blocks and cross-half consistency |
| fitted low-rank antisymmetric counters | inert at zero, monotonically destructive once free |
| same-side (synergy) raw tallies | negative on the mature theme's held-out half AND on the live theme |
| **fitted same-side pair terms** | **the sweep chooses `sigma_pair = 0`** - see below |
| depth-restricted variants (n>=5, n>=8) | numerically zero: depth and coverage cannot both be had |
| hero-vs-class pooling | same half-flip signature; negative on the live theme at every setting |
| whole-comp (3-hero) terms | impossible, not thin - see below |

### The fitted version, which was the operator's own framing

"Like BT but for pairs" - same-side pair terms added to the design matrix and fitted jointly
with the hero strengths under the same regularised likelihood. Genuinely distinct from the
raw tally, and never tested before this round.

Degeneracy verified first: at `sigma_pair -> 0` the model reproduces the shipped one to
1e-16, so the null is exact. Then:

| sigma_pair | gain vs shipped, mature held-out half |
|---|---|
| 0 | 0.000000 |
| 0.02 | -0.000005 |
| 0.10 | -0.000134 |
| 0.50 | -0.003639 |
| 0.80 | -0.008604 |

Monotonically worse on both themes. 25 shuffle splits at the best non-zero setting:
+0.000001, which is numerical dust. **The control settles it: assigning the pair terms to
RANDOM hero pairs that never played together behaves identically** (-0.000002 to -0.000013),
so even the dust is not about pair identity.

**Left to itself the model refuses the pair terms.** That is the cleanest statement of the
result: not "we tuned it badly", but "the likelihood prefers zero".

### No combination is nameable, which was the operator's actual question

Strongest fitted effects on the mature theme:

| pair | times played | gamma |
|---|---|---|
| indris + lorsan | 7 | +0.0008 |
| galahad + phraesto | 4 | +0.0007 |
| indris + thoran | 4 | -0.0007 |
| lorsan + nazrik | 5 | -0.0007 |

Two things make these unusable. They sit on pairs seen 4-7 times; and 0.0008 log-odds is
about **0.02 percentage points** on a prediction, against hundreds of times that for a
single hero's strength. The permutation check finishes it: a shuffled-outcome null produced
a spread of 0.001628 against the observed 0.001548 - the extremes are not merely
insignificant, they are smaller than chance produces.

Carrying the mature theme's gammas to the live theme alone: logloss 0.6931, accuracy 55.6%,
and **zero calls clearing 54%**. A 50/50 model.

### Whole comps: impossible, and worth stating as such

| | Converging Paths | Flourishing Wilds |
|---|---|---|
| comps observed | 730 | 238 |
| distinct comps | 717 | 238 |
| seen 3+ times | 0 | 0 |
| most-seen | 2 | 1 |

Every comp on the live theme is unique. There is nothing to average, at any sample size
reachable inside a three-day theme.

### Same-side pair density, for whoever re-opens this

Same-side pairs are THINNER than the cross-side ones that already failed - six per match
against nine:

| pairs seen | same-side (CP) | cross-side (CP) |
|---|---|---|
| 3+ times | 166 | 343 |
| 5+ times | 17 | 42 |
| 10+ times | 0 | - |

Re-open at ~1,000 matches per theme, not before. That is roughly three contributors
collecting continuously, and it is the only thing that changes the answer.

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
- **Calling the shipped function is not enough - call it with the SHIPPED ARGUMENTS.**
  Omitting `evidence` from `predict` produced a 78% headline for a code path that does not
  exist; the production call (`solstice_clash.py:1769`, band evidence included) gives 61%.
  Same function, same data, a claim inflated by 17 points. Copy the mixin's call site.
- **Selection is a measurement, so audit it.** Any threshold, band edge or cutoff chosen by
  looking at results needs a permutation test of the SELECTION PROCEDURE, not of the chosen
  value. Fixed >=56% permutes at p=0.0025; the procedure that picked it permutes at 0.246.
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
