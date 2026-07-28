# Solstice Clash odds overlay - Android APK

Design, 2026-07-28. Nothing here is built yet.

Supersedes section 2 of `docs/solstice-clash/odds-display-next-steps.md`, whose safe-zone
claim is no longer true. Section 1 of that document, the panel in the AdbAutoPlayer UI,
is untouched and remains a separate piece of work.

## The problem

The odds are computed and logged. The log is on the other screen from the game, and the
number is only useful during the ~20 seconds the draft is open and betting is still
possible. It has to appear where the eyes already are.

## Why an APK rather than a host window

A window drawn by the host compositor beside the Waydroid viewer is cheaper by days: no
Java, no Android SDK, no build artifact, and it can never appear in a capture. Codex
recommended exactly that and recommended against this design.

It was rejected for one reason: **it does not travel.** A collaborator running the fork on
Windows against a different emulator sees nothing, and neither does a real phone. The
display has to live where the game lives.

`cmd notification post` was verified to work over adb with no app at all, but a banner
over a fullscreen game needs importance 4 or a full-screen intent and that subcommand
exposes neither. There is no adb-only route on Android 13.

## Position: y 1866-1920, full width

The previous document put the overlay at y 1620-1900 on the grounds that everything the
bot reads sits between y=80 and y=1603. That was true when it was written and is not true
now:

| What | Where | Conflict |
|---|---|---|
| deepest template / cell read | y <= 1603 | none |
| `POPUP_DISMISS_AT` tap | (540, 1700) | inside the old band |
| `SPECTATOR_BAND` OCR | x 560-900, y 1740-1860 | inside the old band |

The crowd work added a read and the popup handling added a tap, both into what the
document called safe. An overlay is drawn on the same surface `screencap` and the H264
stream capture, so anything it covers becomes unreadable - this is the chat-widget bug the
project has already paid for once, at 0.10-0.14 of match score on one cell.

**y 1866-1920** clears every read band and every tap point with 6px to spare, measured
against a real draft frame. Nothing is drawn there by the game.

That choice is worth more than the 54px it costs:

- No blanking logic. The controller never has to know when a read is about to happen.
- The Android 12+ untrusted-touch rule becomes irrelevant. A touch passing through a SAW
  overlay that is more than ~80% opaque is discarded rather than delivered, and whether
  that applies to adb-**injected** taps is not established. At this position no tap ever
  passes through the overlay, so the question never has to be answered.
- `FLAG_NOT_TOUCHABLE` is still set, as defence in depth rather than as the mechanism.

### What it displays

54px allows roughly 40px glyphs, and 1080px of width fits about 33 of them - more than
the two percentages need.

```text
BLUE 34%  |  RED 66%   25-44%  ?      odds, with the interval and the unproven mark
- not enough data -                   gate_reason returned a reason
```

**Never a bare percentage.** `format_odds` refuses to emit one, on the grounds that a
number without its interval invites acting on a coin flip that happens to read 54%, and
that reasoning is strongest at the surface where a person is actually about to bet. The
interval fits; it goes in.

The `?` is the UNPROVEN marker, and it stays until the model beats the base rate out of
sample. It is one character standing in for the log block's full header, and the reason it
is not omitted is that the overlay is the only place most people will ever read the number.

When `gate_reason` returns a reason the overlay shows the no-call text, not a number and
not blankness: a person who sees nothing assumes the tool is broken, and a person who sees
a number assumes it is a call. The trust label, the pick count and the signal list do not
fit and stay in the log.

### The gesture bar, and the limits of this measurement

Measured on Waydroid: `base=1080x1920`, `app=1080x1883`, `navigation_mode=2`, so the
bottom 37px are nominally the system gesture bar. It does not matter there - the bar is
transparent, the device is an emulator used only for the fullscreen game, and
`NavigationBar0` reports `isVisible=false` while AFK Journey is in the foreground.

**That measurement covers one of the two environments this design exists to serve.** The
whole reason for choosing an APK over a host window is that it reaches a collaborator's
Windows emulator and a real phone, and y=1866 is an absolute pixel constant measured on
neither. Three-button navigation with an opaque bar, a display cutout, or a different
resolution would all put the strip somewhere the measurement does not predict.

