# Odds display - next steps

The odds are computed and logged. What is missing is somewhere to READ them that is not
a log stream, while the draft is running and your eyes are on the phone window.

Decided 2026-07-28. Nothing here is built yet.

## 1. Panel in the AdbAutoPlayer UI

The spec's intended home (section 12): current estimate, its interval, the trust label
and the evidence counts, in a fixed panel rather than a scrolling log.

Svelte work in `src/`, fed by the existing PyTauri event channel that already carries log
messages. No new transport needed - the odds already travel as log lines; the panel needs
a structured event instead of parsing text.

Correct long-term home, but it is on the other screen from the game.

## 2. In-Android overlay (APK)

An overlay drawn INSIDE Waydroid, over the game, which is where the player is looking.

### Why an APK is unavoidable

`cmd notification post` works over adb with no app, and the notification is genuinely
posted - verified on device, `importance=3` on channel `shell_cmd`. But a banner over a
fullscreen game needs importance 4 or a full-screen intent, and `cmd notification` exposes
neither. There is no adb-only route. Confirmed against the subcommand list on Android 13.

### Shape

A single foreground service with a `SYSTEM_ALERT_WINDOW` overlay. No activity, no UI
framework. Controlled entirely from adb, which means the mode drives it and the user
never types a command:

```bash
adb install odds-overlay.apk
adb shell appops set <pkg> SYSTEM_ALERT_WINDOW allow    # no Settings UI needed
adb shell am start-foreground-service <pkg>/.OverlayService
adb shell am broadcast -a <pkg>.ODDS --es text "BLUE 39% | RED 61%"
adb shell am force-stop <pkg>
```

### Two hard constraints

**Position: y 1620-1900, full width.** Everything the bot reads sits between y=80 and
y=1603 across essentially the full width - pick strips, the pool grid, prematch and locked
rows, the ratings band, the VS anchor, the All In tiles. The strip below them is the only
safe zone. An overlay is drawn on the same surface, so `screencap` and the H264 stream both
capture it: anything it covers becomes unreadable. This is the chat-widget bug again, and
that one cost 0.10-0.14 of match score on one cell.

**Transparent unless it has something to say.** The window stays alive but paints nothing
between drafts, so a captured frame is identical to having no overlay at all. It clears
when the picks lock and the fight starts.

`FLAG_NOT_TOUCHABLE` is required, or the overlay swallows taps the bot needs to make.

### Verification before it ships

Capture a frame with the overlay painted, run the six-cell identification on it, and
confirm the scores match the same frame without it. Do not assume the geometry holds.

### Cost

This adds a Java/Android build to a project that has none - a toolchain and a build
artifact, for what is otherwise a hundred lines of service. That is the real price, not
the code.

## 3. Hero strengths on the compete draft screen (the pick assistant)

Raised by the operator, 2026-07-29. Arguably more valuable than the win probability, and
a different product: instead of telling you who will win after the draft, it tells you
**who to pick during it.**

The model already holds a strength number per hero. On the compete draft screen the 20
available heroes are on screen and the operator is choosing between them, with 20+ seconds
per pick - far more time than the spectate mode's ~2s budget. Overlaying each card with
its strength turns a model nobody can see into the one thing a player actually wants.

### Stored or computed live?

Open question, and the answer is not obvious.

- **Computed live** keeps the current architecture intact: matches are the only stored
  truth, everything else is derived, nothing syncs, nothing goes stale. The full refit is
  **9.6ms** measured at 363 matches, so recomputing per draft costs nothing.
- **Stored** would be needed only if the fit ever grows expensive enough to matter, or if
  something outside a run needs the numbers - a UI panel, a report, seeding the next event.

The operator's own point argues for computing live: 20+ seconds per pick means inline
detection of all 20 cards plus a fit is feasible without storing anything. The default
should be live, with storage introduced only when something measurably needs it.

Note the one real exception: **seeding a new event** with the previous event's strengths
needs a snapshot at the boundary. That snapshot is local and never synced, since it is
re-derivable from matches everyone already has.

### What it would take

- Read the 20-card pool - already done in spectate mode, so the geometry exists.
- Score each card and overlay it. In-Android that is the same APK as the odds strip with a
  different layout; on the desktop it could be a panel.
- Beware the capture constraint: an overlay on the pool grid covers exactly what the bot
  reads. In compete mode the bot does not read the pool, so this may be free - but it must
  be verified, not assumed.

### Why it is worth more than the odds

A win probability tells you what is about to happen. This tells you what to do about it,
which is the difference between a scoreboard and an advisor - and it is useful at 55%
confidence where a win probability is not.
