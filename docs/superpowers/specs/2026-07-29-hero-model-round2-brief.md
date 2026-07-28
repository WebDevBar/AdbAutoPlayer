# Hero model - round 2 brief

Round 1 established, across three independent implementations:

- **Bradley-Terry on hero identity CLEARS the bar** at ~335 matches. Codex 0.6884 vs base
  0.6952 (SE 0.0020, 22/25); Fable -0.0059 (3.3x SE, 21/25) and it survives temporal
  validation at 8/9 forward blocks; a third run of the shipped `fit`/`predict` gave
  +0.0042 (4.7x SE, 22/25). The earlier 245-match null was correct when measured - the
  edge appeared between 250 and 300 matches.
- **The post-match stats do NOT convert.** ~12 variants across both reviewers - raw,
  share-of-team, log, class-centred, outcome-adjusted, several shrinkages - all at or
  below the base rate, and all DILUTE Bradley-Terry when added to it. Diagnosis reached
  independently by both: those numbers mostly measure which ROLE a hero plays, and role
  does not predict winning (class composition is the worst family tested).
- **Two new features appeared.** Popularity - how often a hero is picked - clears the bar
  alone (Fable), though part of that edge is hindsight from shuffle-splitting. Slot -
  which draft position - marginally clears at 18/25 (Codex).
- **The crowd loses** in every formulation. Confirmed against 54 scored predictions
  separately: it is a noisy echo of the rating gap.

## The question for round 2

**What is the best COMBINATION?** Round 1 tested families mostly in isolation. The goal
now is one model to display, chosen on merit.

## Required changes to the protocol

Both criticisms from round 1 are accepted and are now mandatory:

1. **FREEZE THE DATA.** The database is live and grew mid-experiment last time, by more
   than some of the effects being measured. Add `WHERE match.id <= FREEZE_ID` to every
   query. FREEZE_ID = 340, the highest decisive match id at 2026-07-28T22:30Z.
2. **TEMPORAL VALIDATION IS NOT OPTIONAL.** Report it alongside the shuffle splits for
   every candidate: train on all matches before a cut, test on the next block of 20, walk
   forward. Shuffle splits leak the future into any fitted prior - popularity especially -
   and the temporal number is the honest one. A candidate that passes shuffle splits and
   fails temporally has not shown anything.

Otherwise unchanged: 25 seeded shuffle splits 0-24, 80/20, mean held-out logloss, paired
SE against the training-fold base rate, splits won. Bar unchanged: beat the base rate by
more than its paired SE on more than 17 of 25 splits, AND now also win a majority of
forward blocks.

## Specific candidates

Not a limit - propose others.

1. `BT + popularity`, popularity computed on the training fold only.
2. `BT + slot`.
3. **Stats as the BT shrinkage target.** Untried by anyone. Instead of adding the stats as
   a separate term - which dilutes - use them to set what a rarely-seen hero shrinks
   TOWARD. A hero seen twice currently shrinks toward zero; it could shrink toward what
   its damage output suggests. This targets exactly where BT is weakest, and is the only
   remaining route by which the stats could contribute.
4. `BT + rating gap` (Fable measured -0.0091, the second best of round 1).
5. The full combination, with weights fitted on the training fold rather than stated.
6. A sigma sweep on the winner - 0.15 was best in round 1 but 0.20 was close.

## What to report

For each candidate: params, shuffle logloss, base rate, paired SE, splits won /25,
temporal logloss, forward blocks won. Then a plain recommendation of ONE model to display,
with the numbers that justify it, and what it should be re-checked against later.

Report failures as prominently as successes.
