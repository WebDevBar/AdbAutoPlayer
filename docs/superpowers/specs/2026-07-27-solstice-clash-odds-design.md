# Solstice Clash Live Odds (Mode C) - Design

**Status:** design, awaiting review
**Date:** 2026-07-27
**Depends on:** Mode A (`2026-07-26-solstice-clash-phase2-design.md`), which collects the match data this model is fitted on.

---

## 1. Goal

While spectating a Solstice Clash draft, show the player an estimate of which side is
likely to win, updating as picks land, in time to place a bet before betting closes.

**The mode never bets.** It shows a number and its uncertainty; the player decides. This is
a hard constraint, not a v1 simplification.

`unknown` is a first-class output. When the data cannot support a call, the display says so
rather than producing a number, and the correct response is to sit the round out.

## 2. What we are predicting, precisely

`P(left wins | the match produces a decisive result)`.

Not `P(left wins)`. Draws produce no result screen, so Mode A detects them by the game
returning to the overworld and skips them - nothing about a drawn match is recorded, not
even that it happened. The data is therefore missing not at random: comps that tend to
stall are systematically absent.

Codex was explicit that there is no cheap correction available while drawn matches are
unobservable. Two honest options:

1. **Document the conditional** and move on. This is the v1 choice.
2. **Record draws with their comps** (Mode A already captures the prematch screen, so the
   comps are known before the match resolves) and fit a three-outcome model:
   ```
   s_left = +η,  s_right = -η,  s_draw = κ + δ_left + δ_right
   P(outcome) = exp(s_outcome) / Σ exp(s)
   ```
   This is the better model and is recorded here as the first follow-up, not v1 scope.

The distinction matters for a bettor: a comp that draws often is not a comp that wins often,
and v1 cannot tell them apart.

## 3. Model

Regularised Bradley-Terry with additive hero strengths and per-player skill.

For match `i`, with `x_ih = +1` if hero `h` is on the left, `-1` if on the right, `0`
otherwise:

```
η_i = β0 + Σ_h x_ih θ_h + φ_(left player, i) - φ_(right player, i)

P(left wins) = sigmoid(η_i) = 1 / (1 + exp(-η_i))
```

Fitted by minimising the penalised negative log likelihood:

```
L = Σ_i [ log(1 + exp(η_i)) - y_i η_i ]
  + (1 / 2σ_θ²) Σ_h θ_h²
  + (1 / 2σ_φ²) Σ_p φ_p²
  + (1 / 2σ_β²) β0²
```

with gradients

```
p_i = sigmoid(η_i)
∂L/∂β0  = Σ_i (p_i - y_i) + β0 / σ_β²
∂L/∂θ_h = Σ_i x_ih (p_i - y_i) + θ_h / σ_θ²
∂L/∂φ_p = Σ_i s_ip (p_i - y_i) + φ_p / σ_φ²      s_ip = +1 left, -1 right, 0 otherwise
```

### Priors

| parameter | prior SD | reasoning |
|---|---|---|
| `θ_h` hero | **0.30** | 95% prior range about ±0.60 log-odds, an odds multiplier of 0.55 to 1.82 for one hero. Generous at a few hundred matches. |
| `φ_p` player | **0.50** | A player controls their whole side, but OCR name errors and spectator noise make a large player prior dangerous. A three-hero side has prior SD `√3 × 0.30 = 0.52`, so 0.50 makes player skill comparable to the entire draft rather than dominant. |
| `β0` intercept | **1.0** | Absorbs any left/right structural advantage. |

Fit with `scipy.optimize.minimize(method="L-BFGS-B")`. About 95 hero parameters plus one per
player plus an intercept - small, and refitting takes well under a second.

**Unregularised logistic regression is forbidden here.** With ~95 heroes and a few hundred
matches it will invent large strengths for heroes seen once or twice.

### Why not the alternatives

- **Exact comp-versus-comp lookup**: C(95,3) is about 138,000 comps, so roughly 1.9e10
  possible matchups. The same matchup will essentially never recur. Rejected.
