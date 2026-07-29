"""What the overlay strip says, and what it takes to get it there - pure, no device."""

from adb_auto_player.games.afk_journey.services.solstice.odds import (
    Match,
    fit,
    predict,
)
from adb_auto_player.games.afk_journey.services.solstice.overlay import display_text


def _fitted():
    """A model that has learned one dominant hero, so predictions are not 50/50."""
    out = []
    for i in range(40):
        allies = [f"h{i % 7}", f"h{(i + 1) % 7}"]
        foes = [f"h{(i + 2) % 7}", f"h{(i + 3) % 7}", f"h{(i + 4) % 7}"]
        if i % 2 == 0:
            out.append(Match(left=("star", *allies), right=tuple(foes), left_won=True))
        else:
            out.append(Match(left=tuple(foes), right=("star", *allies), left_won=False))
    return fit(out)


def _prediction():
    return predict(
        _fitted(), ["star", "h0", "h1"], ["h2", "h3", "h4"],
        left_rating=4400, right_rating=4100,
    )


def test_it_shows_both_sides_and_the_interval():
    text = display_text(_prediction(), None)
    assert "BLUE" in text and "RED" in text
    # Never a bare percentage: odds.format_odds refuses to emit one because a number
    # without its interval invites acting on a coin flip that happens to read 54%, and
    # that reasoning is strongest where somebody is about to bet.
    assert "-" in text


def test_a_gated_prediction_says_so_rather_than_showing_a_number():
    """Blankness reads as broken and a number reads as a call. Neither is true here."""
    text = display_text(None, "4/6 picks locked, need 4")
    assert "%" not in text
    assert "no call" in text.lower()


def test_it_fits_the_strip():
    """~33 glyphs fit at the chosen size. Longer does not wrap - it runs off the edge."""
    assert len(display_text(_prediction(), None)) <= 40


def test_the_two_sides_always_sum_to_a_hundred():
    """A viewer reads these as complements; rounding must not break that."""
    import re

    for lr, rr in ((4400, 4100), (4100, 4400), (4250, 4250), (4600, 4000)):
        p = predict(_fitted(), ["star", "h0", "h1"], ["h2", "h3", "h4"],
                    left_rating=lr, right_rating=rr)
        blue, red = (int(x) for x in re.findall(r"(\d+)%", display_text(p, None))[:2])
        assert blue + red == 100


def test_an_update_is_a_service_start_not_a_broadcast():
    """A broadcast reaches a runtime receiver only while the process is alive, so it
    cannot revive a service the OS killed - and an action-only broadcast is implicit,
    which API 26+ will not deliver to a manifest receiver either. Routing updates through
    onStartCommand means one command both starts and updates."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import (
        SERVICE,
        update_command,
    )

    cmd = update_command("BLUE 34%  |  RED 66%")
    assert cmd[:2] == ["am", "start-foreground-service"]
    assert SERVICE in cmd
    assert "--es" in cmd and "text" in cmd
    # Quoted: the device shell parses `|` as a pipe, so the text is one argument.
    assert "BLUE 34%  |  RED 66%" in cmd[-1]


def test_clearing_sends_an_empty_string():
    from adb_auto_player.games.afk_journey.services.solstice.overlay import clear_command

    # Quoted empty string, not a bare one - an unquoted empty argument disappears
    # entirely and the service would be started with no text extra at all.
    assert clear_command()[-1] == "''"


def test_the_version_is_read_from_dumpsys():
    from adb_auto_player.games.afk_journey.services.solstice.overlay import parse_version

    assert parse_version("    versionCode=7 minSdk=24 targetSdk=33") == 7
    assert parse_version("no such package") is None


def test_a_missing_or_older_install_is_replaced_and_a_newer_one_is_not():
    """Never downgrade: a developer testing a local build must not have it clobbered by
    the bundled one on the next run."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import needs_install

    assert needs_install(None, 3) is True
    assert needs_install(2, 3) is True
    assert needs_install(3, 3) is False
    assert needs_install(4, 3) is False


def test_an_overlay_failure_never_reaches_the_caller():
    """The overlay is a display. A run must not end because a strip of text did not
    paint - every adb call is wrapped and no failure is fatal."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import safe_shell

    class Exploding:
        def shell(self, *_args, **_kwargs):
            raise RuntimeError("device went away")

    assert safe_shell(Exploding(), ["am", "force-stop", "x"]) is None


def test_a_successful_shell_returns_its_output():
    from adb_auto_player.games.afk_journey.services.solstice.overlay import safe_shell

    class Fine:
        def shell(self, cmdargs, **_kwargs):
            return f"ran {' '.join(cmdargs)}"

    assert safe_shell(Fine(), ["echo", "hi"]) == "ran echo hi"


def test_the_text_survives_the_device_shell():
    """adb runs the command through /bin/sh on the device, which parses `|` as a pipe.
    The display text contains one, and unquoted it produced 'RED: inaccessible or not
    found' and no service started."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import update_command

    cmd = update_command("BLUE 34%  |  RED 66%   25-44%")
    assert cmd[-1].startswith("'") and cmd[-1].endswith("'")
    assert "BLUE 34%  |  RED 66%   25-44%" in cmd[-1]


def test_a_quote_in_the_text_cannot_break_out_of_the_argument():
    """Not paranoia about user input - there is none. It is that a generated string with
    an apostrophe would silently turn into a different command."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import update_command

    assert update_command("it's 60%")[-1] == "'its 60%'"
