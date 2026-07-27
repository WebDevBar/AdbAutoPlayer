# Solstice Clash Live Odds (Mode C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fit a regularised Bradley-Terry model to collected Solstice Clash matches and show a live, honestly-gated win probability during the spectate draft, without ever placing a bet.

**Architecture:** Three new pure modules under `services/solstice/` - `identity.py` (player name vetting), `odds.py` (fit and predict), `validate.py` (out-of-sample validation and the display gate) - plus schema v4 tables, store methods, one device-side prerequisite, and the mixin/UI wiring. Everything statistical is pure and fixture-testable with no device and no GUI.

**Tech Stack:** Python 3.13, numpy 2.4.6, sqlite3, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-27-solstice-clash-odds-design.md`

## Global Constraints

- **scipy is NOT available and must NOT be added.** numpy only. Fit with Newton-Raphson (spec section 3). Verified 2026-07-27: numpy is in `src-tauri/pyproject.toml` and the bundled runtime; scipy is in neither `uv.lock` nor `src-tauri/pyembed/`.
- **Never form `H⁻¹` explicitly.** Use `numpy.linalg.solve(H, g)` and `z @ numpy.linalg.solve(H, z)`.
- **The mode never places a bet.** No tap that commits a wager, under any condition.
- **`match_odds` is already taken** and holds the GAME's betting pool (`left_pool`, `right_pool`, `spectators`). Our model predictions go in a new `model_prediction` table. Do not overload `match_odds`.
- **Source of truth paths.** Code: `src-tauri/src-python/adb_auto_player/games/afk_journey/`. Tests: `src-tauri/src-python/tests/games/afk_journey/`. Ignore `src-tauri/build/`, `target/`, and `src-tauri/pyembed/` - those are build outputs.
- **Priors, fixed:** `sigma_theta = 0.30`, `sigma_phi = 0.50`, `sigma_beta = 1.0`, `sigma_pick = 0.30`.
- **Outcome values are `'left'` / `'right'` / `'draw'`.** Never blue/red - colour is an observation channel, not a position.
- **Existing tests must stay green.** Some assert deltas against the shipped `heroes.sqlite`, which holds real collected data; never assert absolute row counts.
- **K&R braces / existing file style.** Edit code with `git apply` or a Python script, never the Edit tool.

---

### Task 1: Player identity vetting

Implements spec section 9. Pure, no numpy, no database.

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/identity.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_identity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `resolve_player_keys(raw_names: list[str | None]) -> list[str | None]` - batch, order-independent, returns a trusted key or `None` per input. `KNOWN_BAD: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

```python
"""Player identity vetting."""

from adb_auto_player.games.afk_journey.services.solstice.identity import (
    resolve_player_keys,
)


def test_normalises_and_keeps_good_names():
    assert resolve_player_keys(["  KONTROL ", "Falsetto"]) == ["KONTROL", "Falsetto"]


def test_rejects_too_short():
    assert resolve_player_keys(["ab", "Falsetto"]) == [None, "Falsetto"]


def test_rejects_known_bad():
    assert resolve_player_keys(["GAME", "Falsetto"]) == [None, "Falsetto"]


def test_rejects_strict_prefix_of_another_name():
    # 'Kru' is a strict prefix of 'Krusty', so it is suspected truncation.
    assert resolve_player_keys(["Kru", "Krusty"]) == [None, "Krusty"]


def test_exact_duplicates_are_not_prefixes_of_each_other():
    assert resolve_player_keys(["Falsetto", "Falsetto"]) == ["Falsetto", "Falsetto"]


def test_order_does_not_change_the_outcome():
    forward = resolve_player_keys(["Kru", "Krusty", "KONTROL"])
    reverse = list(reversed(resolve_player_keys(["KONTROL", "Krusty", "Kru"])))
    assert forward == reverse


def test_none_and_blank_pass_through_as_none():
    assert resolve_player_keys([None, "", "   "]) == [None, None, None]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_identity.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named '...solstice.identity'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Vetting OCR-read player names into trustworthy model keys.

Player identity is a fitted parameter, so a wrong identity is a wrong model. Two
failure modes, both seen in real collected data:

- splitting - one player read two ways becomes two players with half a history each;
- merging - several players read the same way become one phantom player whose term
  absorbs all of their records.

Merging is the dangerous one: splitting only weakens a term toward its prior, while
merging manufactures a confident term out of unrelated matches. Both observed: every
name overlapped by the account badge truncated to `GAME`, and a collected match stores
`【kru`, plainly a partial read.
"""

from __future__ import annotations

import unicodedata

MIN_LENGTH = 3

# Names known to be artefacts rather than players.
KNOWN_BAD: frozenset[str] = frozenset({"GAME"})


def _normalise(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = unicodedata.normalize("NFKC", raw).strip()
    cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch)[0] != "C")
    return cleaned or None


def resolve_player_keys(raw_names: list[str | None]) -> list[str | None]:
    """Map each raw OCR name to a trusted key, or to None if it cannot be trusted.

    Batch by design: the truncation rule compares each name against every other name
    observed, so it is only deterministic when the whole set is known at once. Callers
    derive keys at fit time and never store them as a match column.
    """
    normalised = [_normalise(raw) for raw in raw_names]
    distinct = {name for name in normalised if name is not None}

    def trusted(name: str | None) -> str | None:
        if name is None or len(name) < MIN_LENGTH or name in KNOWN_BAD:
            return None
        # Suspected truncation: a strict prefix of some OTHER observed name.
        if any(other != name and other.startswith(name) for other in distinct):
            return None
        return name

    return [trusted(name) for name in normalised]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_identity.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/identity.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_identity.py
git commit -m "feat(solstice): vet OCR player names into trusted model keys"
```

---

### Task 2: Design matrix and model fit

