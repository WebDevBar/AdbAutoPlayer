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

Cross-theme data is included at a LOWER WEIGHT rather than excluded. Themes change the
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
# 0.15, not the design's 0.30. Measured out of sample on the first 245 collected
# matches (25 shuffle splits, 80/20): 0.30 scored 0.7006 against a 0.6993 baseline -
# WORSE than predicting the base rate - and 0.15 scored 0.6967. Thin data wants a
# tighter prior; revisit when there are thousands of matches rather than hundreds.
SIGMA_THETA = 0.15  # hero strength
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
# higher-rated side. This is a STATED PRIOR from watching the event, not a measurement:
# no collected match carried a rating until 2026-07-28, so there is nothing to fit yet.
# It is used because a considered prior beats a coefficient fitted on zero observations,
# and it is a table rather than a formula so that replacing any band with a measured
# number later is a one-line change.
#
# Read as: a 150-point gap makes the better-rated side about 72%, not 50%.
RATING_NUDGE = (
    # (gap at least, percentage points off 50)
    (400, 0.50),
    (300, 0.40),
    (250, 0.35),
    (200, 0.30),
    (150, 0.22),
    (125, 0.15),
    (100, 0.10),
    (50, 0.05),
    (0, 0.015),
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
# theme being predicted. Same game, same heroes, different pool and battlefield rules.
CROSS_THEME_WEIGHT = 0.35

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
    """Fitted parameters and what they were fitted on."""

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


def design(
    matches: list[Match], theme_id: int | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[str, ...], tuple[str, ...]]:
    """Build the design matrix, outcomes and per-match weights.

    Column 0 is the intercept, then one column per hero, then one per player. A hero is
    +1 on the left and -1 on the right, so only the DIFFERENCE between the sides can be
    learned - which is all a win/lose outcome contains.
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
        if theme_id is not None and match.theme_id != theme_id:
            w[row] = CROSS_THEME_WEIGHT

    return x, y, w, heroes, players


def fit(matches: list[Match], theme_id: int | None = None) -> Fit:
    """Newton-Raphson to convergence. No scipy - it is not in the bundled runtime.

    The Hessian is symmetric positive definite because XᵀWX is positive semi-definite
    and the prior term is strictly positive, so the solve is always well posed. That is
    also what makes the variance in `predict` safe to compute.
    """
    x, y, w, heroes, players = design(matches, theme_id)
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

    appearances: dict[str, int] = {}
    for match in matches:
        for hero in (*match.left, *match.right):
            appearances[hero] = appearances.get(hero, 0) + 1

    return Fit(
        heroes=heroes,
        players=players,
        beta=beta,
        hessian=hessian,
        matches=len(matches),
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

    @property
    def favours(self) -> str:
        return "left" if self.p_mid >= 0.5 else "right"


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

    Scoped PER EVENT and pooled across themes within it. Both halves matter: a rating
    means the same thing whichever theme is running, so splitting the evidence by theme
    would starve every band for no reason - but ratings RESET between events, and the
    game resets rank points on each theme change too, so a gap from a different event is
    a different scale entirely and must not be pooled.

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
W_RATING = 0.60
W_CROWD = 0.70
# Not zero. Measured over 120 real comps, the hero term moves the number by 3 points
# typically, 6 at the 90th percentile and 10 at most - the tight prior already shrinks
# it - so excluding it buys almost no protection while discarding the only signal tied
# to the actual draft. Half strength captures it without letting it lead.
W_HEROES = 0.50

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

    if USE_RATING_PRIOR:
        eta += W_RATING * rating_offset(left_rating, right_rating, evidence)

    crowd_weight = 0.0
    if crowd is not None:
        crowd_weight = W_CROWD * crowd_reliability(spectators, total_pool)
        clamped = min(max(crowd, CROWD_CLAMP[0]), CROWD_CLAMP[1])
        eta += crowd_weight * _logit(clamped)
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
    source: str = "model",
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
    if source == "rating":
        header = "ODDS (from the rating gap - a stated prior, not yet measured)"
    elif VALIDATED:
        header = "ODDS"
    else:
        header = "ODDS (UNPROVEN - not yet better than a coin flip)"
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
        f"  {locked}/6 picks locked, weakest hero seen {prediction.weakest_evidence}x",
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
    locked: int,
    theme_matches: int,
    has_ratings: bool = False,
) -> str | None:
    """Why the odds must NOT be shown, or None if they may be.

    Order matters only for the message; any one of these is disqualifying.
    """
    if locked < MIN_LOCKED_FOR_ODDS:
        return f"{locked}/6 picks locked, need {MIN_LOCKED_FOR_ODDS}"
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
