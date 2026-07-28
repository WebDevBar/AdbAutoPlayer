# Solstice Clash Odds Overlay APK Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the live odds inside Android, on a strip at the bottom of the game, so a person watching a draft can read the number without looking at another screen.

**Architecture:** A Kotlin foreground service holding one `SYSTEM_ALERT_WINDOW` view, driven entirely over adb by a pure Python controller. The controller decides and formats; the mixin executes. The APK ships as a bundled resource and installs itself.

**Tech Stack:** Kotlin (no framework, androidx-core only), Gradle, Android SDK 33; Python 3.13, adbutils, pytest.

## Global Constraints

- **Android `targetSdk` is 33.** API 34 requires a `foregroundServiceType` and none honestly describes this service.
- **Every dimension is a fraction of display height**, read at runtime. Never a pixel constant. `y = 0.9719 * h`, `height = 0.0281 * h`, `textSize = 0.0208 * h`.
- **The overlay must never cost a match.** Every adb call returns a bool, is wrapped, and no failure is fatal. The mode never branches on overlay state.
- **Python style:** ruff, line length 88, Google docstrings, `X | None` not `Optional[X]`, module constants `UPPER_SNAKE_CASE`. Run `uvx ruff check --fix` from the repo root, never from `src-tauri/`.
- **Kotlin/Java style:** K&R braces.
- **Never use the Edit tool on code files** - it converts straight quotes to curly ones. Use `git apply` with a unified diff, or the Write tool for whole files.
- **Package name:** `com.webdevbar.oddsoverlay`. **Action extra:** `text`.
- **Tests run from `src-tauri/src-python/`**: `../../.venv/bin/python -m pytest tests/... -q`

---

### Task 1: The controller - what to display

**Files:**
- Create: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/overlay.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay.py`

**Interfaces:**
- Consumes: `Prediction` from `.odds` (fields `p_mid`, `p_low`, `p_high`, `signals`)
- Produces: `display_text(prediction: Prediction | None, gate: str | None) -> str`, `PACKAGE: str`, `SERVICE: str`

- [ ] **Step 1: Write the failing test**

```python
"""What the overlay strip says - pure, no device."""

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


def test_it_shows_both_sides_and_the_interval():
    p = predict(_fitted(), ["star", "h0", "h1"], ["h2", "h3", "h4"],
                left_rating=4400, right_rating=4100)
    text = display_text(p, None)
    assert "BLUE" in text and "RED" in text
    assert "%" in text
    # Never a bare percentage: odds.format_odds refuses to emit one, and that reasoning
    # is strongest at the surface where somebody is about to bet.
    assert "-" in text


def test_a_gated_prediction_says_so_rather_than_showing_a_number():
    """Blankness reads as broken and a number reads as a call. Neither is true here."""
    text = display_text(None, "4/6 picks locked, need 4")
    assert "%" not in text
    assert "no call" in text.lower()


def test_it_fits_the_strip():
    """~33 characters at the chosen glyph size. Longer silently truncates on device."""
    p = predict(_fitted(), ["star", "h0", "h1"], ["h2", "h3", "h4"],
                left_rating=4400, right_rating=4100)
    assert len(display_text(p, None)) <= 40


def test_the_two_sides_always_sum_to_a_hundred():
    """A viewer reads these as complements; rounding must not break that."""
    import re

    for lr, rr in ((4400, 4100), (4100, 4400), (4250, 4250), (4600, 4000)):
        p = predict(_fitted(), ["star", "h0", "h1"], ["h2", "h3", "h4"],
                    left_rating=lr, right_rating=rr)
        blue, red = (int(x) for x in re.findall(r"(\d+)%", display_text(p, None))[:2])
        assert blue + red == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_overlay.py -q`
Expected: FAIL with `ModuleNotFoundError: ... solstice.overlay`

- [ ] **Step 3: Write minimal implementation**

```python
"""What the odds strip inside Android says, and what it takes to get it there.

Pure: it formats text and emits command argument lists. It never touches a device and
imports nothing that does, which is what makes all of it unit-testable without adb.
"""

from __future__ import annotations

from pathlib import Path

from .odds import Prediction

PACKAGE = "com.webdevbar.oddsoverlay"
SERVICE = f"{PACKAGE}/.OverlayService"

# Roughly 33 glyphs fit the strip at the chosen size. Longer text does not wrap - it
# runs off the edge, silently, which is worse than saying less.
MAX_CHARS = 40


