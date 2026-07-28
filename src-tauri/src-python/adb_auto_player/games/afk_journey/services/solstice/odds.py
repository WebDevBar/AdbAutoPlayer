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

    x = np.zeros((len(matches), 1 + len(heroes) + len(players)))
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
    penalty[1 + len(heroes) :] = 1.0 / SIGMA_PHI**2

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


def predict(fitted: Fit, left: list[str], right: list[str]) -> Prediction:
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

    eta = float(z @ fitted.beta)
    variance = float(z @ np.linalg.solve(fitted.hessian, z))
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


def format_odds(prediction: Prediction, locked: int, gate: str | None) -> list[str]:
    """The odds block, as lines to log. `gate` is why it is NOT actionable, or None.

    Never a bare percentage. A number without its interval invites acting on a coin
    flip that happens to read 54%, and `unknown` is a first-class answer here - it means
    sit this round out, which is a decision, not a failure.
    """
    if gate is not None:
        return ["", _RULE, f"  ODDS: not enough data - {gate}", _RULE, ""]

    left = prediction.p_mid * 100.0
    right = 100.0 - left
    header = "ODDS" if VALIDATED else "ODDS (UNPROVEN - not yet better than a coin flip)"
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
    for match_id, outcome, theme_id, left_player, right_player, side, slug in rows:
        entry = grouped.setdefault(
            match_id,
            {
                "outcome": outcome,
                "theme_id": theme_id,
                "left_player": left_player,
                "right_player": right_player,
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
    fitted: Fit | None, locked: int, theme_matches: int
) -> str | None:
    """Why the odds must NOT be shown, or None if they may be.

    Order matters only for the message; any one of these is disqualifying.
    """
    if locked < MIN_LOCKED_FOR_ODDS:
        return f"{locked}/6 picks locked, need {MIN_LOCKED_FOR_ODDS}"
    if fitted is None or fitted.matches == 0:
        return "no matches collected yet"
    if theme_matches < MIN_MATCHES_FOR_ODDS:
        return f"{theme_matches} matches for this theme, need {MIN_MATCHES_FOR_ODDS}"
    return None
