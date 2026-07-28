# Solstice Clash Live Odds (Mode C) - Design

**Status:** design, awaiting review
**Date:** 2026-07-27
**Depends on:** Mode A (`2026-07-26-solstice-clash-phase2-design.md`), which collects the match data this model is fitted on.

---

## 0. Prerequisites - these block implementation

Two things must be settled before any of the work below is worth doing. They are stated here
rather than in "open items" because an engineer who treats them as follow-ups will build a
mode that cannot run.

**P1. The spectate draft screen must be proven readable.** This mode's entire input is the set
of locked picks read from the spectate draft screen. Mode A registered `spectate_draft_picks`
cell geometry but **never validated its accuracy on that screen** - cross-screen training was
cut from Mode A's scope. Until that is measured, nothing the mode computes or displays can be
trusted to have correct inputs.

Order of work: capture draft-screen frames as fixtures, measure identification accuracy
against the same accept rule Mode A uses (`score >= 0.70 and margin >= 0.10`), and only then
one device smoke test. If accuracy on that screen is materially worse than on the summary
screen, the geometry must be retuned before the model is wired in - a wrong hero read produces
a confident wrong number, which is the exact failure this design exists to avoid.

**P2. Player identity handling (section 9) ships with the first fit, not after it.** Player
terms are part of the model from the start, so the vetting rules must exist before any
parameters are fitted. Fitting first and adding vetting later means the first fitted `φ` values
are built on merged phantom players.

### Testing boundary

These prerequisites are the only device-dependent part of this design. Everything statistical -
fitting, marginalisation, variance, validation, the gate - is pure and must be testable on
fixture match data with no device and no GUI. Screen-reading validation and statistical tests
never share a test path.

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

### Fitting: Newton-Raphson in pure numpy, no scipy

**scipy is not available and must not be added.** Verified against this repo on 2026-07-27:
`numpy>=2.4.6,<2.5` is declared in `src-tauri/pyproject.toml` and bundled in
`src-tauri/pyembed/.../site-packages/numpy`, while scipy appears in neither `uv.lock` nor the
bundled runtime. Adding it would be a dependency change requiring approval and would grow the
shipped bundle substantially, for a problem that does not need it.

Use Newton-Raphson, which suits this problem better than L-BFGS anyway. With the ridge penalty
the objective is strictly convex, the parameter count is small (~95 heroes plus players plus an
intercept), and **the Hessian is needed regardless** for the Laplace interval in section 6 - so
Newton computes the confidence machinery as a side effect of fitting rather than as extra work.

```
repeat:
    p = sigmoid(X @ b)
    g = X.T @ (p - y) + Lam * b                 gradient
    W = p * (1 - p)
    H = (X.T * W) @ X + diag(Lam)               Hessian, symmetric positive definite
    b = b - solve(H, g)
until max(abs(g)) < 1e-8 or 100 iterations
```

`H` is symmetric positive definite because `XᵀWX` is positive semi-definite and `Λ` is strictly
positive, so the solve is always well posed - which is also what makes section 6's `zᵀH⁻¹z`
safe to compute. Convergence is typically 5 to 8 iterations and the whole fit takes
milliseconds.

Never form `H⁻¹` explicitly. Use `numpy.linalg.solve(H, g)` when fitting and
`zᵀ · numpy.linalg.solve(H, z)` for the SE.

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