def display_text(prediction: Prediction | None, gate: str | None) -> str:
    """One line for the strip, or the no-call text.

    Never a bare percentage. `odds.format_odds` refuses to emit one on the grounds that a
    number without its interval invites acting on a coin flip that happens to read 54%,
    and that reasoning is strongest here, where somebody is about to bet.

    Args:
        prediction: The current estimate, or None when there is nothing to show.
        gate: Why the number must not be shown, or None if it may be.

    Returns:
        The text to paint. Empty is never returned - clearing is a separate command.
    """
    if gate is not None or prediction is None:
        return "- no call -"

    blue = round(prediction.p_mid * 100)
    # Complement rather than round independently: a viewer reads these as two halves of
    # one thing, and 34% / 67% from independent rounding looks like a bug.
    red = 100 - blue
    low = round(prediction.p_low * 100)
    high = round(prediction.p_high * 100)
    return f"BLUE {blue}%  |  RED {red}%   {low}-{high}%"


def apk_path() -> Path | None:
    """The bundled APK, if we can find it.

    Same resolver ladder as `bundled_db` and `solstice_icon_dir`: an explicit override,
    the packaged resource directory beside the executable, then a development checkout.
    A hardcoded path once meant a feature that worked on exactly one machine.
    """
    from .paths import resource_file

    return resource_file(Path("solstice_clash") / "odds-overlay.apk")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_overlay.py -q`
Expected: PASS, 4 tests

This will FAIL on `apk_path` because `resource_file` does not exist yet. Task 2 adds it. For this task, temporarily leave `apk_path` out of the module and add it in Task 2 - the tests above do not reference it.

- [ ] **Step 5: Commit**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/overlay.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay.py
git commit -m "feat(solstice): the text the odds overlay displays

Never a bare percentage - format_odds refuses to emit one because a number
without its interval invites acting on a coin flip that reads 54%, and that
reasoning is strongest at the surface where somebody is about to bet. The two
sides are complements rather than independently rounded, because a viewer reads
them as two halves of one thing and 34/67 looks like a defect."
```

---

### Task 2: Generalise the resource resolver

**Files:**
- Modify: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/paths.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_paths.py`

**Interfaces:**
- Produces: `resource_file(relative: Path) -> Path | None` - used by `overlay.apk_path`

The APK needs the same three-step lookup as the database and icons. Rather than a third copy, extract it.

- [ ] **Step 1: Write the failing test**

```python
def test_resource_file_prefers_an_explicit_override(tmp_path, monkeypatch):
    """The override exists so a developer can point at a build without reinstalling."""
    from adb_auto_player.games.afk_journey.services.solstice.paths import resource_file

    target = tmp_path / "thing.apk"
    target.write_bytes(b"x")
    monkeypatch.setenv("ADB_SOLSTICE_RESOURCE_DIR", str(tmp_path))
    assert resource_file(Path("thing.apk")) == target


def test_resource_file_returns_none_when_it_is_not_there(tmp_path, monkeypatch):
    """None, not an exception: a missing resource disables a feature, never a run."""
    from adb_auto_player.games.afk_journey.services.solstice.paths import resource_file

    monkeypatch.setenv("ADB_SOLSTICE_RESOURCE_DIR", str(tmp_path))
    assert resource_file(Path("absent.apk")) is None
```

Add `from pathlib import Path` to the test file's imports if it is not already there.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_paths.py -q -k resource_file`
Expected: FAIL with `ImportError: cannot import name 'resource_file'`

- [ ] **Step 3: Write minimal implementation**

Append to `paths.py`:

```python
def resource_file(relative: Path) -> Path | None:
    """A file shipped with the application, wherever it happens to live.

    The same ladder `bundled_db` and `solstice_icon_dir` use, and for the same reason: a
    hardcoded path meant the icon library was silently EMPTY on every install but one,
    and an empty library is indistinguishable from a bad frame at the call site.

    Args:
        relative: Path under the data directory, e.g. `solstice_clash/odds-overlay.apk`.

    Returns:
        The file, or None if no candidate exists.
    """
    candidates: list[Path] = []

    override = os.environ.get("ADB_SOLSTICE_RESOURCE_DIR")
    if override:
        candidates.append(Path(override).expanduser() / relative)

    here = Path(__file__).resolve()
    for parents_up in (7, 8, 9):
        if len(here.parents) > parents_up:
            candidates.append(here.parents[parents_up] / "data" / relative)

    for parent in here.parents:
        if (parent / "data" / relative).exists():
            candidates.append(parent / "data" / relative)
            break

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_paths.py -q`
Expected: PASS, all existing path tests plus 2 new

