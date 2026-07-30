"""Live win probability from collected matches - pure, no device, no GUI.

Regularised Bradley-Terry: one strength per hero, one skill per player, an intercept for
any structural left/right advantage. A side's strength is the sum of its heroes, so a
draft is scored by adding three numbers and comparing.

Why this and not the obvious alternative: counting each hero's win rate separately
credits a hero for the company they kept. Fitting all heroes at once against each other
discounts a 70% record earned against weak comps, which is the whole point when three
heroes share one outcome.

Regularisation is not optional here. With ~120 heroes and a few hundred matches, an
unpenalised fit invents enormous strengths for heroes seen once or twice - a hero who
appeared in one winning comp would read as unbeatable. Every parameter is shrunk toward
zero, so thin evidence produces a number near "no information" rather than a confident
wrong one.

Cross-theme data is currently DISCARDED - see CROSS_THEME_WEIGHT, which is 0.0. It was
weighted at 0.35 first, on the reading that a theme changes how a fight plays out without
changing which comp is stronger; the game then contradicted that twice in one evening, by
changing the roster and the battlefield between themes. Themes change the
hero pool and the battlefield rules, so another theme's matches are not the same
experiment - but they are not noise either, and early in a theme they are most of what
exists. Weight, not a binary include/exclude, is the honest expression of that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Prior SDs, from the design. Not tunables to reach for: they set how much evidence it
# takes to move a hero away from "average", and loosening them is how a model starts
# reporting certainty it has not earned.
# 0.20 since 2026-07-29, at 340 matches. It was 0.15, chosen when 0.30 measured WORSE
# than the base rate on the first 245 matches (0.7006 against 0.6993) - and that comment
# said thin data wants a tighter prior and to revisit. The optimum has drifted up exactly
# as predicted: two independent sweeps agree 0.20 beats 0.15 (+0.0016 paired, and better
# on walk-forward validation). They diverge above it - Codex prefers 0.30, Fable measures
# 0.25 as missing the splits bar - so 0.20 is the value both support.
# Re-sweep at each rough doubling of the data. This constant is not converged.
SIGMA_THETA = 0.20  # hero strength
SIGMA_PHI = 0.50  # player skill
SIGMA_BETA = 1.0  # intercept

# Player terms are OFF. On 245 matches there were 162 distinct players - nearly two per
# match - so most appear once and their skill term simply absorbs that match's outcome.
# Measured: including them changed out-of-sample logloss by less than 0.0001, so they
# bought nothing while adding 162 parameters of overfitting risk. The machinery stays
# because the question is worth revisiting once players repeat.
USE_PLAYER_TERMS = False

# The ladder rating gap, as ONE fitted coefficient rather than 93. Scaled per 100 points,
# so the fitted value reads directly as "what a 100-point lead is worth in log-odds" -
# and if it is worth nothing, the fit says so rather than us assuming it.
#
# This is the feature most likely to work at this sample size: hero strengths ask for 93
# parameters from a few hundred matches, this asks for one, and the rating is the game's
# own summary of who is the better player.
RATING_SCALE = 100.0

# What a rating gap is worth, as percentage points moved off an even 50/50 toward the
# higher-rated side.
#
# MEASURED, replacing the stated prior it started as. Over 95 rated matches, against the
# hero model, out of sample on 25 shuffle splits:
#
#   mapping                              gain     x SE   splits
#   hero model alone                   +0.0123    4.5    20/25
#   the stated 9-band table x0.60      +0.0182    2.9    18/25
#   THIS: nothing under 100, then flat +0.0215    7.6    23/25
#   deadband 50, proportional, cap 0.5 +0.0202    4.8    22/25
#   deadband 100, proportional         +0.0115    3.4    18/25
#
# Two findings, and the second is the surprising one:
#
#   - Gaps under 100 points are worth NOTHING. The old table nudged 5% at 50 points and
#     1.5% below that; the data says 0-50 is 47% for the favourite, which is noise.
#   - Past 100 points, MORE GAP IS NOT MORE EDGE. Every proportional variant scored at or
#     below this flat step and none was as reliable. What matters is whether the gap
#     clears the threshold, not how far past it goes.
#
# Both fit what the ladder actually is: about +-20 points a match almost regardless of
# opponent, so 100 points is roughly five net wins - the first amount that means anything
# and, apparently, most of what it can mean.
#
# 0.0622 is 0.25 in log-odds, which is what was measured.
#
# CAVEAT: only a handful of matches carry gaps above 150, so if the far end does deserve
# more weight this sample cannot see it. Re-check at ~200 rated matches.
RATING_NUDGE = (
    # (gap at least, percentage points off 50)
    (100, 0.0622),
    (0, 0.0),
)
# Once ratings have been collected for long enough to fit `gamma`, this switches off and
# the fitted coefficient takes over. Kept as a flag so the changeover is deliberate.
USE_RATING_PRIOR = True
# No gap alone is proof. 400+ points means "heavily favoured", not "certain".
MAX_RATING_PROBABILITY = 0.93
# Loose enough not to fight real signal, tight enough that one 1000-point outlier cannot
# swing the model.
SIGMA_RATING = 0.5

# Matches from another theme in the same event count this much against a match from the
# theme being predicted.
#
# 0.35. It was briefly 1.0 on the reading that a theme applies modifiers hitting every
# hero equally - which the game then contradicted on 2026-07-29, twice in one evening:
#
#   - The ROSTER changes. Aurora was pickable in a live Flourishing Wilds match while our
#     Converging Paths roster snapshot had her banned. A hero that cannot be picked in one
#     theme has no strength to carry into the next.
#   - The MAP changes. The in-game Themes screen distinguishes "Standard terrain" from
#     "Special terrain", and positioning on it is part of how a fight resolves.
#
# So a match from a sibling theme is evidence about the same heroes under different rules.
# Not worthless - early in a theme it is most of what exists - but not equivalent either.
#
# 0.0 since 2026-07-29: another theme's matches are DISCARDED, not merely discounted.
# 0.35 was a compromise between "same heroes" and "different rules", and the operator's
# read of the first rotation is that the borrowed evidence hurts more than the thin data
# it was meant to cushion - which is consistent with a roster and a battlefield that both
# change.
#
# The cost is real and accepted: at every rotation the model starts from nothing and is
# useless until the new theme fills in. That is the honest position when the thing being
# modelled genuinely changed, and it is a weight to revisit once two themes each hold a
# few hundred matches and the question can be answered rather than judged.
CROSS_THEME_WEIGHT = 0.0

MAX_ITERATIONS = 100
CONVERGENCE = 1e-8


@dataclass(frozen=True)
class Match:
    """One decisive match, as the model sees it."""

    left: tuple[str, ...]
    right: tuple[str, ...]
    left_won: bool
    theme_id: int | None = None
    left_player: str | None = None
    right_player: str | None = None
    left_rating: int | None = None
    right_rating: int | None = None
    event_id: int | None = None


@dataclass(frozen=True)
class Fit:
    """Fitted parameters and what they were fitted on.

    `matches` counts rows that CONTRIBUTED - not rows loaded. A cross-theme match at
    weight 0 is loaded and ignored, and counting it would tell `gate_reason` the model
    has evidence it does not have."""

    heroes: tuple[str, ...]
    players: tuple[str, ...]
    beta: np.ndarray
    hessian: np.ndarray
    matches: int
    appearances: dict[str, int] = field(default_factory=dict)

    def index_of(self, hero: str) -> int | None:
        """Column for a hero, or None if it was never seen."""
        try:
            return 1 + self.heroes.index(hero)
        except ValueError:
            return None


# Themes whose modifiers are identical, listed by NAME because that is what a person can
# check against the in-game Themes screen. Matches from any theme in a group count in
# FULL toward a fit for any other theme in the same group.
#
# The rotation reset exists because a theme changes the roster and the battlefield. When
# it changes neither, the cold start buys nothing - and at a few hundred matches per
# theme with two collectors, a reset is most of what the model ever costs.
#
# Pooled at FIT time on purpose. The matches stay stored under their own theme, one row
# each, so this is a flag rather than a merge: if the modifiers turn out to differ after
# all, remove the group and nothing has been lost. Fusing the data would have destroyed
# the control group behind the strongest result in the ledger.
#
# 2026-07-30: Flourishing Wilds closes and Tactical Grounds opens with the same
# modifiers. Converging Paths is deliberately NOT here - its modifiers did change, and
# carrying its strengths forward measured 47% directional, worse than a coin.
SHARED_MODIFIER_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"Flourishing Wilds", "Tactical Grounds"}),
)


def themes_sharing_modifiers(name: str | None) -> frozenset[str]:
    """Every theme name sharing `name`'s modifiers, including itself.

    Returns just the name itself when it belongs to no group, so a caller can always
    treat the result as the full set of themes a fit may draw on.
    """
    if name is None:
        return frozenset()
    for group in SHARED_MODIFIER_GROUPS:
        if name in group:
            return group
    return frozenset({name})


def design(
    matches: list[Match],
    theme_id: int | None = None,
    siblings: tuple[int, ...] = (),
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...], tuple[str, ...]]:
    """Build the design matrix, outcomes and per-match weights.

    Column 0 is the intercept, then one column per hero, then one per player. A hero is
    +1 on the left and -1 on the right, so only the DIFFERENCE between the sides can be
    learned - which is all a win/lose outcome contains.

    `siblings` are theme ids sharing this theme's modifiers - see
    SHARED_MODIFIER_GROUPS. They carry full weight; every other theme still carries
    CROSS_THEME_WEIGHT.
    """
    heroes = tuple(sorted({h for m in matches for h in (*m.left, *m.right)}))
    players = (
        tuple(
            sorted(
                {
                    p
                    for m in matches
                    for p in (m.left_player, m.right_player)
                    if p is not None
                }
            )
        )
        if USE_PLAYER_TERMS
        else ()
    )
    hero_at = {h: 1 + i for i, h in enumerate(heroes)}
    player_at = {p: 1 + len(heroes) + i for i, p in enumerate(players)}
    rating_at = 1 + len(heroes) + len(players)

    x = np.zeros((len(matches), rating_at + 1))
    y = np.zeros(len(matches))
    w = np.ones(len(matches))

    for row, match in enumerate(matches):
        x[row, 0] = 1.0
        for hero in match.left:
            x[row, hero_at[hero]] += 1.0
        for hero in match.right:
            x[row, hero_at[hero]] -= 1.0
        if match.left_player in player_at:
            x[row, player_at[match.left_player]] += 1.0
        if match.right_player in player_at:
            x[row, player_at[match.right_player]] -= 1.0
        # A missing rating contributes ZERO, which is "no information about the gap"
        # rather than "the gap was zero" - the column is centred on zero either way.
        if match.left_rating is not None and match.right_rating is not None:
            x[row, rating_at] = (match.left_rating - match.right_rating) / RATING_SCALE
        y[row] = 1.0 if match.left_won else 0.0
        counts_fully = match.theme_id == theme_id or match.theme_id in siblings
        if theme_id is not None and not counts_fully:
            w[row] = CROSS_THEME_WEIGHT

    return x, y, w, heroes, players


def fit(
    matches: list[Match],
    theme_id: int | None = None,
    siblings: tuple[int, ...] = (),
) -> Fit:
    """Newton-Raphson to convergence. No scipy - it is not in the bundled runtime.

    The Hessian is symmetric positive definite because XᵀWX is positive semi-definite
    and the prior term is strictly positive, so the solve is always well posed. That is
    also what makes the variance in `predict` safe to compute.

    `siblings` pools themes with identical modifiers - see SHARED_MODIFIER_GROUPS.
    """
    x, y, w, heroes, players = design(matches, theme_id, siblings)
    penalty = np.full(x.shape[1], 1.0 / SIGMA_THETA**2)
    penalty[0] = 1.0 / SIGMA_BETA**2
    penalty[1 + len(heroes) : -1] = 1.0 / SIGMA_PHI**2
    penalty[-1] = 1.0 / SIGMA_RATING**2

    beta = np.zeros(x.shape[1])
    hessian = np.diag(penalty)
    for _ in range(MAX_ITERATIONS):
        p = 1.0 / (1.0 + np.exp(-(x @ beta)))
        gradient = x.T @ (w * (p - y)) + penalty * beta
        weights = w * p * (1.0 - p)
        hessian = (x.T * weights) @ x + np.diag(penalty)
        # NEVER form the inverse: solve is both faster and better conditioned.
        beta = beta - np.linalg.solve(hessian, gradient)
        if np.max(np.abs(gradient)) < CONVERGENCE:
            break

    # Counted from matches that actually CONTRIBUTED, not from everything handed in.
    # Another theme's matches carry weight 0 now, so counting them said the model had
    # seen a hero 21 times in a theme holding 14 matches - and `hero_evidence` reads this
    # same count to decide how far to trust the hero term, so the error was inflating the
    # model's confidence, not just a log line.
    appearances: dict[str, int] = {}
    for row, match in enumerate(matches):
        if w[row] <= 0.0:
            continue
        for hero in (*match.left, *match.right):
            appearances[hero] = appearances.get(hero, 0) + 1

    # `matches` is the count that CONTRIBUTED, not the count loaded. `gate_reason` reads
    # it to decide whether anything has been collected at all, and with cross-theme
    # matches at weight 0 the loaded count says yes when the fit saw nothing.
    contributing = int(sum(1 for weight in w if weight > 0.0))

    return Fit(
        heroes=heroes,
        players=players,
        beta=beta,
        hessian=hessian,
        matches=contributing,
        appearances=appearances,
    )


@dataclass(frozen=True)
class Prediction:
    """A probability with the uncertainty that makes it usable."""

    p_mid: float
    p_low: float
    p_high: float
    eta: float
    standard_error: float
    known_heroes: int
    weakest_evidence: int
    # Which of the three signals actually moved this number, in the order they are
    # combined. NOT what was available: a crowd read off 12 spectators, or a comp of
    # heroes nobody has seen, contributes a weight of nearly nothing, and naming it
    # would misdescribe the number to whoever is about to act on it.
    signals: tuple[str, ...] = ()

    @property
    def favours(self) -> str:
        return "left" if self.p_mid >= 0.5 else "right"

    @property
    def source_code(self) -> str:
        """A compact record of the composition, stored alongside the prediction.

        Short because the server column is 16 characters - and worth storing at all
        because scored predictions have to be split BY composition later. A
        rating-only call and a rating-plus-crowd call are different models, and
        pooling their calibration would hide whichever of them is wrong.
        """
        return "+".join(s[0] for s in self.signals) or "none"


# Under this weight a signal moved the result by less than the displayed percentage
# rounds to, so naming it would overstate what is in the number.
MIN_REPORTABLE_WEIGHT = 0.02

# Named in the order the formula adds them, so the header reads the way the model works.
_SIGNAL_ORDER = ("rating", "crowd", "heroes")


# How much collected evidence it takes for a band to move halfway off the stated prior.
# 20 matches: enough that one lucky run does not rewrite a band, few enough that a band
# with real traffic converges on its own truth within an evening of collecting.
BAND_PRIOR_STRENGTH = 20.0


def band_of(gap: int) -> int:
    """Which rating band an absolute gap falls in."""
    size = abs(gap)
    return next(threshold for threshold, _ in RATING_NUDGE if size >= threshold)


def band_evidence(
    matches: list[Match], event_id: int | None = None
) -> dict[int, tuple[int, int]]:
    """Per band: (matches seen, times the HIGHER-rated side won).

    Scoped PER EVENT and pooled across themes within it. Both halves matter.

    RANK IS THEME-AGNOSTIC (operator, 2026-07-29). A player's rating does not change when
    the battlefield does, so splitting this evidence by theme would starve every band for
    no reason - and unlike the hero model, which discards other themes outright because
    the roster and the map change, this survives a rotation intact.

    It does NOT survive an event. Ratings reset between events, so a gap from a different
    event is a different scale entirely and must never be pooled with this one.

    An earlier version of this docstring also claimed rank points reset on each theme
    change, which contradicted the pooling directly below it. They do not.

    Only matches carrying both ratings count, which is every match recorded from
    2026-07-28 onward and none before it.
    """
    tally: dict[int, list[int]] = {}
    for match in matches:
        if match.left_rating is None or match.right_rating is None:
            continue
        if event_id is not None and match.event_id != event_id:
            continue
        gap = match.left_rating - match.right_rating
        if gap == 0:
            continue
        entry = tally.setdefault(band_of(gap), [0, 0])
        entry[0] += 1
        higher_won = (gap > 0) == match.left_won
        entry[1] += int(higher_won)
    return {band: (seen, won) for band, (seen, won) in tally.items()}


def blended_nudge(band: int, evidence: dict[int, tuple[int, int]] | None) -> float:
    """The band's nudge, pulled from the stated prior toward what was observed.

    Shrinkage rather than replacement: with no matches the prior stands unchanged, and
    each result moves the band a little. A band that has never been seen never moves,
    which is why bands are coarse - fine bands would each starve.
    """
    prior = next(points for threshold, points in RATING_NUDGE if band >= threshold)
    if not evidence or band not in evidence:
        return prior
    seen, higher_won = evidence[band]
    if seen == 0:
        return prior
    observed = higher_won / seen - 0.5  # as points off even, same units as the table
    weight = seen / (seen + BAND_PRIOR_STRENGTH)
    return (1.0 - weight) * prior + weight * observed


# --- combining the signals -------------------------------------------------------
#
# Three sources, added as LOG-ODDS rather than averaged as percentages: percentages do
# not add (60% and 60% is not 120%), while log-odds do - agreement reinforces, and
# disagreement cancels toward 50%, which is the honest answer for a match nobody can
# call.
#
#   logit(p) = W_RATING * logit(p_rating)
#            + W_CROWD  * q_crowd * logit(p_crowd)
#            + W_HEROES * evidence * logit(p_heroes)
#
# The weights are DAMPED on purpose. Nothing here has been validated against outcomes
# yet, and every one of them should be refitted from scored predictions once there are
# enough - which is the whole reason predictions are recorded.
# 1.0: the nudge table is now a measured offset rather than a stated prior, so there is
# nothing left to damp. The 0.60 existed to hold back a guess.
W_RATING = 1.0
# 0.70 is provisional and possibly too high. A parimutuel pool is not a set of independent
# opinions - bettors see the split before they bet, so money on one side attracts more
# money to that side, and a snowball looks exactly like consensus. `crowd_reliability`
# does not correct for it: weighting by spectator count assumes participants are
# independent, which is the assumption in doubt. Refit this against scored outcomes before
# trusting it. See section 14 of the odds design spec.
# 0.0 since 2026-07-29. Measured, not suspected: across 54 scored predictions the crowd is
# flat across its own confidence, scores 0.83 logloss standalone against a 0.69 constant,
# and is a noisy echo of the rating gap - correlation 0.475, same pick 40 times in 51, and
# on the 11 disagreements the rating is right 8 and the crowd 3. At 0.70 it outvoted every
# signal that works and supplied almost all of the displayed spread, which is precisely why
# confident calls kept landing the wrong way.
#
# NOT negative: the fitted slope is -0.0145 with a standard error of 0.235, indistinguishable
# from zero. Zero means "stop paying for it", not "bet against it".
#
# The pools and spectator count are still read, recorded and synced on every match, so this
# stays testable without any code change. A market usually beats a model, so re-test when
# the player base or the pool sizes move. The admission test is incremental: does
# gap + crowd beat gap alone, out of sample.
W_CROWD = 0.0
# Not zero. Measured over 120 real comps, the hero term moves the number by 3 points
# typically, 6 at the 90th percentile and 10 at most - the tight prior already shrinks
# it - so excluding it buys almost no protection while discarding the only signal tied
# to the actual draft. Half strength captures it without letting it lead.
# 1.0 since 2026-07-29, up from 0.50. The half-strength scale was set when the hero model
# had no measured edge - true at 245 matches, and no longer true. At 340 it clears the
# pre-registered bar in two rounds, on shuffle splits AND walk-forward validation, in three
# independent implementations. It is now the only term in the displayed number.
#
# The `hero_evidence` factor below is kept: damping a comp of heroes nobody has seen toward
# 50% is principled, where the 0.50 was arbitrary.
W_HEROES = 1.0

# A hero seen twice should count for less than one seen fifty times. The fit already
# shrinks a thin hero toward zero, but this scales the whole hero term by how well known
# THIS comp is, so a draft full of rarely-seen heroes contributes little and the term
# grows on its own as the corpus fills.
HERO_EVIDENCE_HALF = 10.0

# The crowd's own probability, clamped: a thin 92/8 market must not behave like
# near-certainty just because few people bet.
CROWD_CLAMP = (0.08, 0.92)


def crowd_reliability(spectators: int | None, total_pool: int | None) -> float:
    """How much the market deserves to be believed, 0 to 1.

    Mostly spectator count, because that is closer to independent opinions - one large
    bet moves money without adding information. Calibrated to the bands observed in
    play: 20 spectators is nothing, 100 is about half, 200+ is full.
    """
    q_count = 0.0
    if spectators is not None:
        q_count = min(max((spectators - 20) / 180.0, 0.0), 1.0)

    q_stake = 0.0
    if total_pool:
        q_stake = min(
            max(np.log(max(total_pool, 1) / 20_000.0) / np.log(1_000_000 / 20_000.0), 0.0),
            1.0,
        )

    if spectators is None:
        # Stake alone is weak evidence, so it is capped well below full trust.
        return float(min(0.5, q_stake))
    return float(0.80 * q_count + 0.20 * q_stake)


def hero_evidence(fitted: Fit, heroes: list[str]) -> float:
    """0 to 1: how much has actually been seen of the heroes in this comp."""
    if not heroes:
        return 0.0
    seen = [fitted.appearances.get(h, 0) for h in heroes]
    return float(
        np.mean([n / (n + HERO_EVIDENCE_HALF) for n in seen])
    )


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return float(np.log(p / (1 - p)))


def rating_offset(
    left_rating: int | None,
    right_rating: int | None,
    evidence: dict[int, tuple[int, int]] | None = None,
) -> float:
    """The stated prior as a log-odds offset, signed toward the better-rated side.

    Percentage points are what a person can reason about; log-odds are what the model
    adds. Converting here keeps the table readable and the arithmetic correct - adding
    percentages to a probability is not the same as combining evidence, and would let a
    big gap plus a strong comp exceed 100%.
    """
    if left_rating is None or right_rating is None:
        return 0.0
    gap = left_rating - right_rating
    if gap == 0:
        return 0.0
    nudge = blended_nudge(band_of(gap), evidence)
    if nudge <= 0.0:
        return 0.0
    # Capped below certainty. A 50-point nudge on top of 50% is exactly 100%, which as
    # log-odds is infinite - and no rating gap alone justifies "cannot lose". The cap is
    # also what keeps a 400-point gap from making the comp irrelevant.
    probability = min(0.5 + nudge, MAX_RATING_PROBABILITY)
    offset = float(np.log(probability / (1.0 - probability)))
    return offset if gap > 0 else -offset


def predict(
    fitted: Fit,
    left: list[str],
    right: list[str],
    left_rating: int | None = None,
    right_rating: int | None = None,
    evidence: dict[int, tuple[int, int]] | None = None,
    crowd: float | None = None,
    spectators: int | None = None,
    total_pool: int | None = None,
) -> Prediction:
    """P(left wins), with an 80% interval.

    Unknown heroes - never seen in the data - contribute NOTHING rather than a guess.
    That is deliberate: their absence widens the interval through `weakest_evidence`
    instead of pretending to a strength the data never supported.
    """
    z = np.zeros(len(fitted.beta))
    z[0] = 1.0
    known = 0
    for hero in left:
        column = fitted.index_of(hero)
        if column is not None:
            z[column] += 1.0
            known += 1
    for hero in right:
        column = fitted.index_of(hero)
        if column is not None:
            z[column] -= 1.0
            known += 1

    # The rating gap. While USE_RATING_PRIOR holds, the stated table supplies it as a
    # fixed offset and the fitted coefficient is left out of the sum - mixing a prior
    # with a coefficient fitted on the same signal would count it twice.
    if not USE_RATING_PRIOR and left_rating is not None and right_rating is not None:
        z[-1] = (left_rating - right_rating) / RATING_SCALE

    # Each source contributes its own log-odds, damped by how much it has earned.
    hero_eta = float(z @ fitted.beta)
    weight_heroes = W_HEROES * hero_evidence(fitted, [*left, *right])
    eta = weight_heroes * hero_eta
    signals: list[str] = []
    if known and weight_heroes >= MIN_REPORTABLE_WEIGHT:
        signals.append("heroes")

    if USE_RATING_PRIOR:
        offset = rating_offset(left_rating, right_rating, evidence)
        eta += W_RATING * offset
        if offset != 0.0:
            signals.append("rating")

    crowd_weight = 0.0
    if crowd is not None:
        crowd_weight = W_CROWD * crowd_reliability(spectators, total_pool)
        clamped = min(max(crowd, CROWD_CLAMP[0]), CROWD_CLAMP[1])
        eta += crowd_weight * _logit(clamped)
        if crowd_weight >= MIN_REPORTABLE_WEIGHT:
            signals.append("crowd")
    variance = float(z @ np.linalg.solve(fitted.hessian, z))
    # NOT scaled by the hero weight. Damping the interval along with the term made a
    # comp of UNKNOWN heroes report more confidence, which is backwards: little is known
    # about it, and the interval is where that belongs. A test caught this.
    standard_error = float(np.sqrt(max(variance, 0.0)))

    # 80%, not 95%: this is read in seconds under a countdown, and a 95% band on a few
    # hundred matches is so wide it says nothing at all.
    half = 1.2816 * standard_error

    def sigmoid(value: float) -> float:
        return float(1.0 / (1.0 + np.exp(-value)))

    seen = [fitted.appearances.get(h, 0) for h in (*left, *right)]
    return Prediction(
        p_mid=sigmoid(eta),
        p_low=sigmoid(eta - half),
        p_high=sigmoid(eta + half),
        eta=eta,
        standard_error=standard_error,
        known_heroes=known,
        weakest_evidence=min(seen) if seen else 0,
        signals=tuple(sorted(signals, key=_SIGNAL_ORDER.index)),
    )


# The log is a stream of one-line status messages, so a number worth acting on has to
# stop looking like one. Blank lines and a rule around it are the whole trick: the eye
# finds it without reading. The panel in the UI is the real home for this; until that
# exists, this is what a person watching a draft actually sees.
_RULE = "=" * 46


def format_odds(
    prediction: Prediction,
    locked: int,
    gate: str | None,
) -> list[str]:
    """The odds block, as lines to log. `gate` is why it is NOT actionable, or None.

    Never a bare percentage. A number without its interval invites acting on a coin
    flip that happens to read 54%, and `unknown` is a first-class answer here - it means
    sit this round out, which is a decision, not a failure.
    """
    if gate is not None:
        return ["", _RULE, f"  ODDS: not enough data - {gate}", _RULE, ""]

    left = prediction.p_mid * 100.0
    right = 100.0 - left
    if VALIDATED:
        header = "ODDS"
    else:
        # Naming the ingredients is the honest version of a disclaimer nobody reads.
        # "rating + crowd" tells the person watching that the crowd is already inside
        # this number - which is what they need to judge whether it tells them anything
        # they did not have from the screen.
        made_of = " + ".join(prediction.signals) or "nothing measurable"
        header = f"ODDS from {made_of} (UNPROVEN - not yet checked against results)"
    band = f"{prediction.p_low * 100:.0f}-{prediction.p_high * 100:.0f}%"
    trust = (
        "high"
        if prediction.standard_error <= 0.25
        else "medium"
        if prediction.standard_error <= 0.45
        else "low"
    )
    return [
        "",
        _RULE,
        f"  {header}",
        f"  BLUE {left:.0f}%   |   RED {right:.0f}%",
        f"  80% interval {band}   trust: {trust}",
        f"  {locked}/6 heroes identified",
        _RULE,
        "",
    ]


MIN_LOCKED_FOR_ODDS = 4
# Below this many decisive matches for the theme, the number is not worth showing. The
# design's own gate is 75; this is the same idea applied to what has actually been
# collected, and it is deliberately a floor rather than a target.
MIN_MATCHES_FOR_ODDS = 40


def load_matches(rows: list[tuple]) -> list[Match]:
    """Group `MatchStore.matches_for_fit` rows into complete three-a-side matches.

    A match missing a hero on either side is DROPPED, not padded: a 2v3 comp teaches the
    model that two heroes beat three.
    """
    grouped: dict[int, dict] = {}
    for (
        match_id,
        outcome,
        theme_id,
        event_id,
        left_player,
        right_player,
        left_rating,
        right_rating,
        side,
        slug,
    ) in rows:
        entry = grouped.setdefault(
            match_id,
            {
                "outcome": outcome,
                "theme_id": theme_id,
                "event_id": event_id,
                "left_player": left_player,
                "right_player": right_player,
                "left_rating": left_rating,
                "right_rating": right_rating,
                "left": [],
                "right": [],
            },
        )
        if side in ("left", "right"):
            entry[side].append(slug)

    out: list[Match] = []
    for entry in grouped.values():
        if len(entry["left"]) != 3 or len(entry["right"]) != 3:
            continue
        out.append(
            Match(
                left=tuple(entry["left"]),
                right=tuple(entry["right"]),
                left_won=entry["outcome"] == "left",
                theme_id=entry["theme_id"],
                left_player=entry["left_player"],
                right_player=entry["right_player"],
                left_rating=entry["left_rating"],
                right_rating=entry["right_rating"],
                event_id=entry["event_id"],
            )
        )
    return out


# Whether the model has ever been shown to beat "predict the base rate" out of sample.
# It has NOT, on the first 245 matches: best variant 0.6967 against 0.6993, winning 15
# of 25 splits, where the design asks for a 0.01 margin and 80% of splits. Until that
# changes the number is shown as UNPROVEN - visible, because watching it move against
# real drafts is how we find out whether it is learning anything, but never dressed up
# as advice.
VALIDATED = False


def gate_reason(
    fitted: Fit | None,
    identified: int,
    theme_matches: int,
    has_ratings: bool = False,
) -> str | None:
    """Why the odds must NOT be shown, or None if they may be.

    Order matters only for the message; any one of these is disqualifying.

    `identified` is how many heroes were READ, not how many are locked. On the locked
    screen all six are locked by definition, so a single unreadable cell used to gate a
    complete draft with "3/6 picks locked, need 4" - a message that was false twice over.
    Gating on what was read is right; describing it as locked was not.
    """
    if identified < MIN_LOCKED_FOR_ODDS:
        return (
            f"{identified}/6 heroes identified, need {MIN_LOCKED_FOR_ODDS}"
        )
    if has_ratings:
        # The rating prior is informative on its own and does not depend on collected
        # matches at all, so a thin hero model is no reason to withhold the number. What
        # it IS is a stated belief rather than a measurement, which the label says.
        return None
    if fitted is None or fitted.matches == 0:
        return "no matches collected yet"
    if theme_matches < MIN_MATCHES_FOR_ODDS:
        return f"{theme_matches} matches for this theme, need {MIN_MATCHES_FOR_ODDS}"
    return None
