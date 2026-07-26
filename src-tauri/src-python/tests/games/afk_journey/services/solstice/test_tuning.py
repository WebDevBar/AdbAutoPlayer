"""Crop/scale tuning against a CONFIRMED identity.

Measured headroom on the three weakest summary cards, tuning crop alone:
  solise  0.781 -> 0.866 (margin 0.244) at hw=22 top=18 bot=26
  baelran 0.798 -> 0.844 (margin 0.323) at hw=24 top=14 bot=26
  indris  0.876 -> 0.905 (margin 0.189) at hw=22 top=14 bot=32
"""

import cv2

from adb_auto_player.games.afk_journey.services.solstice.tuning import tune_cell

SOLISE_CENTRE = (90, 1307)


def test_tuning_improves_the_weakest_card(cfg, library, frames):
    frame = cv2.imread(str(frames["summary_01"]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    result = tune_cell(gray, SOLISE_CENTRE, "solise", library, cfg)

    assert result is not None
    assert result.score >= 0.80, f"expected >=0.80, got {result.score}"
    assert result.margin >= 0.10


def test_tuning_returns_none_when_the_truth_never_wins(cfg, library, frames):
    """If the named hero is not what is on screen, tuning must refuse rather than
    force the wrong answer to score better."""
    frame = cv2.imread(str(frames["summary_01"]))
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    assert tune_cell(gray, SOLISE_CENTRE, "thoran", library, cfg) is None
