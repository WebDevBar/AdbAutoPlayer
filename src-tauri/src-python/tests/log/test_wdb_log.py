"""What reaches the live view, and what only reaches the file."""

import logging

from adb_auto_player.log.wdb_log import (
    WdbSessionFileHandler,
    attach_wdb_session_log,
    is_live_worthy,
    live_message,
)

# Real lines, copied from a run. The operator read these on screen and called them noise;
# each is either already visible in the game or a measurement that matters only on the
# way back through a log.
HIDDEN = [
    "[SC-53] draft over: 5/6 picks read, slowest read 5138ms against a 400ms interval",
    "[SC-61] device stream up - reads should be ~2s faster",
    "[SC-69] locked read: l1:aurora(0.992) l2:granny_dahnie(0.972) l3:evie(0.986)",
    "[SC-71] read order was r3 r2 r1 l3 l2 l1 - the missing side's last slot first",
    "[SC-72] odds model: fitted on 21 matches from this theme",
    "[SC-74] ratings 4066 vs 4055 (gap +11)",
    # NOTE: "[SC-75] recorded prediction ..." used to live here. That line no longer
    # exists - SC-75 now carries the WINNER and the HIT/MISS result, which are shown.
    "[SC-77] crowd 71% left (pools 414179/166578)",
    "[SC-78] market recorded: 22% left from 835702 staked",
    "[SC-80] 206 spectators",
    "ODDS from rating + heroes (UNPROVEN - not yet checked against results)",
    "80% interval 30-69%   trust: low",
    "==============================================",
    "spectate_draft_picks: recorded 6 rows, 0 deduced by elimination, 0 set-consistent",
    "spectate_prematch: recorded 6 rows, 0 deduced by elimination, 0 set-consistent",
    # Screen transitions. Visible on the device before they are logged, and in a narrow
    # panel they push the picks and the call off the top.
    "[SC-50] Solstice Clash: spectating, logging picks, recording",
    "[SC-54] draft screen",
    "[SC-55] draft over - locked screen is up",
    "[SC-58] locked picks screen",
    "[SC-76] FINAL - all picks locked",
    "[SC-82] odds overlay ready",
    # Every hero in this was already announced one line at a time, in draft order.
    "locked - Blue: Bryon, Kruger, Lenya | Red: Ulmus, Cyran, Ludovic",
    # Bookkeeping under the call, and derivable from the pick lines anyway.
    "  4/6 heroes identified",
    "  6/6 heroes identified",
    # A one-off banner. The push/pull summary shares its code and IS shown, which is why
    # this one has to be matched on the message.
    "[SC-35] sync enabled",
    "[SC-35] sync disabled",
]

SHOWN = [
    # The picks, one line each, in draft order.
    '<span class="sc-blue">BLUE 1: Lenya</span>',
    '<span class="sc-red">RED 6: Zandrok</span>',
    # The call, and how it turned out. SC-75 used to be hidden under a comment about
    # "the prediction, which the bubble is already showing" - written before it also
    # carried the RESULT. By the time a result is logged the game has moved on, so it
    # is on no screen at all.
    '[SC-75] <span class="sc-red">RED WINS</span>',
    '[SC-75] called red 56% (r+h) - red won - <span class="sc-hit">HIT</span>',
    "[SC-40] <span class=\"sc-blue\">BLUE WINS</span>",
    "[SC-35] sync: pushed 1, duplicate 0, rejected 0",
    "[SC-03] match did not end in time",
]


def test_the_noise_stays_out_of_the_live_view():
    for line in HIDDEN:
        assert not is_live_worthy(line), line


def test_the_events_worth_watching_get_through():
    for line in SHOWN:
        assert is_live_worthy(line), line


def test_the_live_view_drops_the_sc_code_but_the_file_keeps_it():
    """Codes are for grepping a file and talking about a line precisely.

    Six characters of prefix on every line is six characters of hero name pushed off a
    narrow panel, and nobody watching a draft needs the number.
    """
    assert live_message("[SC-35] sync: pushed 1") == "sync: pushed 1"
    assert live_message('[SC-75] <span class="sc-red">RED WINS</span>') == (
        '<span class="sc-red">RED WINS</span>'
    )
    # Only a code at the START is a prefix. One quoted mid-sentence is content.
    assert live_message("see [SC-41] for why") == "see [SC-41] for why"
    assert live_message("no code here") == "no code here"


def test_a_row_count_that_is_NOT_all_clear_still_shows():
    """The all-zeroes case is the normal one and says nothing; a non-zero one is the
    whole reason the line exists."""
    assert not is_live_worthy(
        "spectate_prematch: recorded 6 rows, 0 deduced by elimination, 0 set-consistent"
    )
    assert is_live_worthy(
        "spectate_prematch: recorded 6 rows, 2 deduced by elimination, 1 set-consistent"
    )


def test_the_session_file_is_truncated_not_appended(tmp_path):
    """An append-only log grows without bound overnight and then has to be searched for
    'which session was that'. Truncating makes it always exactly the run in question."""
    path = tmp_path / "wdb.log"
    path.write_text("stale content from a previous run\n")

    handler = WdbSessionFileHandler(path)
    handler.emit(
        logging.LogRecord("x", logging.INFO, __file__, 1, "fresh line", None, None)
    )
    text = path.read_text()
    assert "stale content" not in text
    assert "fresh line" in text


def test_everything_reaches_the_file_including_what_the_live_view_hides(tmp_path):
    path = tmp_path / "wdb.log"
    handler = WdbSessionFileHandler(path)
    for line in HIDDEN:
        handler.emit(
            logging.LogRecord("x", logging.INFO, __file__, 1, line, None, None)
        )
    text = path.read_text()
    for line in HIDDEN:
        assert line in text


def test_a_failed_write_never_reaches_the_caller(tmp_path):
    """A log is not worth a run."""
    handler = WdbSessionFileHandler(tmp_path / "no-such-dir" / "x" / "wdb.log")
    handler.log_path = tmp_path / "\0invalid"
    handler.emit(
        logging.LogRecord("x", logging.INFO, __file__, 1, "anything", None, None)
    )


def test_attaching_twice_adds_one_handler(tmp_path):
    logger = logging.getLogger("wdb-test-attach")
    logger.handlers.clear()
    first = attach_wdb_session_log(logger, tmp_path / "a.log")
    second = attach_wdb_session_log(logger, tmp_path / "b.log")
    assert first == second
    assert sum(isinstance(h, WdbSessionFileHandler) for h in logger.handlers) == 1
