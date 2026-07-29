"""The odds bubble drawn inside Android, and what it takes to drive it.

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

_VERSION = re.compile(r"versionCode=(\d+)")


def update_command(mode: str, percent: int) -> list[str]:
    """Show the bubble, or hide it.

    Two extras and nothing else: `mode` is "hidden", "blue" or "red", and `pct` is the
    favoured side's percentage. Neither value can contain a space, so neither needs
    quoting - which matters because a quoted single string leaked its own quote into the
    rendered text on device, as "63'%".

    ONE mechanism for start and update, deliberately. A broadcast reaches a
    runtime-registered receiver only while that process is alive, so it cannot revive a
    service the OS has killed, and an action-only broadcast is implicit - which API 26+
    will not deliver to a manifest receiver anyway. Routing every update through
    `onStartCommand` is what makes "the next pick brings it back" true rather than hopeful.

    Args:
        mode: "blue", "red", or "hidden".
        percent: The favoured side's probability, 0-100. Ignored when hidden.

    Returns:
        The adb shell command, already split.
    """
    # `-n` is REQUIRED, not stylistic. `am` parses a bare component as the trailing
    # argument of the intent spec, so extras after it are dropped SILENTLY: the service
    # starts, receives nothing, and paints nothing. Verified on device - without -n the
    # intent prints as MAIN/LAUNCHER with no "(has extras)".
    return [
        "am", "start-foreground-service", "-n", SERVICE,
        "--es", "mode", mode,
        "--ei", "pct", str(int(percent)),
    ]


def bubble_for(prediction, gate: str | None) -> tuple[str, int]:
    """(mode, percent) for a prediction - the only place that decides what is shown.

    Nothing is drawn without a favourite. That is not tidiness: inside the middle band the
    model is right 50.2% of the time across 705 predictions, a literal coin flip, while
    outside it it is right about 58%. So "no favourite" and "nothing worth showing" are
    the same condition, and the bubble appearing at all is the first half of the signal.
    """
    if gate is not None or prediction is None:
        return "hidden", 0
    left = prediction.p_mid
    if abs(left - 0.5) < NO_CALL_MARGIN:
        return "hidden", 0
    return ("blue", round(left * 100)) if left > 0.5 else ("red", round((1 - left) * 100))


# Half-width of the band where the model has nothing to say. 0.02 puts the cut at 48-52%,
# where measured accuracy is 50.2% over 705 predictions.
NO_CALL_MARGIN = 0.02


# Android posts "<app> is displaying over other apps" itself, from the system package,
# the moment a SYSTEM_ALERT_WINDOW view is attached. Nothing in the APK can prevent it and
# there is no channel-block subcommand in `cmd notification`, so the only lever available
# over adb is to snooze it. A day at a time is plenty - a collection run is hours, and the
# next run snoozes it again.
NOTICE_KEY = f"0|android|0|com.android.server.wm.AlertWindowNotification - {PACKAGE}|1000"
SNOOZE_MS = 86_400_000


def snooze_notice_command() -> list[str]:
    """Dismiss Android's own overlay notice for a day.

    Cosmetic, and deliberately best-effort: the notice is a host-side popup that Waydroid
    forwards to the desktop, so it never appears in a captured frame and cannot affect
    what the automation reads. It is only in the way of the person watching.
    """
    return ["cmd", "notification", "snooze", "--for", str(SNOOZE_MS), NOTICE_KEY]


# Where the APK is staged on the device before installing. /data/local/tmp is the one
# directory the adb shell user can always write and the package manager can always read.
STAGED_APK = "/data/local/tmp/odds-overlay.apk"


def dexopt_skip_command() -> list[str]:
    """Turn off ahead-of-time compilation for installs.

    Waydroid's `dex2oat` hangs. Measured on LineageOS 20 for Waydroid: installd killed it
    after 570 SECONDS on a 4KB dex, leaving seven install sessions wedged at 90% and every
    `pm install` blocked forever. With compilation skipped the same APK installs in 25s.

    Shell can set this - no root, which Waydroid does not offer anyway. It does not
    survive a reboot, which is why it is issued as part of installing rather than once by
    hand: the mode must work on a machine nobody prepared.

    The app runs interpreted instead of compiled. For a service that draws one view, that
    costs nothing measurable.
    """
    return ["setprop", "pm.dexopt.install", "skip"]


def install_command() -> list[str]:
    """Install the staged APK. `-r` replaces, `-t` allows a debug-signed build."""
    return ["pm", "install", "-r", "-t", STAGED_APK]


def clear_command() -> list[str]:
    """Hide the bubble.

    The service detaches the view rather than blanking it: the automation reads this same
    screen, and a detached window is the only state provably identical to never having
    installed the overlay.
    """
    return update_command("hidden", 0)


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