Var_total = zᵀ H⁻¹ z + n_unknown × σ_pick²        σ_pick = σ_θ = 0.30
```

So four unknown picks add `√(4 × 0.09) = 0.60` to the standard error.

**The reported probability is not `sigmoid(η_partial)`.** What the display must show is
`P(left wins | locked picks)`, which marginalises over the unknown remainder:

```
p_reported = E_U[ sigmoid(η_partial + U) ]
```

Because `sigmoid` is concave above zero and convex below, `sigmoid(E[η]) ≠ E[sigmoid(η)]` -
using the point value would overstate confidence whenever the draft is incomplete, which is
exactly when the display is used. Use the standard probit approximation to the logistic-normal
integral:

```
p_reported = sigmoid( η_partial / sqrt(1 + π × Var_total / 8) )
```

This shrinks the estimate toward 0.5 in proportion to how much is unknown, which is the
desired behaviour. With `η_partial = 0.8` and four picks outstanding
(`Var_total ≈ 0.36 + 0.05 = 0.41`):

```
sigmoid(0.8) = 0.690          naive, overconfident
sigmoid(0.8 / sqrt(1 + 0.161)) = sigmoid(0.743) = 0.678
```

The interval endpoints are computed on the log-odds scale and then squashed
(section 6), so they use `sqrt(Var_total)` directly rather than the shrunk value.

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

**`z` is the prediction design vector**, laid out in the same parameter order as `H`, and it
must carry every term the mean carries or the interval will be inconsistent with the point
estimate:

| component | value |
|---|---|
| intercept slot | `1` |
| hero slot `h` | `+1` locked left, `-1` locked right, `0` otherwise |
| player slot `p` | `+1` if `p` is the left player, `-1` if the right player, `0` otherwise |

**Unseen players and unseen heroes are not simply dropped.** A player or hero absent from the
fit has no row in `H`, so `zᵀH⁻¹z` cannot account for it. Its mean contribution is `0`, and
its variance contribution is the prior:

```
Var_total += σ_φ²  per unseen player present in the match     (0.25)
Var_total += σ_θ²  per unseen hero already locked             (0.09)
```

Omitting these would report a narrow interval precisely when the model knows least about the
participants.

Report an **80% interval**, not 95%: at this sample size a 95% interval is so wide that a
user learns to ignore it.

```
SE       = sqrt(Var_total)
p_low    = sigmoid(η - 1.28 SE)
p_mid    = sigmoid(η / sqrt(1 + π SE² / 8))      marginalised, per section 5
p_high   = sigmoid(η + 1.28 SE)
```

`p_mid` is the marginalised estimate from section 5, not `sigmoid(η)`. It always lies between
`p_low` and `p_high`, since shrinking `η` toward zero moves the point inside an interval that
is centred on `η`.

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

plus the **validation gate**, stated as an executable condition over the 100 shuffle splits of
section 8:

```
mean(logloss_model) <= mean(logloss_baselineA) - 0.01
mean(logloss_model) <= mean(logloss_baselineB) - 0.01
logloss_model < logloss_baselineB in at least 80 of the 100 splits
```

Both a mean margin and a win rate across splits are required: a mean margin alone can be
produced by a handful of lucky splits, and a win rate alone can be produced by margins too
small to matter.

`|η| >= 0.35` is evaluated on the **displayed** `p_mid`, not on raw `η`, so the gate and the
display cannot disagree. Equivalently: `p_mid <= 0.414 or p_mid >= 0.586`.

Below the gate the display reads `not enough data`, which is the honest answer and the cue to
skip the round.

### The gate makes early-draft display impossible, by construction

Unknown picks alone put a floor under `SE`, before any parameter uncertainty is added:

| unknown picks | floor on SE = `√(n × 0.09)` | can `SE <= 0.45` hold? |
|---|---|---|
| 0 | 0.00 | yes |
| 1 | 0.30 | yes |
| 2 | 0.42 | barely, and only with near-zero parameter uncertainty |
| 3 | 0.52 | **no** |
| 4 | 0.60 | **no** |
| 5 | 0.67 | **no** |

So a number can only ever appear with **at least four of the six picks locked**, and at three
unknown picks or more the display is guaranteed to read `not enough data` no matter how much
history has been collected.

This is a deliberate consequence rather than an oversight, and it is stated here because it
directly limits how early the player gets a signal: reaction time is the whole point of the
feature, and this gate spends some of it. Whether the useful window - between the fourth pick
landing and betting closing - is long enough to act on is a question the first live run
answers, not the spec. If it is too tight, the honest lever is `SE`, not the marginalisation:
raising the ceiling to 0.55 admits three unknown picks and costs precision, and that is a
trade for the user to make with real timings in hand.

Hero-level strengths are shown under a stricter gate: `appearances >= 10` and
`SE(θ_h) <= 0.30`.

### What this means in practice, stated plainly

At the time of writing we have **13 matches, 48 distinct heroes, and a best-covered hero at 4
appearances**, so the display will read `not enough data` and will keep doing so for some
time. Reaching 5 appearances for each of ~95 heroes needs on the order of **200 matches**.
The collection rate measured over those 13 matches is **5.5 minutes per match** in steady
state, excluding two idle gaps, so 200 matches is roughly **18 hours** of uninterrupted
collection - not the 10 hours a 3-minute cycle would imply. That fits inside the remaining
`Converging Paths` window only if collection runs close to continuously.

The gate is deliberately strict because the failure mode it prevents - a confident-looking
number built on almost nothing - is worse than showing nothing. If the user prefers earlier,
noisier numbers, the thresholds are the knob to turn, and they are configuration rather than
code.

### Which rows are eligible to train on

Not every row in the database belongs in the fit. The training query is:

```sql
SELECT ... FROM match
WHERE outcome IN ('left','right')
  AND theme_id = :theme
  AND (theme_resolved_by IS NULL OR theme_resolved_by != 'default')
