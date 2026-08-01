"""Mode B - passive collection while the user plays.

Every test here is device-free: FakeCompeteMode serves queued PNGs and records
any device call, so "never touches the device" is a checkable assertion rather
than a claim.
"""

import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from adb_auto_player.exceptions import GameActionFailedError, GameTimeoutError
from adb_auto_player.games.afk_journey.mixins.solstice_clash import SolsticeClashMixin
from adb_auto_player.games.afk_journey.services.solstice.details_screen import TAB_STRIP
from adb_auto_player.games.afk_journey.services.solstice.store import MatchStore
from adb_auto_player.games.afk_journey.services.solstice.summary import SummaryRead

FRAMES = Path(__file__).parents[1] / "services" / "solstice" / "data"
REPO = Path(__file__).resolve().parents[6]  # parents[5] is src-tauri
SEED_DB = REPO / "data" / "solstice_clash" / "heroes.sqlite"
TEMPLATES = (
    Path(__file__).resolve().parents[4]
    / "adb_auto_player"
    / "games"
    / "afk_journey"
    / "templates"
)
ICON_DIR = Path("/mnt/vault/solstice/gamefiles/ui/icon")


class FakeCompeteMode(SolsticeClashMixin):
    """Device-free. Every method that could reach ADB either serves a queued
    frame or appends to `device_actions`."""

    def __init__(self, db_path: Path, cfg, library, ocr):
        self._frames: list[np.ndarray] = []
        self.device_actions: list[str] = []
        self.start_up_calls = 0
        self._settings = MagicMock()
        self._db_path = db_path
        # ALL FOUR caches must be primed. These are read-only @property with no
        # setter, so priming the *_cache attribute is the only seam - assigning
        # self._store raises AttributeError.
        #
        # _cfg_cache is not an optimisation, it is containment: the real
        # _solstice_cfg calls SolsticeConfig.load(solstice_db_path()), and
        # solstice_db_path() CREATES the user's live application database by
        # copying and scrubbing the seed if it is absent. Leaving it unprimed
        # means the suite reaches outside tmp_path into ~/.local/share whatever
        # db_path says.
        self._store_cache = MatchStore(db_path)
        self._cfg_cache = cfg
        self._lib_cache = library
        self._ocr_cache = ocr
        self._frame_size = (1920, 1080)  # numpy order: rows, cols

    # -- harness API --------------------------------------------------------
    def feed(self, *frames: np.ndarray) -> None:
        self._frames.extend(frames)

    def set_frame_size(self, width: int, height: int) -> None:
        self._frame_size = (height, width)

    # -- device surface, all stubbed ---------------------------------------
    def get_screenshot(self) -> np.ndarray:
        frame = self._frames.pop(0) if self._frames else _blank()
        h, w = self._frame_size
        return frame if frame.shape[:2] == (h, w) else cv2.resize(frame, (w, h))

    def start_up(self, *a, **k) -> None:
        self.start_up_calls += 1

    def tap(self, *a, **k) -> None:
        self.device_actions.append("tap")

    def swipe(self, *a, **k) -> None:
        self.device_actions.append("swipe")

    def hold(self, *a, **k) -> None:
        self.device_actions.append("hold")

    def press_back(self, *a, **k) -> None:
        self.device_actions.append("press_back")

    @property
    def template_dir(self) -> Path:
        return TEMPLATES


def _frame(name: str) -> np.ndarray:
    return cv2.imread(str(FRAMES / f"{name}.png"))


def _blank() -> np.ndarray:
    return np.zeros((1920, 1080, 3), dtype=np.uint8)


def details_frame(match: int = 1, heroes: int = 6) -> np.ndarray:
    """`match` picks between two committed captures of DIFFERENT matches, so
    "reopened the same one" and "played a new one" are separable. `heroes != 6`
    returns the partial capture where two cells did not render."""
    if heroes != 6:
        return _frame("summary_partial")
    return _frame("summary_01" if match == 1 else "summary_02")


def not_details_frame() -> np.ndarray:
    """Any confirmed non-details screen, to make the mode disarm.

    Mode B never identifies the overworld - it only asks "is this the details
    screen?" and everything else disarms - so this does not need to BE the
    overworld. Mode A is the mode that needs a real overworld check and it
    already has the framework's _is_in_overview().
    """
    return _frame("spectate")


# There is deliberately no "broken frame" or "no winner" fixture. Both would be
# blank or junk images, and is_details_screen rejects those BEFORE read_summary
# is ever called - so such a test would pass while exercising nothing. Those
# cases patch read_summary instead.