Then add `apk_path` to `overlay.py` exactly as written in Task 1 Step 3, and re-run the overlay tests.

- [ ] **Step 5: Commit**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/paths.py \
        src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/overlay.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_paths.py
git commit -m "refactor(solstice): one resource resolver instead of a third copy

The APK needs the same override / packaged / checkout ladder the database and
icons use. A hardcoded path is what once left the icon library empty on every
install but one, silently, so the APK gets the ladder from the start rather
than after the same bug."
```

---

### Task 3: The controller - what commands to run

**Files:**
- Modify: `src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/overlay.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay.py`

**Interfaces:**
- Produces: `update_command(text: str) -> list[str]`, `clear_command() -> list[str]`, `stop_command() -> list[str]`, `grant_command() -> list[str]`, `version_command() -> list[str]`, `parse_version(dumpsys_output: str) -> int | None`, `needs_install(installed: int | None, packaged: int) -> bool`

- [ ] **Step 1: Write the failing test**

```python
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
    assert "BLUE 34%  |  RED 66%" in cmd


def test_clearing_sends_an_empty_string():
    from adb_auto_player.games.afk_journey.services.solstice.overlay import clear_command

    assert clear_command()[-1] == ""


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_overlay.py -q`
Expected: FAIL with `ImportError: cannot import name 'update_command'`

- [ ] **Step 3: Write minimal implementation**

Append to `overlay.py`:

```python
import re

_VERSION = re.compile(r"versionCode=(\d+)")


def update_command(text: str) -> list[str]:
    """Start the service, or update it if it is already running.

    ONE mechanism for both, deliberately. A broadcast reaches a runtime-registered
    receiver only while that process is alive, so it cannot revive a service the OS has
    killed, and an action-only `am broadcast` is an implicit broadcast that API 26+ will
    not deliver to a manifest receiver anyway. Routing every update through
    `onStartCommand` is what makes "the next pick brings it back" true rather than hopeful.
    """
    return ["am", "start-foreground-service", SERVICE, "--es", "text", text]


def clear_command() -> list[str]:
    """Paint nothing. The service detaches the view rather than blanking it."""
    return update_command("")


def stop_command() -> list[str]:
    """End the run. The overlay does not outlive the mode that started it."""
    return ["am", "force-stop", PACKAGE]