Implements spec section 3. Pure numpy, no database.

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/odds.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_odds_fit.py`

**Interfaces:**
- Consumes: `resolve_player_keys` from Task 1.
- Produces:
  - `@dataclass(frozen=True) TrainingMatch(left_heroes: tuple[str, ...], right_heroes: tuple[str, ...], left_player: str | None, right_player: str | None, left_won: bool)`
  - `@dataclass(frozen=True) FittedModel(params: np.ndarray, hessian: np.ndarray, index: dict[str, int], hero_appearances: dict[str, int], n_matches: int)`
  - `SIGMA_THETA = 0.30`, `SIGMA_PHI = 0.50`, `SIGMA_BETA = 1.0`, `SIGMA_PICK = 0.30`
  - `build_design(matches) -> tuple[np.ndarray, np.ndarray, dict[str, int], np.ndarray]` returning `(X, y, index, prior_precision)`
  - `fit(matches) -> FittedModel`

`index` maps a parameter name to its column: `"__intercept__"`, `"hero:<slug>"`, `"player:<key>"`.

- [ ] **Step 1: Write the failing test**

```python
"""Design matrix construction and Newton fitting."""

import numpy as np
import pytest

from adb_auto_player.games.afk_journey.services.solstice.odds import (
    SIGMA_THETA,
    TrainingMatch,
    build_design,
    fit,
)


def _match(left, right, left_won, lp="Alice", rp="Bob"):
    return TrainingMatch(tuple(left), tuple(right), lp, rp, left_won)


def test_design_encodes_sides_as_plus_and_minus_one():
    m = _match(["a", "b", "c"], ["d", "e", "f"], True)
    x, y, index, _ = build_design([m])
    assert x[0, index["hero:a"]] == 1.0
    assert x[0, index["hero:d"]] == -1.0
    assert x[0, index["__intercept__"]] == 1.0
    assert x[0, index["player:Alice"]] == 1.0
    assert x[0, index["player:Bob"]] == -1.0
    assert y[0] == 1.0


def test_untrusted_player_gets_no_column():
    # 'GAME' is a known-bad read, so no player term is created for it.
    m = _match(["a", "b", "c"], ["d", "e", "f"], True, lp="GAME")
    _, _, index, _ = build_design([m])
    assert "player:GAME" not in index
    assert "player:Bob" in index


def test_hero_on_both_sides_cancels():
    m = _match(["a", "b", "c"], ["a", "e", "f"], True)
    x, _, index, _ = build_design([m])
    assert x[0, index["hero:a"]] == 0.0


def test_prior_precision_matches_the_spec():
    m = _match(["a", "b", "c"], ["d", "e", "f"], True)
    _, _, index, prior = build_design([m])
    assert prior[index["hero:a"]] == pytest.approx(1.0 / SIGMA_THETA**2)
    assert prior[index["player:Alice"]] == pytest.approx(1.0 / 0.50**2)
    assert prior[index["__intercept__"]] == pytest.approx(1.0 / 1.0**2)


def test_single_win_barely_moves_a_hero():
    """Spec section 4: one match must not make a hero look dominant."""
    model = fit([_match(["a", "b", "c"], ["d", "e", "f"], True)])
    theta_a = model.params[model.index["hero:a"]]
    assert 0.0 < theta_a < 0.10


def test_a_consistently_winning_hero_gets_a_positive_strength():
    matches = [
        _match(["a", f"b{i}", f"c{i}"], [f"d{i}", f"e{i}", f"f{i}"], True)
        for i in range(30)
    ]
    model = fit(matches)
    assert model.params[model.index["hero:a"]] > 0.2


def test_hessian_is_symmetric_positive_definite():
    matches = [
        _match(["a", f"b{i}", "c"], ["d", f"e{i}", "f"], i % 2 == 0) for i in range(10)
    ]
    model = fit(matches)
    assert np.allclose(model.hessian, model.hessian.T)
    # Cholesky succeeds only for a positive definite matrix.
    np.linalg.cholesky(model.hessian)


def test_fit_converges_on_perfectly_separable_data():
    """Ridge keeps parameters finite where unregularised logistic would diverge."""
    matches = [
        _match(["a", f"b{i}", f"c{i}"], [f"d{i}", f"e{i}", f"f{i}"], True)
        for i in range(50)
    ]
    model = fit(matches)
    assert np.all(np.isfinite(model.params))
    assert abs(model.params[model.index["hero:a"]]) < 2.0


def test_appearances_are_counted():
    matches = [_match(["a", "b", "c"], ["d", "e", "f"], True)] * 3
    model = fit(matches)
    assert model.hero_appearances["a"] == 3
    assert model.n_matches == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_odds_fit.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named '...solstice.odds'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Regularised Bradley-Terry odds for Solstice Clash.

P(left wins) = sigmoid(b0 + sum_left theta_h - sum_right theta_h + phi_left - phi_right)

Fitted by Newton-Raphson in pure numpy. scipy is deliberately not used: it is absent
from this repo's lockfile and bundled runtime, the ridge penalty makes the objective
strictly convex, and the Hessian Newton needs each iteration is the same matrix the
Laplace confidence interval needs afterwards.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from .identity import resolve_player_keys

SIGMA_THETA = 0.30  # hero strength prior SD
SIGMA_PHI = 0.50  # player skill prior SD
SIGMA_BETA = 1.0  # intercept prior SD
SIGMA_PICK = 0.30  # SD attributed to one unknown pick

INTERCEPT = "__intercept__"
_MAX_ITER = 100
_TOL = 1e-8


@dataclass(frozen=True)
class TrainingMatch:
    left_heroes: tuple[str, ...]
    right_heroes: tuple[str, ...]
    left_player: str | None
    right_player: str | None
    left_won: bool


