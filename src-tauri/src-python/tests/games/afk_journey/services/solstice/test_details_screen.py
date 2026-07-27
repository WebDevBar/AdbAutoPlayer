"""The details-screen predicate.

Three candidates were measured and rejected before settling on these signals;
see the spec. The rejected ones all looked correct on reasoning.
"""

import inspect

import pytest

from adb_auto_player.games.afk_journey.services.solstice.details_screen import (
    SOLSTICE_CLASH_TITLE,
    TAB_LABELS,
    details_signals,
    is_details_screen,
)

DETAILS = ("summary_01", "summary_02")
NOT_DETAILS = (
    "draft_selecting",
    "prematch_locked",
    "spectate",
    "spectate_draft",
    "spectate_prematch",
)


@pytest.mark.parametrize("name", DETAILS)
def test_accepts_every_details_screen(
    name, frames, read_frame, replay_template, ocr_backend
):
    assert (
        is_details_screen(read_frame(frames[name]), replay_template, ocr_backend)
        is True
    )


@pytest.mark.parametrize("name", NOT_DETAILS)
def test_rejects_every_other_screen(
    name, frames, read_frame, replay_template, ocr_backend
):
    assert (
        is_details_screen(read_frame(frames[name]), replay_template, ocr_backend)
        is False
    )


# --- the optional header-title gate ----------------------------------------


@pytest.mark.parametrize("name", DETAILS)
def test_the_header_title_matches_on_a_solstice_details_screen(
    name, frames, read_frame, replay_template, ocr_backend
):
    assert (
        is_details_screen(
            read_frame(frames[name]),
            replay_template,
            ocr_backend,
            header_title=SOLSTICE_CLASH_TITLE,
        )
        is True
    )


@pytest.mark.parametrize("name", DETAILS)
def test_a_wrong_title_rejects_a_real_details_screen(
    name, frames, read_frame, replay_template, ocr_backend
):
    """The whole point: signals 1 and 2 pass on ANY 3v3 post-battle screen, so
    without this a passive collector records another game mode's match as
    Solstice data and pushes it to the shared pool."""
    assert (
        is_details_screen(
            read_frame(frames[name]),
            replay_template,
            ocr_backend,
            header_title="Dream Realm",
        )
        is False
    )


def test_the_title_is_matched_exactly_not_as_a_substring(
    frames, read_frame, replay_template, ocr_backend
):
    """A substring test would let 'Clash' - or any title containing this one -
    stand in for the real thing."""
    frame = read_frame(frames["summary_01"])
    for wrong in ("Clash", "Solstice", "Solstice Clash Finals", "olstice Clas"):
        assert (
            is_details_screen(
                frame, replay_template, ocr_backend, header_title=wrong
            )
            is False
        ), wrong


def test_one_wrong_character_fails(
    frames, read_frame, replay_template, ocr_backend
):
    """The SILVER/SILVEN lesson, applied to the title.

    That bug was a FUZZY similarity score: SILVER matched SILVEN at 0.833 and was
    accepted. Nothing here scores similarity - the comparison is `==` on the
    normalised strings - so a single wrong character can never pass. This test
    exists to keep it that way: if anyone ever swaps in difflib or a threshold,
    it fails immediately.
    """
    frame = read_frame(frames["summary_01"])
    for near_miss in ("Solstice Clasch", "Solstlce Clash", "Solstice Clash "  + "x",
                      "Solstice Clas", "Soltice Clash"):
        assert (
            is_details_screen(
                frame, replay_template, ocr_backend, header_title=near_miss
            )
            is False
        ), near_miss


def test_the_title_match_tolerates_case_and_spacing(
    frames, read_frame, replay_template, ocr_backend
):
    """Exact on CONTENT, not on presentation - OCR spacing is not stable."""
    frame = read_frame(frames["summary_01"])
    for ok in ("solstice clash", "SOLSTICE CLASH", "  Solstice   Clash  "):
        assert (
            is_details_screen(
                frame, replay_template, ocr_backend, header_title=ok
            )
            is True
        ), ok


def test_omitting_the_title_does_not_mean_the_check_failed(
    frames, read_frame, replay_template, ocr_backend
):
    """`header` is None when unchecked, and None must not read as False -
    otherwise Mode A, which passes no title, would reject every screen."""
    signals = details_signals(
        read_frame(frames["summary_01"]), replay_template, ocr_backend
    )
    assert signals.header is None
    assert signals.confirmed is True


# --- signal reporting, which is what makes silent rot detectable -----------


def test_signals_are_reported_individually(
    frames, read_frame, replay_template, ocr_backend
):
    """The two core signals are ANDed, so a game update that breaks either one
    stops collection silently. The redundancy only exists if a caller can see
    them disagree."""
    details = details_signals(
        read_frame(frames["summary_01"]), replay_template, ocr_backend
    )
    assert details.template is True
    assert details.labels is True

    other = details_signals(
        read_frame(frames["spectate"]), replay_template, ocr_backend
    )
    assert other.confirmed is False


def test_a_details_screen_with_no_readable_tabs_reports_the_disagreement(
    frames, read_frame, replay_template, ocr_backend
):
    """Template fires, labels do not - exactly the shape of a broken label read
    on a screen that IS the details screen."""
    frame = read_frame(frames["summary_01"]).copy()
    from adb_auto_player.games.afk_journey.services.solstice.details_screen import (
        TAB_STRIP,
    )

    x0, y0, x1, y1 = TAB_STRIP
    frame[y0:y1, x0:x1] = 0

    signals = details_signals(frame, replay_template, ocr_backend)
    assert signals.template is True
    assert signals.labels is False
    assert signals.confirmed is False


# --- pure-function guarantees ----------------------------------------------


def test_labels_are_matched_exactly_not_as_substrings():
    """'Really' and 'Rally' both pass a substring test. 'All In' is on the
    betting screen, two characters from 'Ally'."""
    for text in ("Really", "Rally", "Alliance", "All In", "AllIn"):
        assert text.strip().casefold() not in TAB_LABELS
    for text in ("Ally", " enemy ", "ENEMY"):
        assert text.strip().casefold() in TAB_LABELS


def test_it_never_touches_the_device():
    """The predicate takes a frame and a backend. It has no device handle, so it
    cannot tap even by mistake."""
    assert "self" not in inspect.signature(is_details_screen).parameters
    src = inspect.getsource(is_details_screen) + inspect.getsource(details_signals)
    for forbidden in ("tap(", "swipe(", "hold(", "press_back", "navigate"):
        assert forbidden not in src