@pytest.fixture(autouse=True)
def _no_poll_sleep(monkeypatch):
    """POLL_SECONDS is 2.0 in production. Left alone these tests would queue
    dozens of polls and spend a minute purely sleeping."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    monkeypatch.setattr(mod, "POLL_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def sync(monkeypatch):
    """Counts pushes without a network.

    **autouse is a data-integrity requirement, not a convenience.** Every test
    here records matches built from fixture images. Without this patch each one
    constructs a real SyncClient and pushes those rows to the shared pool at
    gameretro.net - the fork key is baked into the build, so credentials are
    present and the push would succeed. Synthetic matches would then be
    permanently in every contributor's training data.

    The seam is the CLASS, not an instance attribute: the mixin has no `_sync`,
    it constructs a local SyncClient(self._store) per run.
    """
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    counter = MagicMock()
    counter.push_calls = 0
    counter.pull_calls = 0

    class FakeSyncClient:
        enabled = True

        def __init__(self, store):
            self.store = store

        def push(self):
            counter.push_calls += 1

        def pull(self):
            counter.pull_calls += 1

    monkeypatch.setattr(mod, "SyncClient", FakeSyncClient)
    return counter


@pytest.fixture(scope="session")
def shared_cfg():
    from adb_auto_player.games.afk_journey.services.solstice.config import (
        SolsticeConfig,
    )

    return SolsticeConfig.load(SEED_DB)


@pytest.fixture(scope="session")
def shared_library(shared_cfg):
    from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary

    # Same skip the solstice conftest's `library` fixture uses: the icon set is
    # extracted game data and is not in the repo.
    if not ICON_DIR.is_dir():
        pytest.skip(f"icon library not available at {ICON_DIR}")
    return IconLibrary.build(shared_cfg, ICON_DIR)


@pytest.fixture(scope="session")
def shared_ocr():
    from adb_auto_player.ocr import RapidOCRBackend

    return RapidOCRBackend()


@pytest.fixture
def mode(tmp_path, shared_cfg, shared_library, shared_ocr):
    db = tmp_path / "heroes.sqlite"
    shutil.copy(SEED_DB, db)  # a COPY: the tests write, and the seed is committed
    return FakeCompeteMode(db, shared_cfg, shared_library, shared_ocr)


@pytest.fixture
def db(mode) -> Path:
    """The database file, for asserting in SQL.

    MatchStore has no match_count/newest_match/audit_count. Asserting in SQL is
    also the stronger test: it reads the table the feature must actually write,
    not a helper that could be wrong in the same direction as the code.
    """
    return mode._db_path


def count(db: Path, sql: str, *args) -> int:
    with sqlite3.connect(db) as con:
        return con.execute(sql, args).fetchone()[0]


def matches(db: Path) -> int:
    return count(db, "SELECT COUNT(*) FROM match WHERE source='compete_summary'")


def audits(db: Path) -> int:
    return count(db, "SELECT COUNT(*) FROM identification_audit")


# --- safety ----------------------------------------------------------------


def test_it_refuses_a_wrong_resolution(mode):
    """Every coordinate was measured on 1080x1920, and the mode may not resize
    the display because the user may be mid-match."""
    mode.set_frame_size(720, 1280)
    with pytest.raises(GameActionFailedError, match=r"\[SC-42\]"):
        mode.collect_while_playing(max_polls=1)


def test_it_never_calls_start_up(mode):
    """start_up() resizes the display and can launch the game - under a live
    ranked match."""
    mode.collect_while_playing(max_polls=1)
    assert mode.start_up_calls == 0


def test_it_never_touches_the_device(mode):
    mode.feed(details_frame(), details_frame(), not_details_frame())
    mode.collect_while_playing(max_polls=3)
    assert mode.device_actions == []


# --- dedupe ----------------------------------------------------------------


def test_one_details_screen_records_once_across_many_polls(mode, db):
    """The screen stays up for tens of seconds; recording it twenty times would
    corrupt the model far more effectively than missing it."""
    mode.feed(*[details_frame()] * 10)
    before = matches(db)
    mode.collect_while_playing(max_polls=10)
    assert matches(db) == before + 1


def test_it_re_arms_after_the_screen_disappears(mode, db):
    mode.feed(details_frame(match=1), not_details_frame(), details_frame(match=2))
    before = matches(db)
    mode.collect_while_playing(max_polls=3)
    assert matches(db) == before + 2


def test_reopening_the_same_match_does_not_duplicate(mode, db, caplog):
    """Layer 1 re-arms because the screen disappeared; layer 2 catches it."""
    mode.feed(details_frame(match=1), not_details_frame(), details_frame(match=1))
    before = matches(db)
    with caplog.at_level("DEBUG"):
        mode.collect_while_playing(max_polls=3)
    assert matches(db) == before + 1
    # The backstop firing is [SC-41] and is NOT a failure - distinct from both
    # the parser raising and an incomplete read.
    assert any("[SC-41]" in r.message for r in caplog.records)


# --- what gets recorded ----------------------------------------------------


def test_a_partial_read_is_skipped_and_stays_armed(mode, db, caplog):
    """A frame caught mid-animation must not record, and must not disarm - the
    next poll gets a clean read of the same screen.

    Also pins the CODE: an incomplete read is [SC-48], distinct from a parser
    that raised ([SC-47]) and from a benign dedupe skip ([SC-41]). They were one
    code until an audit caught that a log could not tell them apart.
    """
    mode.feed(details_frame(heroes=4), details_frame())
    before = matches(db)
    with caplog.at_level("DEBUG"):
        mode.collect_while_playing(max_polls=2)
    assert matches(db) == before + 1
    assert any("[SC-48]" in r.message for r in caplog.records)
    assert not any("[SC-47]" in r.message for r in caplog.records)


def test_a_frame_with_no_winner_is_skipped(mode, db, monkeypatch):
    """A real details screen whose banner has not resolved yields winner=None.
    Recording it would enter a match with no outcome, which the odds model counts
    as neither a win nor a loss.

    Patched rather than fed a junk image on purpose: a junk image is rejected by
    is_details_screen and never reaches read_summary.
    """
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    real = mod.read_summary
    calls = {"n": 0}

    def winner_none_once(frame, cfg, library, ocr):
        calls["n"] += 1
        read = real(frame, cfg, library, ocr)
        if calls["n"] == 1:
            return SummaryRead(
                winner=None,
                left_player=read.left_player,
                right_player=read.right_player,
                heroes=read.heroes,
            )
        return read

    monkeypatch.setattr(mod, "read_summary", winner_none_once)
    mode.feed(details_frame(), not_details_frame(), details_frame())
    before = matches(db)
    mode.collect_while_playing(max_polls=3)
    assert matches(db) == before + 1


def test_it_records_source_compete_summary(mode, db):
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert matches(db) == 1


def test_theme_is_resolved_by_date_not_read_from_screen(mode, db):
    """The details screen never shows the theme, so it can never be 'ocr'."""
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    with sqlite3.connect(db) as con:
        got = con.execute(
            "SELECT theme_resolved_by FROM match WHERE source='compete_summary'"
        ).fetchall()
    assert got and all(r[0] in ("window", "default") for r in got)


def test_it_does_not_write_identification_audit_rows(mode, db):
    """Audit rows are confirmation evidence for cell tuning, and this mode
    cannot long-press to confirm anything."""
    before = audits(db)
    mode.feed(details_frame())
    mode.collect_while_playing(max_polls=1)
    assert audits(db) == before


def test_it_logs_sc40_once_per_recorded_match(mode, caplog):
    """Live verification checks these lines by eye, so they have to be emitted - and
    once per MATCH, not once per poll.

    Each recorded match emits TWO [SC-40] lines: the detail line, and the coloured
    winner announcement added alongside it. So four polls covering two distinct
    matches, one of them seen twice, must produce four records - not eight.
    """
    mode.feed(
        details_frame(match=1),
        details_frame(match=1),
        not_details_frame(),
        details_frame(match=2),
    )
    with caplog.at_level("INFO"):
        mode.collect_while_playing(max_polls=4)
    lines = [r.message for r in caplog.records if "[SC-40]" in r.message]
    assert len(lines) == 4, lines
    assert sum("recorded match" in line for line in lines) == 2, lines


# --- resilience ------------------------------------------------------------


def test_an_exception_in_one_poll_does_not_stop_the_loop(
    mode, db, monkeypatch, caplog
):
    """One bad read must not end an unattended session."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    real = mod.read_summary
    calls = {"n": 0}

    def raise_once(frame, cfg, library, ocr):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated identification failure")
        return real(frame, cfg, library, ocr)

    monkeypatch.setattr(mod, "read_summary", raise_once)
    mode.feed(details_frame(), not_details_frame(), details_frame())
    with caplog.at_level("DEBUG"):
        mode.collect_while_playing(max_polls=3)
    assert matches(db) >= 1
    # A parser that RAISED is [SC-47], never [SC-48] - one means the code broke,
    # the other means the screen was mid-animation and the next poll is fine.
    assert any("[SC-47]" in r.message for r in caplog.records)
    assert not any("[SC-48]" in r.message for r in caplog.records)


