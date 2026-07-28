# Odds calibration - what 54 scored predictions say

Written 2026-07-29, at the end of the Converging Paths theme and before the rotation to
Flourishing Wilds. This is a review document: it states what the collected data shows,
and asks three reviewers to propose what the model should become. It changes nothing.

## Why it exists

The operator's report, unprompted by any analysis: *"a lot of very sure predictions turn
out the other way - 75% confidence in one side to have that side lose is not accurate at
all."*

That is now measurable for the first time. Every prediction has been recorded with its
inputs since 2026-07-28, and 54 of them have an outcome.

## The headline

**The combined number has no edge.** It is right 27 times out of 54, which is a coin flip,
and its logloss is worse than a constant.

| predictor | logloss | vs base rate |
|---|---|---|
| always predict the base rate (44% left) | 0.6865 | - |
| always predict 50% | 0.6931 | worse |
| **our combined number** | **0.6977** | **worse** |
| the rating table alone | **0.6752** | **better** |

Logloss is the standard score for probabilistic predictions: lower is better, and a
predictor that cannot beat a constant is not carrying information. Ours cannot.

The 52-match subset carrying ratings is used throughout so the comparison is like for like.

## Signal by signal

Directional accuracy - did the side we favoured actually win?

| signal | correct |
|---|---|
| higher ladder rating | **30/51 = 59%** |
| our combined number | 27/54 = 50% |
| the crowd's favourite | 26/54 = 48% |

### Rating is the only signal that works, and it works the way it should

| rating gap | favourite won |
|---|---|
| 0-50 | 7/15 = 47% |
| 50-100 | 11/19 = 58% |
| 100-150 | 8/12 = 67% |
| 150+ | 4/5 = 80% |

Monotonic in the gap. That is what a real signal looks like: bigger gap, better prediction.
The operator's stated prior table was, on this evidence, close to right.

### The crowd is not just weak, it is anti-informative

| crowd's confidence | crowd was right |
|---|---|
| 50-60% | 7/13 = 54% |
| 60-70% | 10/24 = 42% |
| 70-80% | 5/10 = 50% |
| 80%+ | 4/7 = 57% |

Flat. A crowd that is 80% sure is no better than one that is 55% sure, and the largest
bucket is *below* chance. Standalone logloss is 0.8307 against a 0.6865 constant.

### Spectator count makes it worse, not better, which breaks our weighting

`crowd_reliability` weights the market by how many people are watching, on the assumption
that more participants means more independent opinions.

| watchers | crowd was right |
|---|---|
| 0-50 | 2/5 = 40% |
| 50-100 | 3/7 = 43% |
| 100-200 | 11/17 = 65% |
| 200+ | **9/21 = 43%** |

The bucket we trust most is the one that performs worst. This is consistent with the
snowball the operator described before seeing any of these numbers: bettors can see the
split before they bet, so money attracts money, and a 300-person pool can be one opinion
amplified 300 times rather than 300 opinions averaged.

## What is therefore happening to the number

The rating signal is real and worth about 0.011 of logloss. It is combined at weight 0.60
with a crowd signal at weight 0.70 - the crowd is weighted *more heavily than the only
signal that works* - and the result is worse than either the rating alone or a constant.

```text
logit(p) = 0.60*logit(p_rating) + 0.70*q_crowd*logit(p_crowd) + 0.50*evidence*logit(p_heroes)
```

Shrinking the rating table alone does not help either, which suggests the table is not the
problem:

| rating table, scaled | logloss |
|---|---|
| x1.00 (as stated) | 0.6752 |
| x0.50 | 0.6770 |
| x0.35 | 0.6806 |
| x0.25 | 0.6836 |

## What this evidence cannot support

Stated plainly, because it bounds every proposal:

- **n = 54.** At this size a 59% directional result has a confidence interval that
  comfortably includes 50%. Nothing here is established; it is the best available reading.
- **One theme, one evening, one operator's device.** Converging Paths only.
- **Ratings exist only from 2026-07-28.** There is no history to fit against.
- **Draws are never recorded** - the game returns to the overworld with no result screen -
  so every row here is decisive by construction.
- **The hero model was separately measured as having no edge** over 277 matches and 93
  heroes: 0.6967 against a 0.6993 baseline, winning 15 of 25 shuffle splits.

## The questions for review

1. Given the crowd is flat-to-anti-informative and gets worse with more participants,
   what should `W_CROWD` be? Zero, a small negative, or a small positive with the
   spectator-count weighting inverted or removed? Argue from the data above.
2. `crowd_reliability` is built on an assumption the data contradicts. Should it be
   replaced by something that measures pool *lopsidedness* rather than participation, or
   dropped?
3. Is the rating table the right shape, or should the gap feed a fitted logistic
   coefficient now that 51 rated matches exist? Note the bands above 150 have 5 matches
   between them.
