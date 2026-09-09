# One CLI, one settings layer, shared with the GUI - design

Written 2026-09-09, revision 2. Supersedes the withdrawn v1/v2 implementation plans, which were
drafted before `docs/cli-revamp-notes.md` was read and treated the menu as a feature of its own.

## The principle

**There is one way to run a task and one place settings live.** Running `afkadb SolsticeClashCollect`
and picking that task from a menu are the same code taking the same path; the menu only supplies
the name. The settings behind it are the same file the GUI reads and writes, resolved the same way,
validated by the same models.

Everything below follows from that. Where today's code has two answers, this design picks one.

## What already agrees, and what does not

**Task execution is already unified.** Both front ends call
`Execute.find_command_and_execute(command, get_game_tasks())` - `__main__.py:279` for the GUI,
`main_cli.py:97` for the CLI. Nothing needs to change here, and nothing should.

**Settings are not unified, in two ways:**

| | GUI | CLI |
|---|---|---|
| Where `App.toml` lives | Tauri's app config dir, `settings.rs:519-524` - on Linux `~/.config/com.AdbAutoPlayer.AdbAutoPlayer/` | whatever `--app-config-dir` is set to; `afk-bot.sh` passes the repo's `src-tauri/settings` |
| Editing | a form generated from the Pydantic models | nothing |

## The config layout is TWO-LEVEL, and `.parent` is correct

An earlier draft of this spec called `.parent` an assumption to remove. That was wrong. The
layout is:

```
<config>/App.toml            app-wide          settings.rs:519-524
<config>/<idx>/ADB.toml      per profile       commands.rs:26-41
<config>/<idx>/AFKJourney.toml
<config>/<idx>/data/         screenshots, guild-scan data
```

`app_config_dir` MEANS the profile directory (`__main__.py:153-155`), so reaching the root for
`App.toml` via `.parent` is right for the GUI. The defect was narrower: **the CLI is handed a flat
directory with no profile level**, so `.parent` walked above the files and found nothing. Because
`Game.app_settings` swallows every exception (`game/game.py:78`) and `from_toml` returns defaults
for a missing file, this failed in total silence - every CLI run used default `template_timeout`,
`action_delay` and `navigation_delay`.

**Already fixed, separately from this design:** `SettingsLoader.app_settings_path()` checks the
profile directory first and the parent second, serving both layouts. Both readers use it -
`game/game.py:73` and `util/execute.py:82`. There were two readers, not one; the earlier draft
named only `execute.py`, and `game.py` is the one that governs every tap and template wait.

## The resolver has two outputs, not one

- **Config root** - for `App.toml`.
- **Profile directory** - `<root>/<profile>/` for `ADB.toml`, the game TOMLs, and `data/`.

**Which profile:** `active_profile` from `App.toml` (`app_settings.py:96`, default `0`). It lives
in the root file, which is resolvable without knowing the profile, so there is no circularity.
This mirrors the GUI rather than inventing a CLI-only rule.

**`data/` moves with it.** `get_app_config_dir()` is also the `data_root` for `hero_scanner.py`
and every guild-scan mixin. Changing where the CLI points relocates screenshots and scan data.
Any change to the CLI's directory is therefore a **data move**, and must be done deliberately.

## The repo TOMLs are operator data, not shipped defaults

Nothing in the GUI reads `src-tauri/settings/` - no reference in any `.rs` file, and only
`main_cli.py:66` in Python. GUI defaults are **model defaults** (`toml_settings.py:24-26` returns
`cls()` when the file is absent).

So `src-tauri/settings/*.toml` are this operator's tuned values - `cf464b56` restored them from a
2026-07-29 vault backup. "Seed the config dir from the shipped defaults on first run" was a
fiction: it would be copying operator data, not defaults.

**Decision: no automatic seeding.** Moving to a shared location is a one-time, explicitly
requested migration, not something a first run does silently.

## Requirements this must satisfy

From `docs/cli-revamp-notes.md`, 2026-08-19, in the operator's order:

1. **Pick a task** - list the registered tasks, choose one, instead of knowing command names.
2. **Define or edit the custom loops** - Custom Routine creation and editing, which today exists
   only in the GUI.
3. **Tail the log while running** - a bounded "last N lines" view during a run.

## Shape

```
afkadb                -> menu, when stdin is a tty
afkadb help           -> the task listing, exit 0
afkadb <cmd|alias>    -> runs it, unchanged
```

```
Menu
 |- Run a task            -> the same call `afkadb <cmd>` makes
 |- Custom Routines       -> create, edit, reorder, delete
 |- Game Settings         -> AFKJourney.toml
 |- App settings          -> submenu: App.toml, ADB.toml   (separate root models)
 `- Quit