@dataclass(frozen=True)
class FittedModel:
    params: np.ndarray
    hessian: np.ndarray
    index: dict[str, int]
    hero_appearances: dict[str, int]
    n_matches: int


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Branchless-stable: avoids overflow for large-magnitude z.
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def build_design(
    matches: list[TrainingMatch],
) -> tuple[np.ndarray, np.ndarray, dict[str, int], np.ndarray]:
    """Build (X, y, index, prior_precision) from training matches."""
    raw_names: list[str | None] = []
    for m in matches:
        raw_names.append(m.left_player)
        raw_names.append(m.right_player)
    keys = resolve_player_keys(raw_names)

    index: dict[str, int] = {INTERCEPT: 0}
    for m in matches:
        for slug in (*m.left_heroes, *m.right_heroes):
            index.setdefault(f"hero:{slug}", len(index))
    for key in keys:
        if key is not None:
            index.setdefault(f"player:{key}", len(index))

    x = np.zeros((len(matches), len(index)), dtype=float)
    y = np.zeros(len(matches), dtype=float)
    for i, m in enumerate(matches):
        x[i, 0] = 1.0
        for slug in m.left_heroes:
            x[i, index[f"hero:{slug}"]] += 1.0
        for slug in m.right_heroes:
            x[i, index[f"hero:{slug}"]] -= 1.0
        left_key, right_key = keys[2 * i], keys[2 * i + 1]
        if left_key is not None:
            x[i, index[f"player:{left_key}"]] += 1.0
        if right_key is not None:
            x[i, index[f"player:{right_key}"]] -= 1.0
        y[i] = 1.0 if m.left_won else 0.0

    prior = np.empty(len(index), dtype=float)
    for name, col in index.items():
        if name == INTERCEPT:
            prior[col] = 1.0 / SIGMA_BETA**2
        elif name.startswith("player:"):
            prior[col] = 1.0 / SIGMA_PHI**2
        else:
            prior[col] = 1.0 / SIGMA_THETA**2
    return x, y, index, prior


