"""One missed pass of the events list must not end the run.

The card being absent is also what a popup, a half-settled scroll or a slow load looks
like, and the events list is reached seconds after a match ends - so the false positive
lands exactly where the real signal does. It cost a live run on 2026-09-10 after 473
matches and another on 2026-09-12 after 2, both while the event was plainly still live.
"""
# ruff: noqa: E402
import sys
from unittest.mock import MagicMock

mock_pytauri = MagicMock()
mock_pytauri.Commands = MagicMock
mock_pytauri.AppHandle = MagicMock
mock_pytauri.Event = MagicMock
mock_pytauri.Emitter = MagicMock
sys.modules["pytauri"] = mock_pytauri
sys.modules["adb_auto_player.ext_mod"] = MagicMock()

import pytest

from adb_auto_player.games.afk_journey.mixins import solstice_clash as sc


class _Loop:
    """The loop under test, with everything it touches around it stubbed out."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.attempts = 0
        self.recorded_matches = 0
        self.stopped = False
        self._match_recorded_this_cycle = False
        self._draw_this_cycle = False
        self._store = MagicMock()

    _collect_forever = sc.SolsticeClashMixin._collect_forever

    def _run_one_match(self):
        self.attempts += 1
        # Default "ok" so a test ends via max_matches, not by exhausting the budget.
        outcome = self._outcomes.pop(0) if self._outcomes else "ok"
        if outcome == "missing":
            raise sc._EventNotRunningError("[SC-27] not in the events list")
        self._match_recorded_this_cycle = True
        self.recorded_matches += 1
        return True

    def _overlay_stop(self):
        # Called on BOTH exit paths, so it cannot tell "event over" from "done". The
        # attempt count is the discriminator; this only proves the overlay is released.
        self.stopped = True

    def _is_in_overview(self):
        return True

    def navigate_to_world(self):  # pragma: no cover - never reached, overview is True
        raise AssertionError("recovery should be a no-op when already on the overworld")


@pytest.fixture(autouse=True)
def _no_sync(monkeypatch):
    monkeypatch.setattr(sc, "SyncClient", lambda store: MagicMock(enabled=False))


def test_a_single_miss_does_not_end_the_run():
    # Miss, then a real match. The old code returned on the first miss.
    loop = _Loop(["missing", "ok"])
    loop._collect_forever(max_restarts=3, max_matches=1)
    # Two attempts means it retried past the miss and then recorded. The old code
    # returned after attempt 1 without ever recording.
    assert loop.attempts == 2, "did not retry after the miss"
    assert loop.recorded_matches == 1


def test_the_counter_resets_on_a_recorded_match():
    # Two misses, a match, two more misses, a match. Never three in a row, so the run
    # must survive six attempts that include four total misses.
    loop = _Loop(["missing", "missing", "ok", "missing", "missing", "ok"])
    loop._collect_forever(max_restarts=3, max_matches=2)
    assert loop.attempts == 6, "stopped early - misses were treated as consecutive"
    assert loop.recorded_matches == 2


def test_three_consecutive_misses_stop_cleanly():
    loop = _Loop(["missing", "missing", "missing"])
    loop._collect_forever(max_restarts=3, max_matches=None)
    assert loop.attempts == sc.MAX_EVENT_MISSING
    assert loop.recorded_matches == 0
    assert loop.stopped, "a genuinely ended event must still stop the run"
