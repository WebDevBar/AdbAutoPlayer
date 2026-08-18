# CLI revamp - intent and requirements (recorded 2026-08-19)

We want to improve or revamp the CLI interface so the bot is fully usable over SSH with no
desktop anywhere. Context: the bot is moving to the server (headless Waydroid/emulator; see
`fedora-setup` repo, `reference/streaming-2026-08-18-sunshine-moonlight-notes.md` rounds 4-5).
A TUI would be nice, but taking it in steps is fine - polish the existing CLI first.

## The three things that matter (user requirements, in order)

1. **Pick a task** - list the registered game tasks and choose one, instead of having to know
   the exact command name for `main_cli.py`.
2. **Define or edit the custom loops** - Custom Routine creation/editing from the CLI (today
   this lives in the GUI; the CLI can only run what already exists).
3. **Tail the log while running** - view the last or last-few log lines during a run (the
   current `--output terminal` stream is a start; a bounded "last N lines" view is the want).

## Target workflow this serves

```
ssh server
  -> launch Waydroid (or emulator) headless
  -> remote into it for eyes when needed (RustDesk or scrcpy - WE WILL TEST BOTH)
  -> in the same SSH session: launch the AAP CLI, pick task, watch the log tail
```

## Starting point (verified 2026-08-19)

- `src-tauri/src-python/adb_auto_player/main_cli.py` is a working entry point: any registered
  game task as a positional command, `--output terminal|text|raw`, `--log-level`, auto-resolved
  `--app-config-dir`/`--resource-dir`.
- Gaps vs the requirements: no task listing/picker (1), no Custom Routine editing (2), no
  bounded log view (3), and a real bot session has never been proven end-to-end GUI-less
  (flagged as a verify item in fedora-setup).

## Process note

Requirement 2 especially touches config formats shared with the GUI - when this work starts,
it goes through the normal flow: brainstorm -> spec -> plan -> approval, in this repo's
`docs/superpowers/`.
