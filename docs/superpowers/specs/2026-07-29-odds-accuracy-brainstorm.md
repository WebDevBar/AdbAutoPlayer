# How do we make this number worth acting on?

2026-07-29. A brainstorming brief for three reviewers. Nothing here is a decision; the
deliverable is ideas with mechanisms, ranked by what they would cost and what they would
buy.

## The state, honestly

The model works and is useless.

- Bradley-Terry on hero identity beats the base rate: +0.0085 logloss, 21 of 25 shuffle
  splits, 8 of 9 forward blocks. It cleared a pre-registered bar in two rounds across
  three independent implementations.
- Its predictions have a standard deviation of **4.9 percentage points**. 53% of them land
  within 3 points of 50/50. The operator's verdict on seeing it: *"51 is hardly a nudge."*
- The calibration is honest. Widening the log-odds by any factor makes logloss
  monotonically worse: k=1.0 gives 0.6885, k=2.0 gives 0.6934, k=4.0 gives 0.7271. The
  flatness is not a display bug and cannot be tuned away.
- The tails are real: outside the 48-52% band the model is right ~58% of the time; inside
  it, 50.2% over 705 predictions - a literal coin flip.

So the number is accurate and indecisive. The question is what makes it decisive without
making it dishonest.

## What has been ruled out

~58 configurations tested. Full detail in `docs/solstice-clash/model-findings-ledger.md`.

- **The crowd's betting split.** Flat across its own confidence, a noisy echo of the
  rating gap, 0.83 logloss standalone. Removed.
- **The ladder rating.** Now measured as HARMFUL next to a working hero model: W_RATING=0
  scores +0.0085 against the shipped 0.60's +0.0073, degrading monotonically upward. The
  ladder gives about +-20 points per match almost regardless of opponent, so a 100-point
  gap is roughly five net wins, not a strength difference.
- **The post-match stats** (damage dealt, healing, damage taken), in ~20 variants across
  both available mechanisms - as an added term and as the hero prior's shrinkage target.
  Diagnosis reached independently by two reviewers: their variance is dominated by ROLE,
  and role composition is the worst-performing family tested.
- **Class, faction, race composition; class-vs-class counters; faction synergy; player
  identity; draft slot.**

## What is knowable and not being read

This is where the operator's instinct points, and it may be the whole answer.

The draft cards on screen carry **hero level** (`Lvl 240`) and a **star tier**, and the
model reads neither. The `power` column exists in the schema and is 0% populated. Two
players can lock the same six heroes with completely different investment and produce
identical model inputs today.

Hero identity is a *preference* signal - it says what people like to pick. Investment is a
*strength* signal. That difference may be the entire gap between 51:49 and something
worth betting on.

Also visible and unread: the 20-hero pool each draft offers, which heroes were passed
over, the pick order, and the ban/lock indicators.

## The question

**Assume the dataset is four times bigger, or ten.** 340 matches becomes 1,400 or 3,400,
with every column we currently store. Now:

1. **What would you build that you cannot build today?** Be specific about the model, not
   just "more data helps". Which of the ruled-out families come back to life at 1,400
   matches, and which are dead at any size?
2. **What must we start collecting TONIGHT so that answer is available later?** This is
   the urgent half. Anything not captured now is unrecoverable - we already lost the
   ability to test the crowd-snowball hypothesis by storing one pool sample per match
   instead of several.
3. **What can be done RIGHT NOW, at 340 matches, that we have not tried?** Ideas that
   extract more from the existing rows rather than waiting.
4. **Is there a fundamentally different framing?** Everything so far predicts a binary
   outcome from a feature vector. Alternatives nobody has considered: modelling the draft
   as a sequence, predicting margin rather than outcome, hierarchical models that pool
   across heroes, anything that changes the shape of the problem rather than its inputs.
5. **What is the realistic ceiling?** If 3v3 auto-battler outcomes are substantially
   determined by things invisible from the draft screen - gear, artifacts, RNG, the
   battlefield modifier - then a decisive number is not achievable and the honest product
   is a confident "too close to call" most of the time. Say so if you think so.

## Transferability is a first-class criterion

Raised by the operator, and it reframes the ranking: **a signal that survives an event
boundary is worth much more than one that must be relearned.**

Bradley-Terry knows nothing at the start of a new event. It took roughly 300 matches to
become useful this time - days of collection - and every new event resets it to useless.
A signal learned as meta-knowledge rather than as per-hero win records would arrive on day
one already informative, which is exactly when a fresh model has nothing to say.

Rank-weighted popularity is the clearest case: "which heroes do strong players choose"
is a claim about the game, not about this event's outcomes, so it should carry over. If it
holds up, its value is not the +0.0137 measured here - it is being immediately useful in
every future event on almost no data.

So for every proposal, state whether it transfers across an event boundary, and rank
transferable signals above equally-strong local ones.

## Specific ideas already on the table

Rank these against your own rather than treating them as a list to complete.

- **Read investment (level, stars) off the draft cards.** Causal, pre-match, and free
  geometrically since the cards are already cropped for identification.
- **Team balance rather than sums.** Every stat test so far summed or ratio'd. None asked
  whether a comp is complementary - three glass cannons and three tanks can have identical
  totals and different outcomes.
- **Stats per rating point.** 5M damage from a 4200 player is a different claim than 5M
  from a 4500.
- **Consistency rather than average.** Every test used means; none used variance.
- **Damage divided by opposing tanking.** The closest anything has come: gain exceeded its
  standard error at 16/25, just under the bar.
- **Rank-weighted hero popularity - the operator's idea, and the evidence is FOR it.**
  Score a hero by the mean rating of the players who picked it, not by how often it was
  picked. On the 61 matches carrying ratings it CLEARS the bar (+0.0137, 3.2x SE, 18/25)
  on the same matches where plain popularity FAILS (-0.0111, 12/25). The sign flips
  between them. It fails on the full 340 only because 279 of those matches have no rating
  to weight by, so the feature is undefined there and falls back to a grand mean.
  This is not a null result - it is a positive result on a small sample, and it is the
  operator's stated position that it will hold up. Treat it as a leading candidate and say
  what would confirm or kill it, including how many rated matches are needed and whether
  the full-set failure has an explanation other than "it does not work".

- **Refuse to print the middle.** Show "too close to call" inside 48-52%, a real number
  outside it. Does not improve the model; makes it usable 61% of the time instead of
  meaningless every time.

## Constraints

- One theme's data so far; a theme applies modifiers hitting every hero equally, and
  matches pool across themes within an event.
- Draws are never recorded - the game returns to the overworld with no result screen - so
  every stored match is decisive by construction.
- No scipy or sklearn in the shipped model; numpy only. Offline exploration may use
  anything.
- Collection is automated and runs nightly; more matches accumulate without effort. Adding
  a new *column* requires reading something new off the screen, which costs OCR time
  inside a ~20 second draft.