4. The hero term contributes at weight 0.50 x evidence despite being measured as having no
   edge. Keep, shrink, or remove?
5. Should anything be displayed at all until a predictor beats the base rate out of
   sample? The current gate is 40 matches per theme, which this passes while the number
   is worthless.
6. What is the smallest change that makes the displayed number defensible tomorrow, when
   Flourishing Wilds resets the theme and the collected evidence with it?

## The raw data

Every scored prediction. `ours` is what was displayed, `gap` is left rating minus right.

| id | ours | ratings | gap | crowd % left | watchers | won |
|---|---|---|---|---|---|---|
| 278 | 49% | 4263/4331 | -68 | 54 | 63 | left |
| 279 | 48% | 4240/4335 | -95 | 92 | 21 | right |
| 280 | 75% | 4423/4266 | +157 | 62 | 114 | left |
| 281 | 53% | 4435/4372 | +63 | 72 | 221 | right |
| 282 | 58% | 4395/4300 | +95 | 65 | 33 | right |
| 283 | 46% | 4410/4411 | -1 | 74 | 103 | left |
| 284 | 34% | 4474/4418 | +56 | 26 | 276 | left |
| 285 | 34% | 4442/4451 | -9 | 29 | 268 | right |
| 286 | 54% | 4431/4463 | -32 | 56 | 197 | right |
| 287 | 42% | 4419/4340 | +79 | 36 | 259 | left |
| 288 | 33% | 4222/4325 | -103 | 16 | 91 | right |
| 289 | 35% | 4300/4408 | -108 | 27 | 115 | right |
| 290 | 54% | 4343/4339 | +4 | 60 | - | right |
| 291 | 43% | 4425/4481 | -56 | 41 | 127 | right |
| 292 | 36% | 4382/4458 | -76 | 33 | 220 | left |
| 293 | 59% | -/4407 | - | 66 | 212 | right |
| 294 | 37% | 4373/4413 | -40 | 34 | 176 | right |
| 295 | 45% | 4158/4240 | -82 | 1 | 18 | right |
| 296 | 41% | 4464/4464 | +0 | 36 | 293 | right |
| 297 | 39% | 4444/4484 | -40 | 34 | 215 | left |
| 298 | 71% | 4462/4289 | +173 | 65 | 114 | left |
| 299 | 42% | -/4473 | - | 36 | 198 | left |
| 300 | 47% | 4445/4449 | -4 | 46 | 293 | right |
| 301 | 46% | 4469/4426 | +43 | 38 | 176 | left |
| 302 | 33% | 4409/4487 | -78 | 32 | 259 | left |
| 303 | 48% | 4434/4466 | -32 | 50 | 226 | left |
| 304 | 39% | 4375/4456 | -81 | 31 | 120 | left |
| 305 | 54% | 4432/4400 | +32 | 63 | 2 | right |
| 306 | 68% | 4392/4473 | -81 | 91 | - | right |
| 307 | 48% | 4377/4460 | -83 | 45 | 56 | left |
| 308 | 52% | 4436/4402 | +34 | 51 | 64 | left |
| 309 | 31% | 4247/4455 | -208 | 55 | 66 | right |
| 310 | 58% | 4477/4355 | +122 | 63 | 26 | left |
| 311 | 58% | 4491/4410 | +81 | 59 | 283 | right |
| 312 | 46% | 4435/4467 | -32 | 48 | 147 | right |
| 313 | 67% | 4531/4417 | +114 | 63 | 320 | right |
| 314 | 41% | 4449/4502 | -53 | 36 | 310 | right |
| 315 | 59% | 4433/4288 | +145 | 95 | - | right |
| 316 | 43% | 4406/4453 | -47 | 36 | 122 | left |
| 317 | 55% | 4431/4405 | +26 | 67 | 72 | right |
| 318 | 39% | 4410/4519 | -109 | 43 | 252 | right |
| 319 | 60% | 4488/4437 | +51 | 70 | 146 | left |
| 320 | 65% | 4506/4428 | +78 | 70 | 215 | left |
| 321 | 25% | 4413/4522 | -109 | 24 | 239 | right |
| 322 | 23% | 4400/4536 | -136 | 18 | 186 | right |
| 323 | 55% | 4549/4463 | +86 | 55 | - | left |
| 324 | 12% | 4438/4565 | -127 | 8 | 256 | right |
| 325 | 44% | 4426/4405 | +21 | 39 | 198 | right |
| 326 | 59% | 4578/4446 | +132 | 48 | 250 | left |
| 327 | 77% | 4591/4434 | +157 | 68 | 239 | left |
| 328 | 81% | 4603/4321 | +282 | 79 | 91 | right |
| 329 | 59% | 4570/4471 | +99 | 67 | 121 | left |
| 330 | 25% | 4457/4585 | -128 | 22 | 165 | left |
| 331 | 72% | 4558/4430 | +128 | 71 | 235 | right |
