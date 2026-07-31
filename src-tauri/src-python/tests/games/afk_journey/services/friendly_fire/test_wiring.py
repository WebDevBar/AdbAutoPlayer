"""Mixin wiring, with stubs standing in for the device."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2

sys.modules.setdefault("pytauri", MagicMock())
sys.modules.setdefault("adb_auto_player.ext_mod", MagicMock())

from adb_auto_player.games.afk_journey.mixins.arena import ArenaMixin  # noqa: E402
from adb_auto_player.games.afk_journey.mixins.supreme_arena import (  # noqa: E402
    SupremeArenaMixin,
)
from adb_auto_player.games.afk_journey.services.friendly_fire.geometry import (  # noqa: E402
    CARD_X_RANGES,
    SA_TAP_POINTS,
    Mode,
)
from adb_auto_player.games.afk_journey.settings import OpponentPosition  # noqa: E402

DATA = Path(__file__).parent / "data"
TEMPLATES = Path(__file__).parents[5] / "adb_auto_player/games/afk_journey/templates"


class _ArenaStub(ArenaMixin):
    def __init__(self, on):
        self._s = SimpleNamespace(arena=SimpleNamespace(prevent_friendly_fire=on))
        self._ff_stop_run = False

    @property
    def settings(self):
        return self._s


class _SAStub(SupremeArenaMixin):
    def __init__(self, on, position=OpponentPosition.Left):
        self._s = SimpleNamespace(
            supreme_arena=SimpleNamespace(
                prevent_friendly_fire=on, opponent_position=position, attempts=5
            )
        )
        self._ff_stop_run = False
        self.taps = []

    @property
    def settings(self):
        return self._s

    def tap(self, coordinates, **kwargs):
        self.taps.append(coordinates)


def test_arena_guard_is_off_by_default():
    assert _ArenaStub(False)._friendly_fire_enabled() is False


def test_arena_guard_reports_on_when_enabled():
    assert _ArenaStub(True)._friendly_fire_enabled() is True


def test_supreme_arena_guard_is_off_by_default():
    assert _SAStub(False)._sa_friendly_fire_enabled() is False


def test_supreme_arena_guard_reports_on_when_enabled():
    assert _SAStub(True)._sa_friendly_fire_enabled() is True


def test_a_non_boolean_setting_never_switches_the_guard_on():
    """Truthiness is not enough. A MagicMock, a stub, or a half-built settings
    object must not silently enable a guard that changes which opponent is
    attacked - which is exactly what an existing coverage test exposed."""
    from unittest.mock import MagicMock as _MM

    arena = _ArenaStub(False)
    arena._s = SimpleNamespace(arena=_MM())
    assert arena._friendly_fire_enabled() is False

    supreme = _SAStub(False)
    supreme._s = SimpleNamespace(supreme_arena=_MM())
    assert supreme._sa_friendly_fire_enabled() is False


def test_the_configured_position_is_read_not_ignored():
    """The toggle must never silently override a setting the user chose."""
    assert _SAStub(True, OpponentPosition.Right)._sa_position() is OpponentPosition.Right


def test_tapping_a_supreme_arena_card_uses_the_existing_fixed_points():
    bot = _SAStub(True)
    bot._tap_sa_card(1)
    assert bot.taps == [SA_TAP_POINTS[1]]


def test_halting_sets_the_run_level_flag():
    """run_arena has two loops; a bare False only breaks the first, so the second
    would claim a free attempt and fight on after an unsafe give-up."""
    arena = _ArenaStub(True)
    assert arena._ff_halt() is False
    assert arena._ff_stop_run is True

    supreme = _SAStub(True)
    assert supreme._sa_halt() is False
    assert supreme._ff_stop_run is True


def test_every_arena_card_is_locatable_by_the_existing_template():
    """Arena's unguarded code searches only the left 40%, which is exactly why it
    cannot reach card 2. Per card x-range, all three match. Measured: 0.99927,
    0.99416, 0.98908 - the floor is 0.98 because card 3 sits at 0.98908."""
    frame = cv2.imread(str(next(DATA.glob("01*.png"))), cv2.IMREAD_COLOR)
    template = cv2.imread(str(TEMPLATES / "arena/opponent.png"), cv2.IMREAD_COLOR)
    assert template is not None
    for index, (x0, x1) in enumerate(CARD_X_RANGES[Mode.ARENA]):
        result = cv2.matchTemplate(frame[:, x0:x1], template, cv2.TM_CCOEFF_NORMED)
        assert result.max() >= 0.98, f"card {index + 1} not located"