The runtime geometry assertion below is the honest mitigation, not a formality: it is what
makes this position safe on a device nobody has measured. `FLAG_LAYOUT_NO_LIMITS` is what
lets the window extend past the app area into that region at all.

## Components

Three pieces, each usable and testable without the others.

### 1. `android/odds-overlay/` - the APK

Kotlin. A foreground service holding one `SYSTEM_ALERT_WINDOW` view. No activity, no
broadcast receiver, no UI framework, no dependencies beyond androidx-core.

**The manifest is load-bearing and every line of it is a way this fails silently:**

```xml
<uses-permission android:name="android.permission.SYSTEM_ALERT_WINDOW"/>
<uses-permission android:name="android.permission.FOREGROUND_SERVICE"/>
<service
    android:name=".OverlayService"
    android:exported="true"
    android:permission="android.permission.DUMP"/>
```

`android:exported="true"` is required or the shell cannot start it: components without an
intent filter default to private on Android 12+, and `am start-foreground-service` from
adb would fail with a permission denial.

`android:permission` is what stops that from meaning *anything on the device* can start
the service and paint arbitrary text across the bottom of the screen. `DUMP` is
signature-or-privileged, so no ordinary app can hold it, while the adb shell does -
verified on the target device, where `dumpsys` runs. The effect is that the service is
reachable from adb and from nothing else. Exported without this is a real hole on a phone,
even if it is harmless on a single-purpose emulator. The `SYSTEM_ALERT_WINDOW` permission must be
declared even though the grant is done by `appops` - the app-op is the grant, the manifest
entry is the request, and neither substitutes for the other. `Settings.canDrawOverlays()`
is checked before `addView` and the service stops itself if it is false, rather than
throwing.

`targetSdk` is 33. Android 34 requires every foreground service to declare a
`foregroundServiceType`, and there is no type that honestly describes this one.

Window parameters, in full - the defaults are wrong in two ways that matter:

```text
type     = TYPE_APPLICATION_OVERLAY
flags    = FLAG_NOT_FOCUSABLE | FLAG_NOT_TOUCHABLE | FLAG_LAYOUT_NO_LIMITS
format   = PixelFormat.TRANSLUCENT      // default is OPAQUE - see below
gravity  = TOP | START
x        = 0
width    = MATCH_PARENT
y        = round(0.9719 * displayHeight)    // 1866/1920
height   = round(0.0281 * displayHeight)    //   54/1920
textSize = round(0.0208 * displayHeight)px  //   40/1920
setFitInsetsTypes(0)                    // default fits all system bars
layoutInDisplayCutoutMode = ALWAYS
view background = Color.TRANSPARENT
```

**Every dimension is a fraction of the display height, never a pixel constant.** The
fractions come from a 1080x1920 measurement, and the height is read at runtime from the
real display metrics rather than assumed. Nobody knows what a collaborator's emulator
reports, and a hardcoded 1866 on a 2340-tall screen would land in the middle of the pool
grid - the one thing this position exists to avoid. The text size scales by the same
factor, so the strip stays legible instead of becoming a thin line of unreadable glyphs.

This does not make the design resolution-independent on its own: the bot's own read bands
are absolute 1080x1920 coordinates, so a different resolution is a problem well before the
overlay is. It does mean the overlay is not the thing that breaks first, and that the
geometry assertion has something correct to assert against.

`setFitInsetsTypes(0)` is what makes that y mean the same y in the same coordinate space
`screencap` returns. The API 30+ default fits the window inside the system bars, so
without it the origin is the top of the app area rather than the top of the display and
the strip lands somewhere else. `FLAG_LAYOUT_NO_LIMITS` permits extending past that area;
it does not by itself define the origin.

`format` defaulting to `OPAQUE` would paint a black bar across the bottom of every
captured frame even with no text - the exact failure this position was chosen to avoid.

### One command for start and update

```bash
adb install -r odds-overlay.apk
adb shell appops set <pkg> SYSTEM_ALERT_WINDOW allow
adb shell am start-foreground-service <pkg>/.OverlayService --es text "BLUE 34%  |  RED 66%"
adb shell am start-foreground-service <pkg>/.OverlayService --es text ""   # clear
adb shell am force-stop <pkg>
```