```

Two conditions, both load-bearing:

- **Decisive only.** Draws carry no comps at all (section 2).
- **Not default-resolved.** A row stamped `theme_resolved_by = 'default'` is one where no dated
  window covered its capture and the OCR name did not match either, so which balance patch it
  belongs to is genuinely unknown. Since parameters are fitted per theme, pooling those rows
  mixes generating processes. They are kept rather than discarded because filling in the window
  later promotes them.

**The `IS NULL` branch is not optional.** `theme_resolved_by` is NULL on every locally-collected
row until a sync round-trip sets it, and in SQL `NULL != 'default'` evaluates to NULL rather than
true - so the naive filter drops every unpushed local match. Verified against the live database
on 2026-07-27: the naive form kept **0 of 32** rows; the NULL-safe form kept all 32. A model
trained on an empty set is indistinguishable from a model that found no signal, which is exactly
the failure this spec's gates are meant to make impossible.

This requirement originates in the sync design
(`gameretro-adb-api/docs/superpowers/specs/2026-07-27-sync-api-design.md`) and is restated here
because this is the document whose implementer writes the query.

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

The exact pass condition that opens the display gate is stated in section 7 and is not
restated here, so there is one definition of it.

**The gate is decided by this refit procedure on historical matches, not by live predictions.**
Section 13's logged live predictions are a monitoring surface: they confirm after the fact
that live behaviour matches the refit estimate, and they are the input to any later
recalibration. They are not what first opens the gate, because before the gate opens no
prediction is shown and the mode would otherwise never bootstrap.

## 9. Player identity safety

Player identity is a fitted parameter, so a wrong identity is a wrong model, not a cosmetic
defect. Names come from OCR and are demonstrably unreliable.

**Raw OCR text must never be used as a player key.** Two distinct failure modes, both observed
in real collected data:

- **Splitting** - one player read two ways becomes two players, each with half a history.
- **Merging** - and this is the dangerous one - several players read the same way become one
  phantom player whose `φ_p` absorbs all of their records. In the manual match data, every
  name overlapped by the account badge truncated to `GAME`. In the collected matches, a name
  is stored as `【kru`, which is plainly a partial read. Any two players sharing a truncated
  prefix would silently merge.

Merging is worse than splitting: splitting weakens a term toward its prior, whereas merging
manufactures a confident term from unrelated matches.

### Rules

`identity.py` maps a raw OCR string to a trusted key or to `None`:

1. Normalise: Unicode NFKC, strip surrounding whitespace and control characters.
2. Reject as untrusted if any of:
   - fewer than 3 characters after normalisation;
   - the string is a strict prefix of a different observed name (suspected truncation);
   - the string is on the known-bad list, which starts with `GAME`.
3. Otherwise return the normalised string as the key.

### What an untrusted name means

An untrusted name is **not** a new player and **not** guessed. That match contributes its
hero information normally and its player term is simply absent:

- **When fitting** - the row is included, with no `φ` term on that side. The hero terms still
  learn from it.
- **When predicting** - `φ = 0` for that side and `Var_total += σ_φ²`, exactly as for an
  unseen player in section 6. Uncertainty widens rather than confidence being invented.

This keeps a bad read cheap. The alternative - dropping the match entirely - would throw away
good hero data to protect a weaker parameter.

### Consequence for existing data

The prefix rule is evaluated against names already collected, so it is order-dependent and
must be applied as a batch pass over the whole table at fit time, not incrementally at insert
time. Player keys are therefore derived during fitting and never stored as a match column.

## 10. Scope: one theme at a time

Hero balance and the map change with the theme, so parameters are fitted **per theme** and
reset when the theme rotates. `Converging Paths` rotates 2026-07-28; the next themes are
`Flourishing Wilds` and `Tactical Grounds`, which share identical rules and differ only in
standard versus special terrain.

Cross-theme memory is a possible later refinement - carrying the previous theme's estimates
as a weak prior, `θ_current ~ N(ρ θ_previous, σ_theme²)` with `ρ` around 0.25 - but v1 fits
each theme from scratch.

## 11. Components

| file | responsibility |
|---|---|
| `services/solstice/odds.py` | **new**, pure. Fit the model from match rows; predict from a partial draft. No device, no UI. |
| `services/solstice/validate.py` | **new**, pure. The repeated-shuffle validation of section 8, the three baselines, and the pass/fail evaluation of section 7's validation gate. Returns a result object; decides nothing about display. |
| `services/solstice/identity.py` | **new**, pure. Normalise and vet player names per section 9 (Player identity safety). Maps a raw OCR string to either a trusted player key or `None`. |
| `services/solstice/store.py` | **modify**. Read matches for fitting; persist fitted parameters, validation summaries, and gate state. |
| `data/solstice_clash/schema.sql` + `migrate.py` | **modify**. Tables for fitted parameters, validation runs, gate state, and logged predictions. |
| `mixins/solstice_clash.py` | **modify**. During the draft, read locked picks, call the predictor, log the result. |
| frontend panel | **new**. Displays the current estimate, interval, trust label and evidence. |

Everything statistical is pure and testable against fixture match data with no device and no
GUI, in the same shape as Mode A's services.

## 12. Display

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

## 13. Prediction logging

Every computed estimate is written to the database with the draft state it was computed
from, the resulting probability and SE, and later the actual outcome. Predictions are logged
**whether or not the display gate is open**, so the hidden pre-gate period accumulates a
shadow record.

This is a monitoring surface, not the gate: section 8's refit validation decides when the
display opens, and these logs confirm afterwards that live behaviour matches it. It costs
nothing to record and it is the only way to notice the model drifting once it is live.

## 13a. Rating gap: a stated prior that learns, per event

Added 2026-07-28, after measurement showed the hero model has no demonstrable edge at
277 matches (0.6967 against a 0.6993 baseline; 3.0 matches per hero parameter, where
free per-hero strengths need roughly 1500-3000).

The four-digit ladder rating is read by OCR from the DRAFT screen, where it is visible
while betting is open, and stored on the match. The gap maps to a probability nudge
through a stated table (<50 points noise, 150 about 72/28, 400+ capped at 93%), because
with no historical ratings there was nothing to fit and a considered prior beats a
coefficient fitted on zero observations.

Each recorded match with both ratings counts toward its band, and the band shrinks from
the prior toward what was observed at weight `n/(n+20)`. Evidence pools across themes -
a rating means the same thing whichever theme is running - but never across events,
because rank points reset on every theme change and the ladder resets between events.

### Where this goes next: a global curve across events

Today every event starts from the same hardcoded table and its learning dies with it.
That does not compound. The structure that does is hierarchical:

- each event's bands shrink toward a GLOBAL curve rather than toward the hardcoded one,
- the global curve is updated from every completed event,
- the hardcoded table is the seed for the first event only.

After three or four events the starting curve is measured rather than stated, while any
single event can still deviate where its meta genuinely differs. Not built yet - there
is exactly one event's data, so there is nothing for a global curve to learn from. Build
it when the second event ends, not before.

## 14. Open items

- **Draw handling** (section 2) - v1 documents the conditional; recording draws with comps
  and fitting the three-outcome model is the first follow-up.
- **Whether player terms actually reduce selection bias** or merely absorb variance. Codex
  was asked directly and its answer should be treated as a hypothesis to test, not settled:
  compare fitted `θ` with and without player terms once enough data exists.
- **Whether the useful window is long enough to act in.** Section 7 shows a number can only
  appear once four of six picks are locked. Whether that leaves enough time before betting
  closes is a timing question the first live run answers.
- **Whether the crowd carries too much weight at 0.70** (raised by the operator,
  2026-07-28). A parimutuel pool is not a set of independent opinions: bettors can see the
  split before they bet, so money on one side attracts more money to that side. The pool is
  partly a measurement of itself, and a snowball can look exactly like consensus while
  carrying no more information than the first few bets did.
  If that is happening, `crowd_reliability` is the wrong correction for it. Weighting by
  spectator count assumes more participants means more independent views, which is the
  assumption in question - a 300-person avalanche would score as maximally reliable.
  Testable once scored predictions exist, and worth testing before trusting the weight:
  fit the crowd coefficient against outcomes directly, and separately check whether early
  pool splits predict results better than late ones. If a late split is systematically
  more extreme than an early one without being more accurate, that is the snowball, and
  the answer is either a lower weight, a shrink toward 50% that grows with how lopsided
  the pool is, or reading the pools early rather than last.

Player-name reliability and draft-screen readability were open items in the first draft of
this spec and have been promoted to prerequisites P1 and P2 in section 0. They are not
follow-ups.