def test_it_stops_when_the_device_connection_dies(mode, monkeypatch):
    """A dead ADB connection must not spin silently for hours while the user
    plays, believing they are collecting."""
    monkeypatch.setattr(mode, "get_screenshot", MagicMock(side_effect=OSError("adb")))
    with pytest.raises(GameActionFailedError, match=r"\[SC-45\]"):
        mode.collect_while_playing(max_polls=100)


def test_a_recovered_screenshot_resets_the_failure_counter(mode, db, monkeypatch):
    """Transient failures must not accumulate across a session into a false
    'the connection is gone'."""
    real = mode.get_screenshot
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] % 3 == 0:
            raise OSError("transient")
        return real()

    monkeypatch.setattr(mode, "get_screenshot", flaky)
    mode.feed(*[details_frame()] * 60)
    mode.collect_while_playing(max_polls=60)  # must not raise
    assert matches(db) >= 1


def test_it_warns_once_when_the_two_signals_disagree(mode, caplog):
    """The whole point of the second signal is noticing a broken first one. A
    details screen with its tab strip blacked out fires the template only."""
    frame = details_frame().copy()
    x0, y0, x1, y1 = TAB_STRIP
    frame[y0:y1, x0:x1] = 0
    mode.feed(*[frame] * 40)
    with caplog.at_level("WARNING"):
        mode.collect_while_playing(max_polls=40)
    assert sum("[SC-46]" in r.message for r in caplog.records) == 1


