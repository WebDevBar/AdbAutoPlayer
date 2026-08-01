"""The encoding is ANTISYMMETRIC, so orientation is free for hero terms and the
rating gap: flip a row and every term negates while y becomes 1-y, and since
sigma(-x.b) = 1 - sigma(x.b) the likelihood contribution is identical.

The INTERCEPT is the sole exception - its column is 1.0 regardless of orientation,
which is exactly why it can learn the 56.0% first-pick advantage, and exactly why a
pooled row with an arbitrary orientation would corrupt it.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from adb_auto_player.games.afk_journey.services.solstice.odds import (
    Match,
    design,
    fit,
)

_GOLDEN = Path(__file__).parent / "data" / "golden_fit.json"


@pytest.fixture
def _golden():
    return json.loads(_GOLDEN.read_text())


@pytest.fixture
def golden_local_matches(_golden):
    """The LOCAL-ONLY population the baseline was fitted on.

    Rehydrated from the committed file rather than re-read from the database, which
    has since been reshaped - and captured before any of this change landed, because
    afterwards the code that produced it can no longer run.
    """
    return [
        Match(
            left=tuple(m["left"]),
            right=tuple(m["right"]),
            left_won=m["left_won"],
            theme_id=m["theme_id"],
            left_rating=m["left_rating"],
            right_rating=m["right_rating"],
            event_id=m["event_id"],
            # Every row in the baseline is local by construction, so every one of them
            # contributes to the intercept exactly as it did before.
            blue_trio=1,
        )
        for m in _golden["matches"]
    ]


@pytest.fixture
def golden_coefficients(_golden):
    return np.array(_golden["beta"])


def _row(blue_trio, left_won=True):
    return Match(
        left=("a", "b", "c"),
        right=("m", "n", "o"),
        left_won=left_won,
        theme_id=1,
        left_rating=100,
        right_rating=200,
        blue_trio=blue_trio,
    )


def _flipped(m):
    return Match(
        left=m.right,
        right=m.left,
        left_won=not m.left_won,
        theme_id=m.theme_id,
        left_rating=m.right_rating,
        right_rating=m.left_rating,
        blue_trio=m.blue_trio,
    )


def test_a_pooled_row_has_a_zero_intercept():
    x, _y, _w, _h, _p = design([_row(blue_trio=None)], theme_id=1)
    assert x[0][0] == 0.0


def test_a_local_row_has_a_one_intercept():
    x, _y, _w, _h, _p = design([_row(blue_trio=1)], theme_id=1)
    assert x[0][0] == 1.0


def test_hero_terms_are_identical_for_a_pooled_row():
    """Only the intercept differs. Every pooled comp still trains the hero strengths,
    which is the entire point of pooling.
    """
    local = design([_row(blue_trio=1)], theme_id=1)[0][0]
    pooled = design([_row(blue_trio=None)], theme_id=1)[0][0]
    assert list(local[1:]) == list(pooled[1:])


def test_flipping_a_row_leaves_the_likelihood_unchanged():
    """Antisymmetry, measured rather than asserted. Uses a POOLED row so the intercept
    is zero for both and cannot mask the result.
    """
    m = _row(blue_trio=None)
    x1, y1, _w, _h, _p = design([m], theme_id=1)
    x2, y2, _w, _h, _p = design([_flipped(m)], theme_id=1)
    beta = np.arange(1, x1.shape[1] + 1) * 0.01

    def ll(x, y):
        p = 1.0 / (1.0 + np.exp(-x @ beta))
        return y * np.log(p) + (1 - y) * np.log(1 - p)

    assert abs(ll(x1[0], y1[0]) - ll(x2[0], y2[0])) < 1e-12


def test_a_fit_with_no_pooled_rows_is_unchanged(
    golden_local_matches, golden_coefficients
):
    """The hard requirement: this must not move the model for a user with no pool.

    The baseline was captured with the pre-rework code on the pre-rework database.
    If this drifts, the change was not inert for local-only data and the whole
    justification for making it goes with it.
    """
    result = fit(golden_local_matches, theme_id=golden_local_matches[0].theme_id)
    np.testing.assert_allclose(result.beta, golden_coefficients, rtol=1e-8, atol=1e-10)