```

Not a tty means no menu: print the listing to stderr and exit non-zero. A menu in a cron job is a
hang, not a UI.

## The three things that will destroy operator data if done naively

### 1. Writing through a Pydantic model deletes keys

`TomlSettings.from_toml` validates with `extra="ignore"` (`toml_settings.py:32`), and `App.toml`
carries `notifications_enabled` (line 10) and `action_log_limit` (line 14) which **no model
declares** (`app_settings.py:54,72`). Serialising a model back to TOML drops them.

The editor edits the dict `tomllib` produced and writes that. The model validates and describes
fields; it is never the source of what is written.

Consequence, stated rather than discovered later: a key absent from the model is **preserved but
not shown**. Those two App.toml keys are invisible in the editor. The Python and Rust models have
also already drifted - Rust `UISettings` has `minimize_should_go_to_tray` (`settings.rs:245`) and
Python's does not (`app_settings.py:62`) - so "the editor shows every setting" is a claim this
design does NOT make.

### 2. "It loaded" is not validation

`from_toml` catches **every** exception, not just `ValidationError` (`toml_settings.py:28,41`), and
falls back to defaults. A save path relying on load to prove validity will write anything.
Validation calls `model_validate` directly and lets the error propagate.

### 3. Saves reformat the file

`tomli-w` does not preserve comments or layout, and the checked-in TOMLs use quoted table names and
multiline arrays that a rewrite would flatten. `TaskListSettings` compounds it: a
`field_validator` coerces legacy plain-string tasks into `{"name": ..., "repeat": true}`
(`my_custom_routine_settings.py:33-40`), so a file in the legacy form changes shape merely by being
loaded.

**The guarantee is therefore semantic, not byte-level:** every key that was present is still
present with the same value, and no key the operator did not edit changes value. Formatting may
change. That is the honest promise, and the tests assert exactly it - a byte-identical promise
would be untestable and false.

## Custom routines: what a task name may be

`TaskListSettings.tasks` validates nothing about names (`my_custom_routine_settings.py:22`); the
GUI offers choices from `CUSTOM_ROUTINE_REGISTRY` (`__main__.py:474`). The CLI editor offers the
same registry, so nothing NEW can be free text.

**But entries already present may not be in the registry.** `AFKJourney.toml:20` holds
`"Claim AFK Rewards"` and `"Equip new Equipment"`; neither is registered. The editor therefore:

- **preserves** unknown entries, never dropping them,
- **displays** them marked as unrecognised rather than hiding them,
- allows removing or reordering them, but not creating more.

Dropping them would silently break a routine the operator uses daily.

## The log tail

Task execution is synchronous (`execute.py:174`), so there is no second thread to render into. A
live-refreshing pane is **out of scope**.

What is specified: a deque handler modelled on `WarningContextFileHandler`
(`logging_setup.py:88-91`, which already uses a 200-record buffer) installed at the console
handler's level. It stores **records** and re-renders them, rather than capturing stdout, so the
tail is exactly what the console showed.

**Not after Ctrl+C.** `Execute.function` catches `KeyboardInterrupt` and calls `sys.exit(0)`
(`execute.py:277-281`), and the watchdog raises the same through `_thread.interrupt_main()`
(`:162`). Redisplaying a tail after an interrupt would mean changing that, which alters scripted
behaviour. The tail is offered after a run **completes**.

## The emulator check belongs to the task, not the front end

`afk-bot.sh:15-17` refuses to start Python unless the emulator is booted. That makes `afkadb help`
require a device it does not need, and a task chosen inside the menu skip the check entirely. The
check moves to immediately before a task runs, wherever it was chosen from. Settings editing never
requires a device.

## Caches must be invalidated after every save

The GUI runs each task in a fresh subprocess (`__main__.py:332-345`); the menu reuses one
interpreter. Game settings, `adb_settings()`, and the ADB client and controller are all cached per
profile (`tauri_context/cache.py`), and the CLI's profile key is `None`.

So editing `ADB.toml` in the menu and then running a task would use the OLD device. The GUI clears
`ADB_SETTINGS`, `ADB` and `GAME_SETTINGS` on save (`__main__.py:571-580`); the menu calls the same
clears after every write. This is the single largest source of stale state in a long-lived
process, and it is the residual risk this design carries.

## New dependency

`tomli-w` is **not** in this project - absent from `src-tauri/pyproject.toml` and `uv.lock`, and
`import tomli_w` fails in both venvs. Adding it is part of the work, chosen over `tomlkit` because
the settings files contain no comments.

## Success

- An SSH session with no display can pick and run any task, build a custom routine, change any
  scalar or enum-list setting, and read the tail of a run.
- The GUI and the CLI read and write the same files.
- Saving a file the operator did not edit changes no value in it.
- `afkadb c2` and every scripted invocation behave as they do today.

## Out of scope

- No TUI framework, no mouse, no live-refreshing pane.
- No profile switching in the CLI.
- Nothing outside the three TOMLs.
- Reconciling the Python and Rust settings models, which is a real but separate defect.