- **Hero-pair synergy or counter terms**: thousands of parameters, needing many thousands of
  matches per theme. Statistically hopeless at this sample size. Rejected for v1.
- **Raw per-hero win rates**: a hero with one win reads as 100%. Unusable as a model, though
  retained as a diagnostic display.

## 4. Cold start

An unseen hero has `θ_h = 0` and contributes average strength. This falls out of the prior
rather than being special-cased.

A hero that wins its only match moves very little. With `σ_θ = 0.30`, `σ_θ² = 0.09`, and a
maximum single-match residual near 0.5:

```
θ_h ≈ 0.09 × 0.5 ≈ 0.045
```

which is negligible, as it should be. The same logic applies to players: one or two
appearances leave `φ_p` essentially at the prior.

Player terms become informative at roughly **4 appearances** and worth displaying at **8 to
10**.

## 5. Partial comps

Betting closes before the 6th pick locks, so odds must be computed from 1 to 5 known picks.
Pick order is known: left takes slots 1, 4, 5 and right takes 2, 3, 6.

**Unknown slots contribute zero to the mean and widen the interval.**

```
η_partial = β0 + Σ_locked_left θ_h - Σ_locked_right θ_h + φ_left - φ_right

Var(η) = z' H⁻¹ z + n_unknown × σ_pick²        σ_pick = σ_θ = 0.30
```

So four unknown picks add `√(4 × 0.09) = 0.60` to the standard error.

**This deliberately rejects imputing from the visible draft pool.** Codex initially proposed
sampling unknown picks from the visible remaining heroes, then retracted it on learning our
constraint: while spectating, only 15 of the 20 offered heroes are on screen - the betting
panel covers the fourth row - and Mode A does not read the grid at all because a partial
view is a biased sample. Imputing from a biased pool adds directional bias while *pretending*
to reduce uncertainty. Zero mean with a wider interval is the honest choice, and being vague
beats being wrong.

## 6. Confidence

Laplace approximation around the fitted parameters.

```
H = Xᵀ W X + Λ         W_ii = p_i (1 - p_i),  Λ = diag(1/σ_β², 1/σ_θ², …, 1/σ_φ², …)
SE(η) = sqrt( zᵀ H⁻¹ z + n_unknown σ_pick² )
```

Report an **80% interval**, not 95%: at this sample size a 95% interval is so wide that a
user learns to ignore it.

```
p_low = sigmoid(η - 1.28 SE),  p_mid = sigmoid(η),  p_high = sigmoid(η + 1.28 SE)
```

Trust label from `SE(η)`:

| SE | label |
|---|---|
| < 0.25 | high |
| 0.25 - 0.60 | medium |
| > 0.60 | low |

Most predictions will be low or medium for the first theme window. That is the data being
honest, not a defect.

Alongside the number, show the evidence behind it: total appearances across the locked
heroes, and the weakest single hero's appearance count. A 54% built on one appearance must
not look like a 54% built on two hundred.

## 7. The display gate

**No numeric recommendation is shown unless all of these hold:**

```
decisive matches this theme        >= 75
min appearances among locked heroes >= 5
SE(η)                              <= 0.45
|η|                                >= 0.35        (p at least 0.586 or at most 0.414)
```

and the model must beat **both** baselines out of sample (section 8). Below the gate the
display reads `not enough data`, which is the honest answer and the cue to skip the round.

Hero-level strengths are shown under a stricter gate: `appearances >= 10` and
`SE(θ_h) <= 0.30`.

### What this means in practice, stated plainly

At the time of writing we have **11 matches with a median of 1 appearance per hero**, so the
display will read `not enough data` and will keep doing so for some time. Reaching 5
appearances for each of ~95 heroes needs on the order of **200 matches**; at the measured
~3 minutes per collection cycle that is roughly **10 hours**, or one overnight run.

The gate is deliberately strict because the failure mode it prevents - a confident-looking
number built on almost nothing - is worse than showing nothing. If the user prefers earlier,
noisier numbers, the thresholds are the knob to turn, and they are configuration rather than
code.