**Every update is a `start-foreground-service`, not a broadcast.** A broadcast reaches a
runtime-registered receiver only while the process is alive, so it cannot revive a service
the OS has killed - and an action-only `am broadcast` is an implicit broadcast, which
API 26+ will not deliver to a manifest receiver anyway. Routing updates through
`onStartCommand` means the same command starts the service when it is dead and delivers
text when it is alive, which is also what makes "the next pick restarts it" true rather
than hopeful.

An empty string **removes the view** from the window manager rather than setting empty
text. A translucent attached surface should be invisible, but "should be" is not good
enough here: detaching is the only state that is provably identical to never having
installed the overlay, and that is the state it holds between drafts and after the picks
lock.

The service posts its own foreground notification to satisfy Android 13, on a
MIN-importance channel. That notification is not the display.

### Everything expensive happens once, at run start

Install, the `dumpsys package` version comparison, the `appops` grant and the geometry
assertion all run once in `collect_solstice_clash`, before the collection loop begins.
None of them touch a draft.

This is not tidiness. A draft is ~20-25 seconds and the first pick reads already compete
with the chat drag, the model fit and the ratings OCR; a screencap alone is measured at
3-4 seconds on this device. An `adb install` on the draft path would cost pick reads and
push the first displayed number past the moment a bet is possible, which is the one thing
this feature exists to prevent.

During a draft the mode sends text and nothing else.

### 2. `services/solstice/overlay.py` - the controller

Pure. Takes a `Prediction` and returns the display string; takes a device state and
returns the adb argument lists to run. It executes nothing and imports no device module,
which is what makes it fully unit-testable.

```python
def display_text(prediction: Prediction) -> str: ...
def install_plan(installed_version: int | None, packaged_version: int) -> list[list[str]]: ...
def update_command(text: str) -> list[str]: ...   # start-foreground-service --es text
def clear_command() -> list[str]: ...             # the same, with an empty string
```

The APK resolves through the same ladder as `bundled_db()` and `solstice_icon_dir()`:
explicit env override, packaged resource directory beside the executable, then a
development checkout. The hardcoded-vault-path bug that silently emptied the icon library
on every machine but one is the reason that ladder exists; the APK gets it from the start.

### 3. Mixin wiring

Four call sites, all of which already exist in `solstice_clash.py`:

| Existing point | Overlay action |
|---|---|
| run start, once | install, grant, and assert the geometry - see below |
| draft screen confirmed | clear, in case a previous match left something painted |
| odds computed (4th pick onward) | `--es text "<odds>"` |
| picks lock / `_log_final_odds` | `--es text "<final odds>"` - **shown, not cleared** |
| fight starts (prematch screen gone) | `--es text ""` |
| run ends | `force-stop` |

There is no separate "start" step. The first update starts the service, every later one
reuses it, and one that arrives after the OS killed it starts it again - which is the
whole reason the transport is a service start rather than a broadcast.

**The final number is displayed, not discarded.** Betting stays open through the
last-chance countdown on the locked screen - `MATCH_TIMEOUT` is measured from the prematch
screen precisely because that countdown runs after it - so the complete six-hero
prediction, which the code itself calls "the one a person would actually act on", arrives
while a bet is still possible. Clearing at lock would blank the display during the most
decisive seconds and show only running estimates on partial comps. The strip is safe over
that screen too: the deepest cell registered on `prematch_locked_teams` ends at y=1580,
286px above the overlay.

**The clear on draft-confirm is a retry, not a duplicate.** A clear command that fails at
the end of a match would otherwise leave the previous match's number painted over the next
draft's first three picks, presenting a stale call as a current one. Clearing again when a
draft is confirmed costs one command and closes that window.

## Distribution

The APK ships inside the release as a resource, next to the hero icons and the seed
database. On a collect run the mode compares the installed `versionCode` against the
packaged one and installs if it is missing or older. A Windows collaborator gets the
overlay by updating the desktop app; nothing is typed.

Two commands are added to the mode list for the cases automation should not own:

- **Install Odds Overlay** - install and grant, then stop. For setting up a device
  without starting a collection run.
- **Uninstall Odds Overlay** - `pm uninstall`. The overlay is a thing installed on the
  user's device, and removing it must not require knowing an adb incantation.

### Signing

A dedicated keystore held at `~/.local/share/webdevbar/`, outside every repository, and
injected into CI as a secret exactly as `ADB_SYNC_KEY_BUILTIN` is. A stable signature is
what allows `adb install -r` to upgrade in place; a per-build key would force an uninstall
and lose the grant with it. It is not a security boundary - the APK is handed to
collaborators - and it is never committed.

