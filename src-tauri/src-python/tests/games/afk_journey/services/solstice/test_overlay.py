"""What the odds bubble shows, and what it takes to get it there - pure, no device."""

from adb_auto_player.games.afk_journey.services.solstice.odds import (
    Match,
    fit,
    predict,
)


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


def test_the_overlay_notice_is_snoozed_by_key_for_a_day():
    """Android posts the "displaying over other apps" notice from the system package the
    moment the view attaches. Nothing in the APK can stop it and there is no channel-block
    subcommand, so snoozing is the only lever adb has."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import (
        PACKAGE,
        snooze_notice_command,
    )

    cmd = snooze_notice_command()
    assert cmd[:3] == ["cmd", "notification", "snooze"]
    assert cmd[cmd.index("--for") + 1] == "86400000"
    assert PACKAGE in cmd[-1] and cmd[-1].startswith("0|android|0|")


def test_an_update_is_a_service_start_not_a_broadcast():
    """A broadcast reaches a runtime receiver only while the process is alive, so it
    cannot revive a service the OS killed - and an action-only broadcast is implicit,
    which API 26+ will not deliver to a manifest receiver either. One command therefore
    both starts and updates."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import (
        SERVICE,
        update_command,
    )

    cmd = update_command("blue", 63)
    assert cmd[:2] == ["am", "start-foreground-service"]
    # `-n` is required, not stylistic: `am` parses a bare component as the intent spec's
    # trailing argument and silently drops every extra after it. Verified on device - the
    # service started, received nothing, and painted nothing.
    assert cmd[cmd.index(SERVICE) - 1] == "-n"
    assert "blue" in cmd and "63" in cmd


def test_the_two_fields_never_need_quoting():
    """A single "blue 63" string had to be quoted to survive the device shell, and the
    quote then leaked into the rendered text as "63'%". Neither field can contain a
    space, so neither is quoted and neither can be mangled."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import update_command

    for part in update_command("red", 58):
        assert "'" not in part and '"' not in part


def test_hiding_is_a_mode_not_an_empty_string():
    from adb_auto_player.games.afk_journey.services.solstice.overlay import clear_command

    assert "hidden" in clear_command()


def test_no_favourite_means_no_bubble():
    """Inside the middle band the model is right 50.2% of the time across 705
    predictions - a coin flip - while outside it it is right about 58%. So "no favourite"
    and "nothing worth showing" are the same condition."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import predict
    from adb_auto_player.games.afk_journey.services.solstice.overlay import bubble_for

    fitted = _fitted()
    even = predict(fitted, ["h0", "h1", "h2"], ["h3", "h4", "h5"])
    assert bubble_for(even, None)[0] == "hidden"
    assert bubble_for(None, "4/6 picks locked")[0] == "hidden"


def test_the_colour_names_the_favoured_side_and_the_number_is_its_own():
    """Only the favoured side's probability is shown; the complement is implied by the
    colour, which is what lets the plate be small."""
    from adb_auto_player.games.afk_journey.services.solstice.odds import predict
    from adb_auto_player.games.afk_journey.services.solstice.overlay import bubble_for

    p = predict(_fitted(), ["star", "h0", "h1"], ["h2", "h3", "h4"],
                left_rating=4600, right_rating=4100)
    mode, pct = bubble_for(p, None)
    assert mode in ("blue", "red")
    assert 50 < pct <= 100


def test_installing_skips_ahead_of_time_compilation():
    """Waydroid's dex2oat hangs - installd killed it after 570 seconds on a 4KB dex, and
    every pm install blocked forever behind sessions wedged at 90%. Skipping compilation
    installs the same APK in 25 seconds.

    Issued as part of installing rather than once by hand, because the property does not
    survive a reboot and the mode has to work on a machine nobody prepared."""
    from adb_auto_player.games.afk_journey.services.solstice.overlay import (
        dexopt_skip_command,
        install_command,
    )

    assert dexopt_skip_command() == ["setprop", "pm.dexopt.install", "skip"]
    assert install_command()[:2] == ["pm", "install"]
    assert "-t" in install_command()