# --- sync ------------------------------------------------------------------


def test_it_pushes_after_each_recorded_match(mode, sync):
    """NOT on exit: the GUI stop button is SIGTERM and Python does not run
    finally blocks on SIGTERM, so an on-exit push would never fire in the only
    way a user actually stops this mode."""
    mode.feed(details_frame(match=1), not_details_frame(), details_frame(match=2))
    mode.collect_while_playing(max_polls=3)
    assert sync.push_calls == 2


def test_it_does_not_push_when_nothing_was_recorded(mode, sync):
    mode.feed(not_details_frame(), not_details_frame())
    mode.collect_while_playing(max_polls=2)
    assert sync.push_calls == 0


def test_it_emits_a_periodic_heartbeat(mode, caplog, monkeypatch):
    """An on-stop summary would be skipped by SIGTERM exactly like the push. A
    heartbeat also answers "is this still working?" while the user can act."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    monkeypatch.setattr(mod, "HEARTBEAT_POLLS", 5)
    mode.feed(*[not_details_frame()] * 12)
    with caplog.at_level("INFO"):
        mode.collect_while_playing(max_polls=12)
    assert sum("[SC-43]" in r.message for r in caplog.records) == 2


# --- Mode A adopting the predicate (Task 4) --------------------------------


def test_it_returns_the_frame_once_the_details_screen_appears(mode):
    """Replaces sleep-and-hope with a bounded wait. Tested on the extracted
    helper, not _run_one_match, which taps and navigates and cannot be driven by
    a device-free harness."""
    mode.feed(not_details_frame(), not_details_frame(), details_frame())
    frame = mode._wait_for_details_screen()
    assert frame is not None
    assert frame.shape[:2] == (1920, 1080)


def test_it_raises_sc44_if_the_details_screen_never_arrives(mode, monkeypatch):
    """A short timeout: the point is the raise, not waiting 15 real seconds."""
    import adb_auto_player.games.afk_journey.mixins.solstice_clash as mod

    monkeypatch.setattr(mod, "DETAILS_TIMEOUT", 1.0)
    monkeypatch.setattr(mod, "DETAILS_POLL_DELAY", 0.05)
    mode.feed(*[not_details_frame()] * 40)
    with pytest.raises(GameTimeoutError, match=r"\[SC-44\]"):
        mode._wait_for_details_screen()


def test_mode_a_requires_no_header_title(mode):
    """Mode A navigated to the event itself, so it must NOT require the title -
    if it did, header=False would reject every screen."""
    import inspect

    src = inspect.getsource(mode._wait_for_details_screen)
    assert "header_title" not in src
