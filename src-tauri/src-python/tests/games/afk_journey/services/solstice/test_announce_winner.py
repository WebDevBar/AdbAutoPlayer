"""The winner headline must be stated in BLUE/RED, never in panel terms.

A 2026-09-07 run printed "BLUE WINS" directly above "called blue 51% - red won -
MISS" for the same match. The headline was fed `read.winner`, which is which PANEL
won, and `announce_winner` maps left->blue unconditionally. Which panel a side
occupies is not fixed - resolving that is what `_winner_in_blue_terms` exists for.
"""

import types

from adb_auto_player.games.afk_journey.mixins.solstice_clash import SolsticeClashMixin
from adb_auto_player.games.afk_journey.services.solstice.odds import announce_winner

_TRIO_1 = ["brutus", "kafra", "odie"]
_TRIO_2 = ["lucca", "parisa", "silvina"]


def _read(winner: str):
    heroes = [types.SimpleNamespace(side="left", slug=s) for s in _TRIO_1]
    heroes += [types.SimpleNamespace(side="right", slug=s) for s in _TRIO_2]
    return types.SimpleNamespace(winner=winner, heroes=heroes)


def test_the_left_panel_winning_is_not_automatically_blue():
    """The exact shape of the reported bug: left panel won, but blue was trio 1."""
    trios = (sorted(_TRIO_1), sorted(_TRIO_2))
    # The RIGHT panel won, and blue is trio 1 - so red won.
    side = SolsticeClashMixin._winner_in_blue_terms(_read("right"), trios, 1)
    assert side == "right"
    assert "RED WINS" in announce_winner(side)


def test_blue_winning_is_announced_as_blue():
    trios = (sorted(_TRIO_1), sorted(_TRIO_2))
    side = SolsticeClashMixin._winner_in_blue_terms(_read("left"), trios, 1)
    assert side == "left"
    assert "BLUE WINS" in announce_winner(side)


def test_an_unresolved_orientation_claims_no_colour():
    """blue_trio is None in the compete path, which never watches the draft."""
    trios = (sorted(_TRIO_1), sorted(_TRIO_2))
    assert SolsticeClashMixin._winner_in_blue_terms(_read("left"), trios, None) is None
    assert announce_winner(None) == "result unresolved"
