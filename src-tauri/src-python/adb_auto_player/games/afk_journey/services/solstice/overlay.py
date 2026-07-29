"""The odds strip drawn inside Android, and what it takes to drive it.

Pure. This module formats text and emits adb argument lists; it never touches a device
and imports nothing that does, which is what makes all of it testable without adb.

The overlay exists because the number is only useful during the ~20 seconds a draft is
open, and the log is on the other screen from the game. It lives inside Android rather
than on the host desktop for one reason: a collaborator running this on Windows against a
different emulator has to see it too.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .odds import Prediction

PACKAGE = "com.webdevbar.oddsoverlay"
SERVICE = f"{PACKAGE}/.OverlayService"

# The bundled APK's versionCode. Bump together with `versionCode` in the Gradle build, or
# an upgraded desktop app will keep running last month's overlay.
OVERLAY_VERSION = 1

# Roughly 33 glyphs fit the strip at the chosen size. Longer text does not wrap - it runs
# off the edge, silently, which is worse than saying less.
MAX_CHARS = 40

_VERSION = re.compile(r"versionCode=(\d+)")


def display_text(prediction: Prediction | None, gate: str | None) -> str:
    """One line for the strip, or the no-call text.

    Never a bare percentage. `odds.format_odds` refuses to emit one on the grounds that a
    number without its interval invites acting on a coin flip that happens to read 54%,
    and that reasoning is strongest here, at the surface where somebody is about to bet.

    Args:
        prediction: The current estimate, or None when there is nothing to show.
        gate: Why the number must not be shown, or None if it may be.

    Returns:
        The text to paint. Never empty - clearing is a separate command, because an empty
        string means "detach the view" rather than "paint nothing".
    """
    if gate is not None or prediction is None:
        # Not blankness. A viewer who sees nothing assumes the tool is broken; one who
        # sees a number assumes it is a call. Neither is true when the gate is closed.
        return "- no call -"

    blue = round(prediction.p_mid * 100)
    # The complement rather than an independent rounding: a viewer reads these as two
    # halves of one thing, and 34% / 67% from separate rounding looks like a defect.
    red = 100 - blue
    low = round(prediction.p_low * 100)
    high = round(prediction.p_high * 100)
    return f"BLUE {blue}%  |  RED {red}%   {low}-{high}%"


def update_command(text: str) -> list[str]:
    """Start the service, or update it if it is already running.

    ONE mechanism for both, deliberately. A broadcast reaches a runtime-registered
    receiver only while that process is alive, so it cannot revive a service the OS has
    killed - and an action-only `am broadcast` is an implicit broadcast, which API 26+
    will not deliver to a manifest receiver anyway. Routing every update through
    `onStartCommand` is what makes "the next pick brings it back" true rather than
    hopeful.
    """
    # QUOTED. adb runs this through the device's /bin/sh, which parses `|` as a pipe -
    # the display text contains one, and unquoted it produced "RED: inaccessible or not
    # found" and no service. Single quotes are safe because the text is generated here
    # and never contains one; `_shell_safe` enforces that rather than trusting it.
    return ["am", "start-foreground-service", SERVICE, "--es", "text", _shell_safe(text)]


def _shell_safe(text: str) -> str:
    """Wrap text so the device shell treats it as one argument.

    Anything that could end the quoting is stripped rather than escaped: this is a display
    string we generate, so there is no legitimate case for a quote or a backslash in it,
    and silently dropping one is better than a command that parses as something else.
    """
    cleaned = text.replace("'", "").replace("\\", "")
    return f"'{cleaned}'"


def clear_command() -> list[str]:
    """Paint nothing.

    The service detaches the view rather than blanking it. A translucent attached surface
    should be invisible, but the bot reads this same screen, and a detached window is the
    only state provably identical to never having installed the overlay.
    """
    return update_command("")


def stop_command() -> list[str]:
    """End the run. The overlay does not outlive the mode that started it."""
    return ["am", "force-stop", PACKAGE]


def grant_command() -> list[str]:
    """Grant the overlay permission without any Settings UI.

    Re-issued after every install: an uninstall/install cycle loses the grant, and
    re-granting one already in place costs nothing.
    """
    return ["appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "allow"]


def version_command() -> list[str]:
    """Read the installed versionCode, if the package is there at all."""
    return ["dumpsys", "package", PACKAGE]


def parse_version(dumpsys_output: str) -> int | None:
    """The installed versionCode, or None if the package is not installed.

    Args:
        dumpsys_output: Raw output of `dumpsys package <pkg>`.

    Returns:
        The versionCode, or None.
    """
    found = _VERSION.search(dumpsys_output or "")
    return int(found.group(1)) if found else None


def needs_install(installed: int | None, packaged: int) -> bool:
    """Whether to install the bundled APK over what is on the device.

    Strictly older only. A developer testing a locally built overlay must not have it
    clobbered by the bundled one on the next collection run.

    Args:
        installed: versionCode on the device, or None if absent.
        packaged: versionCode of the APK shipped with this build.

    Returns:
        True if the bundled APK should be installed.
    """
    return installed is None or installed < packaged


def safe_shell(device, cmdargs: list[str]) -> str | None:
    """Run an adb shell command, swallowing every failure.

    The overlay is a display: a run must not end because a strip of text did not paint.

    Args:
        device: Anything with a `shell(cmdargs)` method.
        cmdargs: The command, already split.

    Returns:
        The output, or None on any failure - which every caller treats as "no overlay".
    """
    try:
        result = device.shell(cmdargs)
        return result if isinstance(result, str) else str(result)
    except Exception as exc:  # noqa: BLE001 - a display is never worth a match
        logging.debug(f"[SC-81] overlay command failed: {exc}")
        return None


def apk_path() -> Path | None:
    """The bundled APK, if we can find it.

    Same resolver ladder as `bundled_db` and `solstice_icon_dir`: an explicit override,
    the packaged resource directory beside the executable, then a development checkout. A
    hardcoded path once left the icon library empty on every machine but one - silently,
    because an empty library is indistinguishable from a bad frame at the call site.
    """
    from .paths import resource_file

    return resource_file(Path("solstice_clash") / "odds-overlay.apk")