def fit(matches: list[TrainingMatch]) -> FittedModel:
    """Fit by Newton-Raphson. Converges in a handful of iterations."""
    x, y, index, prior = build_design(matches)
    b = np.zeros(x.shape[1], dtype=float)
    hessian = np.diag(prior)

    for _ in range(_MAX_ITER):
        p = _sigmoid(x @ b)
        g = x.T @ (p - y) + prior * b
        w = p * (1.0 - p)
        hessian = (x.T * w) @ x + np.diag(prior)
        if np.max(np.abs(g)) < _TOL:
            break
        b = b - np.linalg.solve(hessian, g)

    appearances: Counter[str] = Counter()
    for m in matches:
        for slug in (*m.left_heroes, *m.right_heroes):
            appearances[slug] += 1

    return FittedModel(
        params=b,
        hessian=hessian,
        index=index,
        hero_appearances=dict(appearances),
        n_matches=len(matches),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_odds_fit.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/odds.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_odds_fit.py
git commit -m "feat(solstice): ridge Bradley-Terry fit by Newton-Raphson in numpy"
```

---

### Task 3: Prediction with marginalisation and confidence

Implements spec sections 5 and 6. Appends to `odds.py`.

**Files:**
- Modify: `.../services/solstice/odds.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_odds_predict.py`

**Interfaces:**
- Consumes: `FittedModel`, `SIGMA_PICK`, `SIGMA_PHI`, `SIGMA_THETA` from Task 2.
- Produces:
  - `@dataclass(frozen=True) DraftState(left_heroes: tuple[str, ...], right_heroes: tuple[str, ...], left_player: str | None, right_player: str | None, n_unknown: int)`
  - `@dataclass(frozen=True) Prediction(p_mid: float, p_low: float, p_high: float, eta: float, se: float, trust: str, total_appearances: int, weakest_appearances: int)`
  - `predict(model: FittedModel, draft: DraftState) -> Prediction`

- [ ] **Step 1: Write the failing test**

```python
"""Partial-draft prediction, marginalisation, and intervals."""

import math

import pytest

from adb_auto_player.games.afk_journey.services.solstice.odds import (
    DraftState,
    TrainingMatch,
    fit,
    predict,
)


def _training(n=40):
    return [
        TrainingMatch(
            ("a", f"b{i}", f"c{i}"),
            (f"d{i}", f"e{i}", f"f{i}"),
            "Alice",
            "Bob",
            i % 3 != 0,
        )
        for i in range(n)
    ]


def test_complete_draft_has_no_unknown_variance():
    model = fit(_training())
    full = DraftState(("a", "b0", "c0"), ("d0", "e0", "f0"), "Alice", "Bob", 0)
    partial = DraftState(("a",), (), "Alice", "Bob", 5)
    assert predict(model, full).se < predict(model, partial).se


def test_unknown_picks_shrink_the_estimate_toward_half():
    model = fit(_training())
    known = DraftState(("a", "b0", "c0"), ("d0", "e0", "f0"), "Alice", "Bob", 0)
    unknown = DraftState(("a", "b0", "c0"), (), "Alice", "Bob", 3)
    assert abs(predict(model, unknown).p_mid - 0.5) < abs(
        predict(model, known).p_mid - 0.5
    )


def test_p_mid_lies_inside_the_interval():
    model = fit(_training())
    p = predict(model, DraftState(("a",), ("d0",), "Alice", "Bob", 4))
    assert p.p_low <= p.p_mid <= p.p_high


def test_marginalisation_uses_the_probit_approximation():
    """Spec section 5: p_mid = sigmoid(eta / sqrt(1 + pi * var / 8))."""
    model = fit(_training())
    draft = DraftState(("a", "b0"), ("d0",), "Alice", "Bob", 3)
    p = predict(model, draft)
    expected = 1.0 / (
        1.0 + math.exp(-p.eta / math.sqrt(1.0 + math.pi * p.se**2 / 8.0))
    )
    assert p.p_mid == pytest.approx(expected)


def test_unseen_hero_widens_the_interval():
    model = fit(_training())
    seen = DraftState(("a", "b0", "c0"), ("d0", "e0", "f0"), "Alice", "Bob", 0)
    unseen = DraftState(("a", "b0", "zzz"), ("d0", "e0", "f0"), "Alice", "Bob", 0)
    assert predict(model, unseen).se > predict(model, seen).se


def test_unseen_player_widens_the_interval():
    model = fit(_training())
    seen = DraftState(("a", "b0", "c0"), ("d0", "e0", "f0"), "Alice", "Bob", 0)
    unseen = DraftState(("a", "b0", "c0"), ("d0", "e0", "f0"), "Nobody", "Bob", 0)
    assert predict(model, unseen).se > predict(model, seen).se


def test_untrusted_player_is_treated_as_unseen_not_as_a_player():
    model = fit(_training())
    p = predict(model, DraftState(("a",), ("d0",), "GAME", "Bob", 4))
    assert p.se > 0.0
    assert 0.0 < p.p_mid < 1.0


def test_trust_labels_follow_the_thresholds():
    model = fit(_training())
    # Five unknown picks force SE well above 0.60.
    assert predict(model, DraftState((), (), None, None, 5)).trust == "low"


def test_evidence_counts_report_the_weakest_hero():
    model = fit(_training())
    p = predict(model, DraftState(("a", "b0"), (), "Alice", "Bob", 4))
    assert p.total_appearances >= p.weakest_appearances
    assert p.weakest_appearances >= 1


def test_evidence_counts_are_zero_when_nothing_is_locked():
    model = fit(_training())
    p = predict(model, DraftState((), (), None, None, 6))
    assert p.total_appearances == 0
    assert p.weakest_appearances == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_odds_predict.py -v`
Expected: FAIL, `ImportError: cannot import name 'DraftState'`

- [ ] **Step 3: Write minimal implementation**

Append to `odds.py`:

```python
@dataclass(frozen=True)
class DraftState:
    left_heroes: tuple[str, ...]
    right_heroes: tuple[str, ...]
    left_player: str | None
    right_player: str | None
    n_unknown: int


@dataclass(frozen=True)
class Prediction:
    p_mid: float
    p_low: float
    p_high: float
    eta: float
    se: float
    trust: str
    total_appearances: int
    weakest_appearances: int


_Z80 = 1.2815515655446004  # 80% two-sided normal quantile


def _trust_label(se: float) -> str:
    if se < 0.25:
        return "high"
    if se <= 0.60:
        return "medium"
    return "low"


def predict(model: FittedModel, draft: DraftState) -> Prediction:
    """Predict P(left wins | locked picks), marginalising over what is not yet known.

    The reported probability is NOT sigmoid(eta): sigmoid is nonlinear, so the mean of
    the squashed value is not the squash of the mean. Using the point value would
    overstate confidence exactly when the draft is incomplete, which is when this is
    read. The probit approximation shrinks toward 0.5 in proportion to what is unknown.
    """
    z = np.zeros(len(model.index), dtype=float)
    z[model.index[INTERCEPT]] = 1.0

    # Unknown picks each contribute one pick's worth of variance, and unseen
    # participants contribute their prior - neither has a row in the Hessian, so
    # neither would otherwise be accounted for at all.
    extra_var = draft.n_unknown * SIGMA_PICK**2

    for slug in draft.left_heroes:
        col = model.index.get(f"hero:{slug}")
        if col is None:
            extra_var += SIGMA_THETA**2
        else:
            z[col] += 1.0
    for slug in draft.right_heroes:
        col = model.index.get(f"hero:{slug}")
        if col is None:
            extra_var += SIGMA_THETA**2
        else:
            z[col] -= 1.0

    keys = resolve_player_keys([draft.left_player, draft.right_player])
    for key, sign in ((keys[0], 1.0), (keys[1], -1.0)):
        col = None if key is None else model.index.get(f"player:{key}")
        if col is None:
            extra_var += SIGMA_PHI**2
        else:
            z[col] += sign

    eta = float(z @ model.params)
    var = float(z @ np.linalg.solve(model.hessian, z)) + extra_var
    se = math.sqrt(max(var, 0.0))

    eta_shrunk = eta / math.sqrt(1.0 + math.pi * var / 8.0)
    p_mid = float(_sigmoid(np.array([eta_shrunk]))[0])
    p_low = float(_sigmoid(np.array([eta - _Z80 * se]))[0])
    p_high = float(_sigmoid(np.array([eta + _Z80 * se]))[0])

    locked = (*draft.left_heroes, *draft.right_heroes)
    counts = [model.hero_appearances.get(slug, 0) for slug in locked]
    return Prediction(
        p_mid=p_mid,
        p_low=p_low,
        p_high=p_high,
        eta=eta,
        se=se,
        trust=_trust_label(se),
        total_appearances=sum(counts),
        weakest_appearances=min(counts) if counts else 0,
    )
```

Add `import math` to the top of `odds.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_odds_predict.py -v`
Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/odds.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_odds_predict.py
git commit -m "feat(solstice): marginalised partial-draft prediction with Laplace intervals"
```

---

### Task 4: Validation and the display gate

Implements spec sections 7 and 8. Pure; deterministic via an explicit seed.

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/validate.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_validate.py`

**Interfaces:**
- Consumes: `TrainingMatch`, `DraftState`, `Prediction`, `fit`, `predict` from Tasks 2-3.
- Produces:
  - `@dataclass(frozen=True) ValidationResult(model_logloss: float, baseline_a_logloss: float, baseline_b_logloss: float, model_brier: float, split_wins: int, n_splits: int, passes: bool)`
  - `cross_validate(matches, n_splits=100, seed=0) -> ValidationResult`
  - `gate_open(result, prediction, n_decisive, min_hero_appearances) -> bool`
  - Constants `MIN_DECISIVE = 75`, `MIN_HERO_APPEARANCES = 5`, `MAX_SE = 0.45`, `MIN_MARGIN_FROM_HALF = 0.086` (`0.586 - 0.5`), `LOGLOSS_MARGIN = 0.01`, `MIN_SPLIT_WIN_RATE = 0.80`

- [ ] **Step 1: Write the failing test**

```python
"""Out-of-sample validation and the display gate."""

from adb_auto_player.games.afk_journey.services.solstice.odds import (
    DraftState,
    Prediction,
    TrainingMatch,
)
from adb_auto_player.games.afk_journey.services.solstice.validate import (
    ValidationResult,
    cross_validate,
    gate_open,
)


def _noise(n=200):
    """Coin-flip outcomes: no hero signal exists to find."""
    return [
        TrainingMatch(
            (f"a{i % 9}", f"b{i % 7}", f"c{i % 5}"),
            (f"d{i % 8}", f"e{i % 6}", f"f{i % 4}"),
            None,
            None,
            i % 2 == 0,
        )
        for i in range(n)
    ]


def _signal(n=200):
    """Hero 'super' always wins; everything else is filler."""
    out = []
    for i in range(n):
        left = i % 2 == 0
        heroes = ("super", f"b{i % 7}", f"c{i % 5}")
        other = (f"d{i % 8}", f"e{i % 6}", f"f{i % 4}")
        out.append(
            TrainingMatch(
                heroes if left else other,
                other if left else heroes,
                None,
                None,
                left,
            )
        )
    return out


def _prediction(se=0.2, p_mid=0.70):
    return Prediction(p_mid, 0.6, 0.8, 0.85, se, "high", 100, 20)


def test_noise_does_not_pass_validation():
    assert cross_validate(_noise(), n_splits=20, seed=1).passes is False


def test_real_signal_passes_validation():
    assert cross_validate(_signal(), n_splits=20, seed=1).passes is True


def test_validation_is_deterministic_for_a_seed():
    a = cross_validate(_signal(), n_splits=20, seed=7)
    b = cross_validate(_signal(), n_splits=20, seed=7)
    assert a == b


def test_baseline_a_is_the_coin_flip_logloss():
    result = cross_validate(_noise(), n_splits=20, seed=1)
    assert abs(result.baseline_a_logloss - 0.6931) < 0.01


def test_gate_stays_shut_when_validation_fails():
    failed = ValidationResult(0.69, 0.6931, 0.69, 0.25, 5, 20, False)
    assert gate_open(failed, _prediction(), n_decisive=200, min_hero_appearances=20) is False


def test_gate_stays_shut_below_the_match_minimum():
    ok = ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, True)
    assert gate_open(ok, _prediction(), n_decisive=74, min_hero_appearances=20) is False


