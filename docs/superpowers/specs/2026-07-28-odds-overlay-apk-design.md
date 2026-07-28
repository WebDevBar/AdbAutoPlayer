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

54px allows roughly 40px glyphs. `BLUE 34%  |  RED 66%` fits at full width; the interval,
the pick count and the signal list do not, and stay in the log.

## Components

Three pieces, each usable and testable without the others.

### 1. `android/odds-overlay/` - the APK

Kotlin. A foreground service holding one `SYSTEM_ALERT_WINDOW` view and one broadcast
receiver. No activity, no UI framework, no dependencies beyond androidx-core.

```text
TYPE_APPLICATION_OVERLAY
FLAG_NOT_FOCUSABLE | FLAG_NOT_TOUCHABLE | FLAG_LAYOUT_NO_LIMITS
gravity  = TOP | START
x, y     = 0, 1866
width    = MATCH_PARENT
height   = 54
```

Driven entirely over adb, so the mode drives it and the user never types a command:

```bash
adb install -r odds-overlay.apk
adb shell appops set <pkg> SYSTEM_ALERT_WINDOW allow
adb shell am start-foreground-service <pkg>/.OverlayService
adb shell am broadcast -a <pkg>.ODDS --es text "BLUE 34%   |   RED 66%"
adb shell am broadcast -a <pkg>.ODDS --es text ""     # clear
adb shell am force-stop <pkg>
```

An empty string paints nothing, so a captured frame is byte-identical to having no
overlay. That is the state it holds between drafts and after the picks lock.

The service must post its own foreground notification to satisfy Android 13; that
notification is on a MIN-importance channel and is not the display.

### 2. `services/solstice/overlay.py` - the controller

Pure. Takes a `Prediction` and returns the display string; takes a device state and
returns the adb argument lists to run. It executes nothing and imports no device module,
which is what makes it fully unit-testable.

```python
def display_text(prediction: Prediction) -> str: ...
def install_plan(installed_version: int | None, packaged_version: int) -> list[list[str]]: ...
def update_command(text: str) -> list[str]: ...
```

The APK resolves through the same ladder as `bundled_db()` and `solstice_icon_dir()`:
explicit env override, packaged resource directory beside the executable, then a
development checkout. The hardcoded-vault-path bug that silently emptied the icon library
on every machine but one is the reason that ladder exists; the APK gets it from the start.

### 3. Mixin wiring

Four call sites, all of which already exist in `solstice_clash.py`:

| Existing point | Overlay action |
|---|---|
| draft screen confirmed | ensure installed, start service |
| odds computed (4th pick onward) | broadcast the text |
| picks lock / `_log_final_odds` | broadcast empty |
| run ends | `force-stop` |

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
| `appops` grant denied | logged once, run continues; the overlay simply never appears |
| broadcast fails mid-draft | the previous number stays up; it is cleared at lock regardless |
| service killed by the OS | the next pick's broadcast restarts it |
| device is not Waydroid | no difference - every call is adb, nothing assumes the host |

Every adb call returns a bool and is wrapped. The mode never branches on overlay state,
and no overlay failure is ever fatal.

## Verification

**The gate that matters.** Capture a draft frame with the overlay painted and the same
frame without it. Run the six-cell identification, the ratings read, the pool read and the
spectator read on both, and assert every score matches. Committed as a fixture pair under
`tests/games/afk_journey/services/solstice/data/`, not performed as a manual check - the
whole point is that it keeps being true after someone moves the strip by 20px.

The Kotlin side gets no unit tests: it is one view and one receiver, and the behaviour
worth testing is whether it lands in the right pixels, which only the fixture pair above
can answer.

The controller gets full unit tests - it formats text and emits command lists, both pure.

## Out of scope

- The panel in the AdbAutoPlayer UI (section 1 of the next-steps document). Independent,
  and still worth building.
- Any interaction: the overlay never accepts input, has no settings UI, and cannot be
  moved by the user.
- Showing anything other than the two percentages. The interval, trust label, pick count
  and signal list stay in the log where there is room for them.