def grant_command() -> list[str]:
    """Grant the overlay permission without any Settings UI.

    Re-issued after every install: an uninstall/install cycle loses the grant, and a
    grant that is already in place costs nothing to repeat.
    """
    return ["appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "allow"]


def version_command() -> list[str]:
    """Read the installed versionCode, if the package is there at all."""
    return ["dumpsys", "package", PACKAGE]


def parse_version(dumpsys_output: str) -> int | None:
    """The installed versionCode, or None if the package is not installed."""
    found = _VERSION.search(dumpsys_output or "")
    return int(found.group(1)) if found else None


def needs_install(installed: int | None, packaged: int) -> bool:
    """Whether to install the bundled APK over what is on the device.

    Strictly older only. A developer testing a locally built overlay must not have it
    clobbered by the bundled one on the next collection run.
    """
    return installed is None or installed < packaged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_overlay.py -q`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/overlay.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay.py
git commit -m "feat(solstice): the adb commands that drive the overlay

Every update is a start-foreground-service, not a broadcast. A broadcast reaches
a runtime receiver only while the process lives, so it cannot revive a service
the OS killed, and an action-only broadcast is implicit - which API 26+ will not
deliver to a manifest receiver either. One mechanism starts and updates, which
is what makes 'the next pick brings it back' true rather than hopeful."
```

---

### Task 4: The APK

**Files:**
- Create: `android/odds-overlay/settings.gradle.kts`
- Create: `android/odds-overlay/build.gradle.kts`
- Create: `android/odds-overlay/app/build.gradle.kts`
- Create: `android/odds-overlay/app/src/main/AndroidManifest.xml`
- Create: `android/odds-overlay/app/src/main/java/com/webdevbar/oddsoverlay/OverlayService.kt`
- Create: `android/odds-overlay/README.md`

**Interfaces:**
- Produces: `app/build/outputs/apk/release/app-release.apk` with `versionCode 1`, package `com.webdevbar.oddsoverlay`, service `.OverlayService`

Prerequisite, run once by the operator (needs sudo, so it is theirs to run):

```bash
sudo dnf install -y java-21-openjdk-devel
mkdir -p ~/Android/cmdline-tools && cd ~/Android/cmdline-tools
curl -L -o tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip -q tools.zip && mv cmdline-tools latest && rm tools.zip
export ANDROID_HOME=~/Android
~/Android/cmdline-tools/latest/bin/sdkmanager --sdk_root=$ANDROID_HOME \
    "platform-tools" "platforms;android-33" "build-tools;33.0.2"
```

- [ ] **Step 1: Write the manifest**

`android/odds-overlay/app/src/main/AndroidManifest.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW"/>
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>

    <application android:label="Odds Overlay" android:icon="@android:drawable/ic_dialog_info">
        <!-- exported: or the shell cannot start it - components without an intent filter
             default to private on Android 12+.
             permission: DUMP is signature-or-privileged, so no ordinary app can hold it
             and the adb shell does. Without it, exported means ANY app on the device can
             paint arbitrary text across the bottom of the screen. -->
        <service
            android:name=".OverlayService"
            android:exported="true"
            android:permission="android.permission.DUMP"/>
    </application>
</manifest>
```

- [ ] **Step 2: Write the service**

`android/odds-overlay/app/src/main/java/com/webdevbar/oddsoverlay/OverlayService.kt`:

```kotlin
package com.webdevbar.oddsoverlay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.TextView

/**
 * One strip of text at the bottom of the screen, driven entirely over adb.
 *
 * Every update arrives as a service start rather than a broadcast, so the same command
 * starts this service when it is dead and updates it when it is alive.
 */
class OverlayService : Service() {

    private var view: TextView? = null
    private lateinit var windows: WindowManager

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        windows = getSystemService(WINDOW_SERVICE) as WindowManager
        startForeground(NOTIFICATION_ID, notification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val text = intent?.getStringExtra("text") ?: ""
        if (text.isEmpty()) {
            detach()
        } else {
            show(text)
        }
        // NOT START_STICKY: a restart with a null intent would repaint a stale number
        // with no way to know it is stale. The next pick sends another command anyway.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        detach()
        super.onDestroy()
    }

    /** Paint, attaching the window on first use. */
    private fun show(text: String) {
        // Checked rather than assumed: without the grant addView throws, and a crash
        // loop is a worse failure than no overlay.
        if (!Settings.canDrawOverlays(this)) {
            stopSelf()
            return
        }
        val existing = view
        if (existing != null) {
            existing.text = text
            return
        }
        val height = resources.displayMetrics.heightPixels
        val strip = TextView(this).apply {
            this.text = text
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.TRANSPARENT)
            textSize = (TEXT_FRACTION * height / resources.displayMetrics.density)
            gravity = Gravity.CENTER
            setShadowLayer(6f, 0f, 0f, Color.BLACK)
        }
        windows.addView(strip, params(height))
        view = strip
    }

    /**
     * Remove the view entirely rather than setting empty text.
     *
     * A translucent attached surface should be invisible, but "should be" is not enough:
     * the bot reads this same screen, and a detached window is the only state provably
     * identical to never having installed the overlay.
     */
    private fun detach() {
        view?.let { windows.removeView(it) }
        view = null
    }

    private fun params(height: Int): WindowManager.LayoutParams {
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            Math.round(HEIGHT_FRACTION * height),
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            // The default is OPAQUE, which would paint a black bar across the bottom of
            // every captured frame even with no text - the exact failure this position
            // was chosen to avoid.
            PixelFormat.TRANSLUCENT,
        )
        params.gravity = Gravity.TOP or Gravity.START
        params.x = 0
        params.y = Math.round(Y_FRACTION * height)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            // Without this the origin is the top of the APP area rather than the display,
            // and y means something different from what screencap returns.
            params.fitInsetsTypes = 0
            params.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        }
        return params
    }

    private fun notification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "Odds overlay", NotificationManager.IMPORTANCE_MIN)
            )
        }
        return Notification.Builder(this, CHANNEL)
            .setContentTitle("Odds overlay")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .build()
    }

    companion object {
        // Fractions of display height, measured on 1080x1920: y=1866, height=54, text=40.
        // Never pixel constants - nobody knows what a collaborator's emulator reports,
        // and a hardcoded 1866 on a 2340-tall screen lands in the middle of the pool grid.
        private const val Y_FRACTION = 0.9719f
        private const val HEIGHT_FRACTION = 0.0281f
        private const val TEXT_FRACTION = 0.0208f
        private const val CHANNEL = "odds"
        private const val NOTIFICATION_ID = 1
    }
}
```

- [ ] **Step 3: Write the Gradle files**

`android/odds-overlay/settings.gradle.kts`:

```kotlin
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "odds-overlay"
include(":app")
```

`android/odds-overlay/build.gradle.kts`:

```kotlin
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
```

`android/odds-overlay/app/build.gradle.kts`:

```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.webdevbar.oddsoverlay"
    compileSdk = 33

    defaultConfig {
        applicationId = "com.webdevbar.oddsoverlay"
        minSdk = 26
        // targetSdk 33 deliberately: API 34 requires every foreground service to declare
        // a foregroundServiceType, and none of them honestly describes this one.
        targetSdk = 33
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Debug signing so `adb install -r` upgrades in place during development.
            // CI re-signs with the release keystore.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
}
```

- [ ] **Step 4: Build it and confirm the artifact exists**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer/android/odds-overlay
export ANDROID_HOME=~/Android JAVA_HOME=/usr/lib/jvm/java-21-openjdk
gradle wrapper --gradle-version 8.9      # once, then commit the wrapper
./gradlew assembleRelease
ls -la app/build/outputs/apk/release/app-release.apk
```

Expected: the APK exists. Then verify it installs and paints:

```bash
adb install -r app/build/outputs/apk/release/app-release.apk
adb shell appops set com.webdevbar.oddsoverlay SYSTEM_ALERT_WINDOW allow
adb shell am start-foreground-service com.webdevbar.oddsoverlay/.OverlayService --es text "BLUE 34%  |  RED 66%   25-44%"
adb shell screencap -p /sdcard/o.png && adb pull /sdcard/o.png /tmp/o.png
```

Open `/tmp/o.png` and confirm the strip is at the bottom and the game above it is untouched.

- [ ] **Step 5: Commit**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add android/
git commit -m "feat(solstice): the odds overlay APK

One foreground service, one SYSTEM_ALERT_WINDOW view, no activity and no
broadcast receiver. Every dimension is a fraction of display height read at
runtime rather than a pixel constant, because nobody knows what a collaborator's
emulator reports and a hardcoded 1866 on a 2340-tall screen lands in the middle
of the pool grid.

Three defaults are wrong here and all three are set explicitly: the pixel format
would be OPAQUE and paint a black bar across every captured frame, fitInsetsTypes
would measure y from the app area rather than the display, and an exported
service with no android:permission is startable by any app on the device."
```

---

### Task 5: Wire it into the mode

**Files:**
- Modify: `src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/solstice_clash.py`
- Test: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay.py`

**Interfaces:**
- Consumes: `overlay.display_text`, `overlay.update_command`, `overlay.clear_command`, `overlay.stop_command`, `overlay.grant_command`, `overlay.version_command`, `overlay.parse_version`, `overlay.needs_install`, `overlay.apk_path`
- Produces: `_overlay_setup() -> None`, `_overlay_say(prediction, gate) -> None`, `_overlay_clear() -> None`, `_overlay_stop() -> None` on the mixin

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_overlay.py -q -k safe_shell`
Expected: FAIL with `ImportError: cannot import name 'safe_shell'`

- [ ] **Step 3: Write minimal implementation**

Append to `overlay.py`:

```python
import logging


def safe_shell(device, cmdargs: list[str]) -> str | None:
    """Run an adb shell command, swallowing every failure.

    The overlay is a display: a run must not end because a strip of text did not paint.
    Returns None on any failure, which every caller treats as "no overlay this time".
    """
    try:
        result = device.shell(cmdargs)
        return result if isinstance(result, str) else str(result)
    except Exception as exc:  # noqa: BLE001 - a display is never worth a match
        logging.debug(f"[SC-81] overlay command failed: {exc}")
        return None
```

Then add to `SolsticeClashMixin` in `solstice_clash.py`, using `git apply` with a unified diff (never the Edit tool):

```python
    def _overlay_setup(self) -> None:
        """Install, grant and verify the overlay. Called ONCE, before the loop.

        Not on the draft path. A draft is ~20-25 seconds and the first pick reads already
        compete with the chat drag, the model fit and the ratings OCR; an adb install
        there would cost pick reads and push the first number past the moment a bet is
        possible, which is the one thing this feature exists to prevent.
        """
        self._overlay_ok = False
        try:
            apk = overlay.apk_path()
            if apk is None:
                logging.info("[SC-82] no overlay APK bundled - running without it")
                return
            device = self._device.d
            installed = overlay.parse_version(
                overlay.safe_shell(device, overlay.version_command()) or ""
            )
            if overlay.needs_install(installed, OVERLAY_VERSION):
                logging.info(f"[SC-82] installing the odds overlay from {apk}")
                self._device.d.d.install(str(apk), nolaunch=True, silent=True)
            # Unconditionally after any install: an uninstall/install cycle loses the
            # grant, and re-granting an existing grant costs nothing.
            overlay.safe_shell(device, overlay.grant_command())
            self._overlay_ok = True
        except Exception as exc:  # noqa: BLE001 - never worth a match
            logging.info(f"[SC-82] overlay unavailable, continuing without it: {exc}")

    def _overlay_say(self, prediction, gate: str | None) -> None:
        """Paint the current number, or the no-call text."""
        if not getattr(self, "_overlay_ok", False):
            return
        text = overlay.display_text(prediction, gate)
        overlay.safe_shell(self._device.d, overlay.update_command(text))

    def _overlay_clear(self) -> None:
        """Paint nothing.

        Also called when a draft is confirmed, as a retry: a clear that failed at the end
        of the last match would otherwise leave its number painted over the next draft's
        first three picks, showing a stale call as a current one.
        """
        if not getattr(self, "_overlay_ok", False):
            return
        overlay.safe_shell(self._device.d, overlay.clear_command())

    def _overlay_stop(self) -> None:
        """The overlay does not outlive the run that started it."""
        if not getattr(self, "_overlay_ok", False):
            return
        overlay.safe_shell(self._device.d, overlay.stop_command())
```

Add `OVERLAY_VERSION = 1` near the other module constants, `from ..services.solstice import overlay` to the imports, and these four call sites:

- `collect_solstice_clash`, after `self.navigate_to_world()`: `self._overlay_setup()`
- `_watch_draft`, immediately after the `[SC-54] draft screen` log: `self._overlay_clear()`
- `_log_odds` and `_log_final_odds`, after their `format_odds` loop: `self._overlay_say(prediction, gate)`
- `_run_one_match`, where the prematch screen is left for the fight: `self._overlay_clear()`
- `_collect_forever`, at the end of the run: `self._overlay_stop()`

- [ ] **Step 4: Run the tests**

```bash
cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/ -q
```
Expected: PASS, all tests. Then confirm nothing undefined slipped in:

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
uvx ruff check --select F821,F841 src-tauri/src-python/adb_auto_player/games/afk_journey/
```
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add src-tauri/src-python/adb_auto_player/games/afk_journey/
git commit -m "feat(solstice): drive the overlay from the collection mode

Install, version check and grant happen once at run start, never on the draft
path - a draft is ~20 seconds and a screencap alone costs 3-4 of them, so an
install there would push the first number past the moment a bet is possible.

The final number is displayed rather than cleared: betting stays open through
the last-chance countdown on the locked screen, which is why MATCH_TIMEOUT is
measured from the prematch screen. Clearing at lock would blank the display
during the most decisive seconds."
```

---

### Task 6: Prove the overlay does not blind the bot

**Files:**
- Create: `src-tauri/src-python/scripts/capture_overlay_fixtures.py`
- Create: `src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay_capture.py`
- Add: `src-tauri/src-python/tests/games/afk_journey/services/solstice/data/draft_with_overlay.png`
- Add: `src-tauri/src-python/tests/games/afk_journey/services/solstice/data/draft_without_overlay.png`

**Interfaces:**
- Consumes: `read_ratings`, `read_pools`, `read_spectators`, the cell registry reads
- Produces: the committed fixture pair

- [ ] **Step 1: Write the capture script**

```python
"""Capture the overlay fixture pair from a live device.

Run this, not your memory, whenever the strip moves. A committed PNG does not
re-capture itself: without regenerating the pair, moving the overlay in Kotlin leaves
the test passing forever against a frame from the afternoon somebody drew it.

Usage: with a draft on screen,
    ../../.venv/bin/python scripts/capture_overlay_fixtures.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adb_auto_player.games.afk_journey.services.solstice import overlay  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / (
    "tests/games/afk_journey/services/solstice/data"
)


def adb(*args: str) -> bytes:
    return subprocess.run(["adb", *args], capture_output=True, check=True).stdout


def capture(name: str) -> None:
    (DATA / name).write_bytes(adb("exec-out", "screencap", "-p"))
    print(f"wrote {DATA / name}")


def main() -> int:
    adb("shell", *overlay.clear_command())
    capture("draft_without_overlay.png")
    adb("shell", *overlay.update_command("BLUE 34%  |  RED 66%   25-44%"))
    capture("draft_with_overlay.png")
    adb("shell", *overlay.clear_command())
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the test**

```python
"""The overlay must not change a single thing the bot reads.

This is the chat-widget bug the project has already paid for once, at 0.10-0.14 of match
score on one cell. The overlay is drawn on the same surface screencap and the H264 stream
capture, so anything it covers becomes unreadable.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

DATA = Path(__file__).parent / "data"
WITH = DATA / "draft_with_overlay.png"
WITHOUT = DATA / "draft_without_overlay.png"

pytestmark = pytest.mark.skipif(
    not (WITH.exists() and WITHOUT.exists()),
    reason="run scripts/capture_overlay_fixtures.py against a live draft first",
)


def _frames():
    return cv2.imread(str(WITH)), cv2.imread(str(WITHOUT))


def test_everything_above_the_strip_is_byte_identical():
    """The strongest possible statement, and the cheapest to check: if not one pixel the
    bot reads has changed, no read can have changed."""
    painted, clean = _frames()
    assert painted.shape == clean.shape
    height = clean.shape[0]
    cut = int(0.9719 * height)
    assert np.array_equal(painted[:cut], clean[:cut])


def test_the_strip_itself_did_change():
    """Guards against a fixture pair captured with the overlay never painting, which
    would make the test above pass for the wrong reason."""
    painted, clean = _frames()
    cut = int(0.9719 * clean.shape[0])
    assert not np.array_equal(painted[cut:], clean[cut:])


def test_the_ratings_read_the_same_with_and_without():
    from adb_auto_player.games.afk_journey.services.solstice.ratings import read_ratings
    from adb_auto_player.ocr.rapidocr_backend import RapidOCRBackend

    ocr = RapidOCRBackend()
    painted, clean = _frames()
    assert read_ratings(painted, ocr, "draft") == read_ratings(clean, ocr, "draft")


def test_the_pools_and_spectators_read_the_same():
    from adb_auto_player.games.afk_journey.services.solstice.pools import (
        read_pools,
        read_spectators,
    )
    from adb_auto_player.ocr.rapidocr_backend import RapidOCRBackend

    ocr = RapidOCRBackend()
    painted, clean = _frames()
    assert read_pools(painted, ocr) == read_pools(clean, ocr)
    assert read_spectators(painted, ocr) == read_spectators(clean, ocr)
```

- [ ] **Step 3: Capture the fixtures**

Start a collection run, wait for a draft screen, then in another terminal:

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer/src-tauri/src-python
../../.venv/bin/python scripts/capture_overlay_fixtures.py
```

- [ ] **Step 4: Run the test**

```bash
cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/test_overlay_capture.py -q
```
Expected: PASS, 4 tests. If `test_everything_above_the_strip_is_byte_identical` fails, the overlay is covering something the bot reads - fix the geometry, do NOT relax the test.

- [ ] **Step 5: Commit**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add src-tauri/src-python/scripts/capture_overlay_fixtures.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/test_overlay_capture.py \
        src-tauri/src-python/tests/games/afk_journey/services/solstice/data/draft_with*.png
git commit -m "test(solstice): prove the overlay changes nothing the bot reads

Byte-identical above the strip, plus the ratings, pools and spectator reads
compared with and without. This is the chat-widget bug the project already paid
for once, at 0.10-0.14 of match score on one cell.

The pair is regenerated by a committed script rather than by memory: a committed
PNG does not re-capture itself, so without the script, moving the strip in Kotlin
leaves this passing forever against an old frame."
```

---

### Task 7: Install and uninstall commands, and the release build

**Files:**
- Modify: `src-tauri/src-python/adb_auto_player/games/afk_journey/mixins/solstice_clash.py`
- Modify: `.github/workflows/release-webdevbar.yaml`
- Modify: `src-tauri/tauri.bundle.linux.json` and `src-tauri/tauri.bundle.windows.json`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: everything above
- Produces: two registered GUI commands; the APK bundled into the release at `data/solstice_clash/odds-overlay.apk`

- [ ] **Step 1: Add the two commands**

```python
    @register_command(
        name="SolsticeClashInstallOverlay",
        gui=GUIMetadata(
            label="WDB: Install Odds Overlay",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Install the in-game odds strip and grant it permission",
        ),
    )
    def install_odds_overlay(self) -> None:
        """Install and grant, without starting a collection run.

        A collection run does this itself. This exists for setting a device up
        beforehand, and so that the thing installed on someone's device is visible in the
        mode list rather than being a side effect of something else.
        """
        self.start_up(device_streaming=False)
        self._overlay_setup()
        if getattr(self, "_overlay_ok", False):
            logging.info("[SC-82] odds overlay installed and granted")

    @register_command(
        name="SolsticeClashUninstallOverlay",
        gui=GUIMetadata(
            label="WDB: Uninstall Odds Overlay",
            category=AFKJCategory.EVENTS_AND_OTHER,
            tooltip="Remove the in-game odds strip from the device",
        ),
    )
    def uninstall_odds_overlay(self) -> None:
        """Remove it.

        The overlay is a thing installed on the user's own device, and removing it must
        not require knowing an adb incantation.
        """
        self.start_up(device_streaming=False)
        try:
            self._device.d.d.uninstall(overlay.PACKAGE)
            logging.info("[SC-82] odds overlay removed")
        except Exception as exc:  # noqa: BLE001
            logging.warning(f"[SC-82] could not remove the overlay: {exc}")
```

- [ ] **Step 2: Bundle the APK into the release**

Add a job step to `.github/workflows/release-webdevbar.yaml`, before the Tauri build step:

```yaml
      - name: Build the odds overlay APK
        run: |
          cd android/odds-overlay
          ./gradlew assembleRelease
          mkdir -p ../../data/solstice_clash
          cp app/build/outputs/apk/release/app-release.apk \
             ../../data/solstice_clash/odds-overlay.apk
```

The `data/` directory is already declared as a bundle resource in both
`tauri.bundle.linux.json` and `tauri.bundle.windows.json` (`"../data/": "./data/"`), so
no config change is needed - verify this with `grep -n '"../data/"' src-tauri/tauri.bundle.*.json` and only edit if it is absent.

- [ ] **Step 3: Verify the commands appear and work**

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
./.venv/bin/python -m adb_auto_player.main_cli GameGUIOptions 2>/dev/null | grep -i overlay
```
Expected: both labels listed. Then run the install command against the device and confirm with:

```bash
adb shell pm list packages | grep oddsoverlay
```

- [ ] **Step 4: Full test suite**

```bash
cd src-tauri/src-python && ../../.venv/bin/python -m pytest tests/games/afk_journey/services/solstice/ -q
```
Expected: PASS, all tests.

- [ ] **Step 5: Changelog and commit**

Add under `## [Unreleased]` / `### Added`:

```markdown
- **Solstice Clash: the odds appear inside the game.** A strip at the very bottom of the
  screen shows the current number during a draft, so it can be read without looking at
  another window. Two new commands install and remove it; a collection run installs it
  itself, so a Windows collaborator gets it by updating.
  - Positioned at the bottom 2.8% of the display, below everything the bot reads and
    below every point it taps, so it cannot blind the automation. Verified by a committed
    fixture pair: every read is compared with and without the overlay painted.
  - Every dimension is a fraction of display height read at runtime, never a pixel
    constant, because nobody knows what resolution a collaborator's emulator reports.
```

```bash
cd /home/toshe/Dev/webdevbar/adbautoplayer
git add -A
git commit -m "feat(solstice): install and uninstall the overlay from the mode list

A collection run installs it itself, so a Windows collaborator gets it by
updating the app. The explicit commands exist so that a thing installed on
someone's device is visible where they can see and remove it, rather than being
a side effect of something else."
```

---

## Self-Review

**Spec coverage.** Position and the fractional geometry: Task 4. Content including the
gated case: Task 1. Manifest, exported service, DUMP permission, TRANSLUCENT,
setFitInsetsTypes: Task 4. Update-as-service-start: Task 3. Controller purity: Tasks 1
and 3. Resource ladder: Task 2. Distribution and the two commands: Task 7. Lifecycle
including the final number shown at lock and the clear-on-draft retry: Task 5. Failure
handling: Task 5, `safe_shell`. The verification gate and fixture regeneration: Task 6.
Build toolchain: Task 4 prerequisite; CI: Task 7. Signing is the one spec item deferred -
Task 4 uses debug signing and the spec's release keystore is a CI concern that blocks
nothing until the first signed release.

**Placeholders.** None. Every code step carries the code.

**Type consistency.** `display_text(prediction, gate)` is defined in Task 1 and called
with the same two arguments in Task 5. `update_command`/`clear_command`/`stop_command`/
`grant_command`/`version_command` return `list[str]` throughout and are always passed to
`safe_shell(device, cmdargs)`. `OVERLAY_VERSION` in Task 5 matches `versionCode = 1` in
Task 4 - bump both together. `resource_file` is defined in Task 2 and used by
`overlay.apk_path` from Task 1, which is why Task 1 Step 4 says to add that function in
Task 2 rather than before it.