def test_gate_stays_shut_when_a_locked_hero_is_thin():
    ok = ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, True)
    assert gate_open(ok, _prediction(), n_decisive=200, min_hero_appearances=4) is False


def test_gate_stays_shut_when_se_is_too_wide():
    ok = ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, True)
    assert (
        gate_open(ok, _prediction(se=0.46), n_decisive=200, min_hero_appearances=20)
        is False
    )


def test_gate_stays_shut_when_the_call_is_too_close_to_half():
    ok = ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, True)
    assert (
        gate_open(ok, _prediction(p_mid=0.55), n_decisive=200, min_hero_appearances=20)
        is False
    )


def test_gate_opens_when_every_condition_holds():
    ok = ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, True)
    assert gate_open(ok, _prediction(), n_decisive=200, min_hero_appearances=20) is True


def test_three_unknown_picks_can_never_open_the_gate():
    """Spec section 7: SE floor sqrt(3*0.09)=0.52 exceeds MAX_SE=0.45."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import fit, predict

    model = fit(_signal())
    p = predict(model, DraftState(("super",), (), None, None, 3))
    ok = ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, True)
    assert p.se > 0.45
    assert gate_open(ok, p, n_decisive=200, min_hero_appearances=20) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_validate.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named '...solstice.validate'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Out-of-sample validation and the gate that decides whether to show a number.

Beating a 50/50 baseline is not enough: if one side has any structural advantage, a
model can beat 50/50 while learning nothing about heroes. Baseline B - always predict
the training left-win rate - is the real bar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .odds import Prediction, TrainingMatch, fit, predict
from .odds import DraftState

MIN_DECISIVE = 75
MIN_HERO_APPEARANCES = 5
MAX_SE = 0.45
MIN_MARGIN_FROM_HALF = 0.086  # p_mid must be <= 0.414 or >= 0.586
LOGLOSS_MARGIN = 0.01
MIN_SPLIT_WIN_RATE = 0.80
_EPS = 1e-12


@dataclass(frozen=True)
class ValidationResult:
    model_logloss: float
    baseline_a_logloss: float
    baseline_b_logloss: float
    model_brier: float
    split_wins: int
    n_splits: int
    passes: bool


def _logloss(p: float, y: bool) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return -(math.log(p) if y else math.log(1.0 - p))


def cross_validate(
    matches: list[TrainingMatch], n_splits: int = 100, seed: int = 0
) -> ValidationResult:
    """Repeated 80/20 shuffle validation, refitting on each split."""
    rng = np.random.default_rng(seed)
    n = len(matches)
    n_test = max(1, n // 5)

    model_losses: list[float] = []
    base_a_losses: list[float] = []
    base_b_losses: list[float] = []
    briers: list[float] = []
    wins = 0

    for _ in range(n_splits):
        order = rng.permutation(n)
        test_idx = order[:n_test]
        train = [matches[i] for i in order[n_test:]]
        test = [matches[i] for i in test_idx]

        model = fit(train)
        left_rate = sum(1 for m in train if m.left_won) / max(len(train), 1)

        split_model, split_a, split_b, split_brier = [], [], [], []
        for m in test:
            draft = DraftState(
                m.left_heroes, m.right_heroes, m.left_player, m.right_player, 0
            )
            p = predict(model, draft).p_mid
            split_model.append(_logloss(p, m.left_won))
            split_a.append(_logloss(0.5, m.left_won))
            split_b.append(_logloss(left_rate, m.left_won))
            split_brier.append((p - (1.0 if m.left_won else 0.0)) ** 2)

        model_losses.append(float(np.mean(split_model)))
        base_a_losses.append(float(np.mean(split_a)))
        base_b_losses.append(float(np.mean(split_b)))
        briers.append(float(np.mean(split_brier)))
        if model_losses[-1] < base_b_losses[-1]:
            wins += 1

    model_ll = float(np.mean(model_losses))
    base_a = float(np.mean(base_a_losses))
    base_b = float(np.mean(base_b_losses))
    passes = (
        model_ll <= base_a - LOGLOSS_MARGIN
        and model_ll <= base_b - LOGLOSS_MARGIN
        and wins >= math.ceil(MIN_SPLIT_WIN_RATE * n_splits)
    )
    return ValidationResult(
        model_logloss=model_ll,
        baseline_a_logloss=base_a,
        baseline_b_logloss=base_b,
        model_brier=float(np.mean(briers)),
        split_wins=wins,
        n_splits=n_splits,
        passes=passes,
    )


def gate_open(
    result: ValidationResult,
    prediction: Prediction,
    n_decisive: int,
    min_hero_appearances: int,
) -> bool:
    """Every condition in spec section 7 must hold before a number is shown.

    The margin test is applied to the DISPLAYED p_mid rather than to raw eta, so the
    gate and the display can never disagree.
    """
    return (
        result.passes
        and n_decisive >= MIN_DECISIVE
        and min_hero_appearances >= MIN_HERO_APPEARANCES
        and prediction.se <= MAX_SE
        and abs(prediction.p_mid - 0.5) >= MIN_MARGIN_FROM_HALF
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_validate.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/validate.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_validate.py
git commit -m "feat(solstice): out-of-sample validation and the display gate"
```

---

### Task 5: Schema v4 - model fits, validation runs, predictions

**Files:**
- Modify: `data/solstice_clash/schema.sql`
- Modify: `data/solstice_clash/migrate.py` (bump `SCHEMA_VERSION` 3 → 4)
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_schema_v4.py`

**Interfaces:**
- Consumes: existing `match` table.
- Produces: tables `model_fit`, `model_prediction`. **Not** `match_odds`, which already exists and holds the GAME's betting pool.

- [ ] **Step 1: Write the failing test**

```python
"""Schema v4: model fits and logged predictions."""

import sqlite3

import pytest

from tests.games.afk_journey.services.solstice.conftest import fresh_db  # noqa: F401


def test_version_is_four(fresh_db):
    con = sqlite3.connect(fresh_db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 4


def test_model_fit_and_prediction_tables_exist(fresh_db):
    con = sqlite3.connect(fresh_db)
    names = {
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"model_fit", "model_prediction"} <= names


def test_match_odds_still_holds_the_game_pool(fresh_db):
    """Regression: our predictions must not have been merged into the game's pool."""
    con = sqlite3.connect(fresh_db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(match_odds)")}
    assert {"left_pool", "right_pool", "spectators"} <= cols
    assert "p_mid" not in cols


def test_prediction_rejects_a_probability_outside_zero_to_one(fresh_db):
    con = sqlite3.connect(fresh_db)
    con.execute(
        "INSERT INTO model_fit(fitted_at,theme,n_matches,passes_validation) "
        "VALUES('2026-07-27T00:00:00Z','converging-paths',200,1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO model_prediction(fit_id,predicted_at,n_locked,n_unknown,"
            "p_mid,se,gate_open) VALUES(1,'2026-07-27T00:00:00Z',4,2,1.5,0.3,0)"
        )


def test_prediction_is_logged_even_when_the_gate_is_shut(fresh_db):
    con = sqlite3.connect(fresh_db)
    con.execute(
        "INSERT INTO model_fit(fitted_at,theme,n_matches,passes_validation) "
        "VALUES('2026-07-27T00:00:00Z','converging-paths',20,0)"
    )
    con.execute(
        "INSERT INTO model_prediction(fit_id,predicted_at,n_locked,n_unknown,"
        "p_mid,se,gate_open) VALUES(1,'2026-07-27T00:00:00Z',2,4,0.52,0.7,0)"
    )
    assert con.execute("SELECT COUNT(*) FROM model_prediction").fetchone()[0] == 1
```

If `conftest.py` has no `fresh_db` fixture, add one that runs `migrate.py` against a `tmp_path` database and yields the path.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_schema_v4.py -v`
Expected: FAIL, `PRAGMA user_version` returns 3

- [ ] **Step 3: Write minimal implementation**

Append to `data/solstice_clash/schema.sql`:

```sql
-- A fitted model. One row per fit; the newest row for a theme is the live one.
-- `passes_validation` is the section-8 refit verdict, NOT a live measurement.
CREATE TABLE IF NOT EXISTS model_fit(
  id                INTEGER PRIMARY KEY,
  fitted_at         TEXT NOT NULL,
  theme             TEXT,
  n_matches         INTEGER NOT NULL,
  passes_validation INTEGER NOT NULL CHECK(passes_validation IN (0,1)),
  model_logloss     REAL,
  baseline_a_logloss REAL,
  baseline_b_logloss REAL,
  model_brier       REAL,
  split_wins        INTEGER,
  n_splits          INTEGER,
  params_json       TEXT
);

-- Every prediction we computed, logged whether or not it was shown. This is the
-- monitoring surface: it never decides the gate, it only lets us check afterwards
-- that live behaviour matched the refit estimate.
CREATE TABLE IF NOT EXISTS model_prediction(
  id            INTEGER PRIMARY KEY,
  fit_id        INTEGER NOT NULL REFERENCES model_fit(id) ON DELETE CASCADE,
  match_id      INTEGER REFERENCES match(id) ON DELETE SET NULL,
  predicted_at  TEXT NOT NULL,
  n_locked      INTEGER NOT NULL,
  n_unknown     INTEGER NOT NULL,
  p_mid         REAL NOT NULL CHECK(p_mid >= 0.0 AND p_mid <= 1.0),
  p_low         REAL CHECK(p_low IS NULL OR (p_low >= 0.0 AND p_low <= 1.0)),
  p_high        REAL CHECK(p_high IS NULL OR (p_high >= 0.0 AND p_high <= 1.0)),
  eta           REAL,
  se            REAL NOT NULL CHECK(se >= 0.0),
  trust         TEXT,
  gate_open     INTEGER NOT NULL CHECK(gate_open IN (0,1)),
  draft_json    TEXT,
  actual_outcome TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_prediction_fit ON model_prediction(fit_id);
CREATE INDEX IF NOT EXISTS idx_model_prediction_match ON model_prediction(match_id);
```

In `migrate.py`, set `SCHEMA_VERSION = 4`. The existing `CREATE TABLE IF NOT EXISTS` application path plus the version bump is sufficient - no `ADD_COLUMNS` entries are needed, since both tables are new.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/ -v`
Expected: PASS, including the pre-existing v3 tests

- [ ] **Step 5: Verify the shipped database migrates without loss**

```bash
cd /mnt/docs/adbautoplayer
cp data/solstice_clash/heroes.sqlite /mnt/vault/solstice/heroes-pre-v4.sqlite
sqlite3 data/solstice_clash/heroes.sqlite "SELECT COUNT(*) FROM match;"   # note it
python3 data/solstice_clash/migrate.py
sqlite3 data/solstice_clash/heroes.sqlite "PRAGMA user_version; SELECT COUNT(*) FROM match;"
```
Expected: version 4, match count UNCHANGED. If the count moved, stop and restore the backup.

- [ ] **Step 6: Commit**

```bash
git add data/solstice_clash/schema.sql data/solstice_clash/migrate.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_schema_v4.py
git commit -m "feat(solstice): schema v4 - model fits and logged predictions"
```

---

### Task 6: Store methods for fits and predictions

**Files:**
- Modify: `.../services/solstice/store.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_store_model.py`

**Interfaces:**
- Consumes: schema v4 (Task 5), `ValidationResult` (Task 4), `Prediction` (Task 3).
- Produces on `MatchStore`:
  - `training_matches(theme: str | None = None) -> list[TrainingMatch]`
  - `record_fit(fitted_at, theme, n_matches, result, params_json) -> int`
  - `record_prediction(fit_id, predicted_at, draft, prediction, gate_open, match_id=None) -> int`
  - `latest_fit(theme: str | None = None) -> int | None`

- [ ] **Step 1: Write the failing test**

```python
"""Reading training data and persisting fits and predictions."""

from adb_auto_player.games.afk_journey.services.solstice.odds import DraftState
from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
from adb_auto_player.games.afk_journey.services.solstice.validate import ValidationResult

from tests.games.afk_journey.services.solstice.conftest import fresh_db  # noqa: F401


def _result(passes=True):
    return ValidationResult(0.60, 0.6931, 0.68, 0.21, 18, 20, passes)


def test_training_matches_excludes_draws_and_unfinished(fresh_db):
    """Only decisive matches train the model - draws carry no comps at all."""
    store = MatchStore(fresh_db)
    # Seed via the existing record_match/record_heroes API, one 'left', one 'draw',
    # one with outcome NULL.
    ...
    matches = store.training_matches()
    assert all(m.left_won in (True, False) for m in matches)
    assert len(matches) == 1


def test_training_matches_skips_incomplete_comps(fresh_db):
    """A match with an unidentified slot is a 2v3 and must not train the model."""
    ...


def test_record_and_read_back_a_fit(fresh_db):
    store = MatchStore(fresh_db)
    fit_id = store.record_fit("2026-07-27T00:00:00Z", "converging-paths", 200, _result(), "{}")
    assert store.latest_fit("converging-paths") == fit_id


def test_latest_fit_is_none_before_any_fit(fresh_db):
    assert MatchStore(fresh_db).latest_fit("converging-paths") is None


def test_prediction_is_recorded_with_the_gate_state(fresh_db):
    store = MatchStore(fresh_db)
    fit_id = store.record_fit("2026-07-27T00:00:00Z", "converging-paths", 20, _result(False), "{}")
    draft = DraftState(("a", "b"), ("c",), "Alice", "Bob", 3)
    ...
```

Fill the `...` blocks with the real seeding calls when writing the task - use `MatchRecord` and `HeroSlot` from `store.py` exactly as `test_store.py` already does.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/test_store_model.py -v`
Expected: FAIL, `AttributeError: 'MatchStore' object has no attribute 'training_matches'`

- [ ] **Step 3: Write minimal implementation**

Add to `MatchStore`. `training_matches` must reject anything that would corrupt the fit:

```python
    def training_matches(self, theme: str | None = None) -> list[TrainingMatch]:
        """Decisive, fully-identified matches only.

        Three exclusions, each of which would otherwise corrupt the fit:
        - outcome NULL or 'draw' - no label, and a draw carries no comps at all;
        - any slot not 'identified' - a missing hero makes a 3v3 look like a 2v3;
        - not exactly three heroes a side.
        """
        sql = (
            "SELECT id,left_player,right_player,outcome FROM match "
            "WHERE outcome IN ('left','right')"
        )
        args: tuple = ()
        if theme is not None:
            sql += " AND theme=?"
            args = (theme,)
        out: list[TrainingMatch] = []
        with self._connect() as con:
            for match_id, left_player, right_player, outcome in con.execute(sql, args):
                rows = con.execute(
                    "SELECT side,hero_slug,status FROM match_hero WHERE match_id=?",
                    (match_id,),
                ).fetchall()
                if any(status != "identified" or slug is None for _, slug, status in rows):
                    continue
                left = tuple(slug for side, slug, _ in rows if side == "left")
                right = tuple(slug for side, slug, _ in rows if side == "right")
                if len(left) != 3 or len(right) != 3:
                    continue
                out.append(
                    TrainingMatch(left, right, left_player, right_player, outcome == "left")
                )
        return out
```

`record_fit`, `record_prediction`, and `latest_fit` are straightforward inserts and a `SELECT id ... ORDER BY id DESC LIMIT 1`, following the existing method style in this file.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/services/solstice/ -v`
Expected: PASS, whole solstice suite

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/store.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_store_model.py
git commit -m "feat(solstice): store methods for training data, fits and predictions"
```

---

### Task 7: PREREQUISITE P1 - prove the spectate draft screen is readable

Implements spec section 0, P1. **This task gates Task 8.** If it fails, stop and report; do not wire the model to an unvalidated input.

**Files:**
- Create: `src-tauri/src-python/scripts/check_draft_screen.py`
- Create: fixtures under `/mnt/vault/solstice/draft-fixtures/` (large; never `/tmp`)

- [ ] **Step 1: Capture draft-screen frames**

With a spectate draft live on device, capture at least 8 frames across different pick counts:

```bash
mkdir -p /mnt/vault/solstice/draft-fixtures
adb exec-out screencap -p > /mnt/vault/solstice/draft-fixtures/draft-$(date +%H%M%S).png
```

- [ ] **Step 2: Measure identification accuracy against the Mode A accept rule**

`check_draft_screen.py` loads each frame, crops the `spectate_draft_picks` cells from
`SolsticeConfig`, runs the same image match Mode A uses, and prints per-cell
`score`, `margin`, and whether it passes `score >= 0.70 and margin >= 0.10`.

Run: `cd src-tauri/src-python && uv run python scripts/check_draft_screen.py /mnt/vault/solstice/draft-fixtures`

- [ ] **Step 3: Judge the result and record it**

Write the measured accuracy into `docs/solstice-clash/match-data/converging-paths/matches.md`.

- **If every locked cell passes:** proceed to Task 8.
- **If cells fail:** the geometry is wrong. Retune `spectate_draft_picks` cell bounds using the same method that fixed the prematch cells (which went from 3/6 at 0.53-0.78 to 6/6 at 0.94-0.99 after a 40px correction), re-measure, and only then proceed. **Do not proceed on a partial pass** - a wrong hero read produces a confident wrong number, the exact failure this design exists to prevent.

- [ ] **Step 4: Commit the script and the finding**

```bash
git add src-tauri/src-python/scripts/check_draft_screen.py \
        docs/solstice-clash/match-data/converging-paths/matches.md
git commit -m "test(solstice): measure draft-screen hero identification accuracy"
```

---

### Task 8: Wire the model into the spectate mode

**Files:**
- Modify: `.../mixins/solstice_clash.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/mixins/test_solstice_odds.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6, and Task 7's verdict.
- Produces: `_predict_from_draft(self, frame) -> Prediction | None` and a log line per recomputation.

**Blocked by Task 7.** Do not start until draft-screen reading is proven.

- [ ] **Step 1: Write the failing test**

Test the pure decision logic with a fake store and a fake frame reader - no device:

```python
def test_gate_shut_logs_not_enough_data_and_no_number(caplog):
    ...
    assert "not enough data" in caplog.text
    assert "%" not in caplog.text


def test_gate_open_logs_probability_interval_and_evidence(caplog):
    ...
    assert "80% interval" in caplog.text
    assert "Evidence" in caplog.text


def test_prediction_is_logged_to_the_database_even_when_the_gate_is_shut():
    ...


def test_the_mode_never_taps_a_bet_button():
    """Hard constraint: no code path commits a wager."""
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/mixins/test_solstice_odds.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

On each draft poll: read locked picks, build `DraftState` with `n_unknown = 6 - n_locked`,
call `predict`, call `gate_open`, `record_prediction` unconditionally, and log either the
full line or `not enough data`. Never emit a bare percentage.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && uv run pytest tests/games/afk_journey/ -v`

- [ ] **Step 5: Commit**

```bash
git add src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/solstice_clash.py \
        src-tauri/src-python/tests/games/afk_journey/mixins/test_solstice_odds.py
git commit -m "feat(solstice): live odds during the spectate draft"
```

---

### Task 9: Full-suite verification

- [ ] **Step 1: Run the whole Python suite**

```bash
cd src-tauri/src-python && uv run pytest tests/ -q 2>&1 | tee /tmp/pytest-odds-$(date +%H%M%S).log
```
Expected: no failures. Fix any breakage immediately rather than noting it.

- [ ] **Step 2: Lint**

```bash
cd /mnt/docs/adbautoplayer && uv run ruff check src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/
```

- [ ] **Step 3: Confirm scipy was never introduced**

```bash
cd /mnt/docs/adbautoplayer && ! grep -rn "import scipy\|from scipy" src-tauri/src-python/ && echo "clean"
```
Expected: `clean`

- [ ] **Step 4: Fit against the real collected data and report**

```bash
cd src-tauri/src-python && uv run python -c "
from pathlib import Path
from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
from adb_auto_player.games.afk_journey.services.solstice.odds import fit
from adb_auto_player.games.afk_journey.services.solstice.validate import cross_validate
m = MatchStore(Path('../../data/solstice_clash/heroes.sqlite')).training_matches()
print('training matches:', len(m))
r = cross_validate(m, n_splits=20, seed=0)
print(r)
"
```

Report the numbers as they come out. **A `passes=False` here is the expected and correct result at current sample size** - the gate is doing its job, not failing. Do not tune thresholds to force it open.

- [ ] **Step 5: Update the changelog and commit**

```bash
git add CHANGELOG.md && git commit -m "docs: changelog for Solstice Clash live odds"
```