## 8. Validation

The model is only trusted once it demonstrably beats guessing.

```
logloss_i  = -[ y_i log p_i + (1-y_i) log(1-p_i) ]
Brier      = mean( (p_i - y_i)² )
```

Three-way comparison, all evaluated out of sample:

| | prediction |
|---|---|
| Baseline A | `p = 0.5` (log loss 0.6931, Brier 0.25) |
| Baseline B | `p = training left-win rate` |
| Model | regularised BT above |

**Beating Baseline A is not sufficient.** If the left side has any structural advantage, a
model can beat 50/50 while learning nothing about heroes. Baseline B is the real bar.

Procedure: repeated shuffle validation, 100 repeats of an 80/20 split, refitting each time;
report the mean and spread of test log loss.

Interpretation:

| test log loss | reading |
|---|---|
| > 0.693 | worse than guessing |
| ~0.690 | noise |
| ~0.675 | weak but real |
| ~0.650 | meaningful signal |

Useful means: below both baselines by at least 0.01 to 0.02, stably across splits.

## 9. Scope: one theme at a time

Hero balance and the map change with the theme, so parameters are fitted **per theme** and
reset when the theme rotates. `Converging Paths` rotates 2026-07-28; the next themes are
`Flourishing Wilds` and `Tactical Grounds`, which share identical rules and differ only in
standard versus special terrain.

Cross-theme memory is a possible later refinement - carrying the previous theme's estimates
as a weak prior, `θ_current ~ N(ρ θ_previous, σ_theme²)` with `ρ` around 0.25 - but v1 fits
each theme from scratch.

## 10. Components

| file | responsibility |
|---|---|
| `services/solstice/odds.py` | **new**, pure. Fit the model from match rows; predict from a partial draft. No device, no UI. |
| `services/solstice/store.py` | **modify**. Read matches for fitting; persist fitted parameters. |
| `data/solstice_clash/schema.sql` + `migrate.py` | **modify**. Tables for fitted parameters and logged predictions. |
| `mixins/solstice_clash.py` | **modify**. During the draft, read locked picks, call the predictor, log the result. |
| frontend panel | **new**. Displays the current estimate, interval, trust label and evidence. |

Everything statistical is pure and testable against fixture match data with no device and no
GUI, in the same shape as Mode A's services.

## 11. Display

Two surfaces, both in the existing AdbAutoPlayer UI:

1. **Live log** - the mode already logs there, so each recomputation writes one line. Nearly
   free, and gives a scrollback of how the estimate moved as picks landed.
2. **Panel** - the current estimate, its interval, the trust label, and the evidence counts.

Example content:

```
Left win: 54%   (80% interval 45-63%)
Trust: low
Evidence: 17 appearances across locked heroes, weakest 1
```

Never the bare `54%`.

## 12. Prediction logging

Every computed estimate is written to the database with the draft state it was computed
from, the resulting probability and SE, and later the actual outcome. This is what makes
section 8's validation possible on live predictions rather than only on refits, and it costs
nothing to record.

## 13. Open items

- **Draw handling** (section 2) - v1 documents the conditional; recording draws with comps
  and fitting the three-outcome model is the first follow-up.
- **Player-name reliability** - names come from OCR. Mode A saw a name read as `GAME` when
  the account badge overlapped it. A misread name creates a phantom player, splitting one
  player's history in two. Needs a normalisation and probably a minimum-length sanity check
  before player terms are trusted.
- **Whether player terms actually reduce selection bias** or merely absorb variance. Codex
  was asked directly and its answer should be treated as a hypothesis to test, not settled:
  compare fitted `θ` with and without player terms once enough data exists.
- **Reading the draft live** - Mode C must read locked picks from the spectate draft screen.
  Mode A registered `spectate_draft_picks` cell geometry but its accuracy on that screen was
  never validated, because cross-screen training was reduced in scope. That validation is a
  prerequisite for this mode, not an afterthought.