## Build

The Android SDK and a JDK are installed on the host. This adds a toolchain to a project
that has none, which is the real price of this design and not the ~150 lines of Kotlin.

- Local: `./gradlew assembleDebug`, warm, is seconds. That matters because overlay
  geometry is inherently trial and error and a CI round trip is minutes.
- Release: the existing `release-webdevbar.yaml` gains a job that builds the signed APK
  and commits it into the bundle. The shipped artifact is built by CI, never by whatever
  happened to be on a developer's machine.

The Gradle wrapper is committed. The SDK is not.

## Failure handling

The overlay is a display. Nothing it does may cost a match.

| Failure | Behaviour |
|---|---|
| APK not found in resources | logged once, run continues without it |
| `install` fails | logged once, run continues |
| `appops` grant denied | logged once, run continues; the service stops itself rather than throwing |
| an update fails mid-draft | the previous number stays up; it is cleared at lock regardless |
| service killed by the OS | the next pick's `start-foreground-service` brings it back with the current text |
| APK installed with a different signature | `install -r` fails; the controller falls back to `pm uninstall` then install, which is the only recovery and loses the app-op grant, so the grant is re-issued unconditionally after any install |
| device is not Waydroid | no difference - every call is adb, nothing assumes the host |

Every adb call returns a bool and is wrapped. The mode never branches on overlay state,
and no overlay failure is ever fatal.

## Verification

Three checks, and the order below is from "would catch a real regression" downward. The
first draft of this section listed only the fixture pair and claimed it would keep being
true after someone moved the strip by 20px. It would not: a committed PNG does not
re-capture itself, so a Kotlin change moves the overlay and the test keeps passing against
the old frame forever.

**1. The geometry assertion, at run start.** The window parameters are a request, not a
guarantee - the system may reposition or resize a `TYPE_APPLICATION_OVERLAY` window, and
an inset default would move it silently. So before the first draft, the controller paints
a known marker, takes one `screencap`, finds its bounding box and asserts it lands in the
expected fraction of the captured frame's height, across the full width - computed from
the capture rather than compared against a remembered 1866. A mismatch disables the overlay for the run and logs
the measured box rather than quietly covering a read band.

This is the only check that runs on a device nobody measured, which makes it the one that
matters on Windows and on a phone. Its weakness is that it fails in the field rather than
in CI, and its failure is silent to a user who is not reading the log - so the disable is
logged at WARNING with the measured box, and the mode reports "overlay disabled" once in
its run summary rather than only in a line that scrolls away.

**2. The fixture pair, regenerated rather than remembered.** A draft frame with the
overlay painted and the same frame without it, both committed under
`tests/games/afk_journey/services/solstice/data/`. The test runs the six-cell
identification, the ratings read, the pool read and the spectator read on both and asserts
every score matches.

The pair is produced by a committed script that captures both frames from a live device,
and the script - not a person's memory - is what a geometry change requires re-running.
Without that, this test is a permanent record of one afternoon.

**3. The overlay shows the right thing.** Every adb call is wrapped and never fatal, and
the mode never branches on overlay state, which means a wrong extra key or a component
renamed in a refactor produces a feature that silently does not exist while every test
above stays green. So the with-overlay fixture is OCR'd in the strip region and the text
matched against what the controller emitted for that prediction. It is the only assertion
that connects the number the model produced to pixels a person can read.

**4. Transparent when cleared.** Capture with the overlay installed but cleared, and diff
against a capture with the service stopped. They must be identical - this is what catches
an `OPAQUE` format or a stray background colour.

The Kotlin side gets no unit tests: it is one view and one `onStartCommand`, and the
behaviour worth testing is whether it lands in the right pixels, which only the fixture
pair and the geometry assertion above can answer.

The controller gets full unit tests - it formats text and emits command lists, both pure.

## Out of scope

- The panel in the AdbAutoPlayer UI (section 1 of the next-steps document). Independent,
  and still worth building.
- Any interaction: the overlay never accepts input, has no settings UI, and cannot be
  moved by the user.
- Showing anything other than the two percentages. The interval, trust label, pick count
  and signal list stay in the log where there is room for them.
