# One CLI, one settings layer, shared with the GUI - design

Written 2026-09-09, revision 7. Supersedes the withdrawn v1/v2 implementation plans, which were
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
`main_cli.py:99` for the CLI. Nothing needs to change here, and nothing should.

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
`Game.app_settings` swallows every exception (`game/game.py:75-76`) and `from_toml` returns defaults
for a missing file, this failed in total silence.

**Its effect today was nil, and saying otherwise would be an overclaim:** `src-tauri/settings/App.toml`
has no `[advanced]` section at all - only `profiles`, `ui` and `logging` - so `template_timeout`,
`action_delay` and `navigation_delay` were on defaults before the fix and remain so. The fix
matters because adding `[advanced]` would otherwise have had no effect and given no clue why.

**Already fixed, separately from this design:** `SettingsLoader.app_settings_path()` checks the
profile directory first and the parent second, serving both layouts. Both readers use it -
`game/game.py:74` and `util/execute.py:83`. There were two readers, not one; the earlier draft
named only `execute.py`, and `game.py` is the one that governs every tap and template wait.

## The resolver has two outputs, and must handle a layout with only one level

Two things are needed:

- **Config root** - where `App.toml` lives.
- **Profile directory** - `<root>/<profile>/` for `ADB.toml`, the game TOMLs, and `data/`.

**But the CLI's directory is flat.** `afk-bot.sh:42` passes `src-tauri/settings`, which holds all
three TOMLs side by side and has no `0/` subdirectory. Applying the two-level rule literally would
look for `src-tauri/settings/0/ADB.toml`, find nothing, and `from_toml` would return defaults
silently (`toml_settings.py:24-26`) - reopening for ADB and the game settings exactly the failure
class the App.toml fix just closed.

**Detection rule, stated so an implementer cannot get it wrong:**

```
given DIR (--app-config-dir, else main_cli.py's derived <repo>/src-tauri/settings):
    if (DIR / "App.toml").is_file():          # flat: everything lives here
        root = DIR;         profile_dir = DIR
    elif (DIR.parent / "App.toml").is_file(): # two-level: DIR is the profile dir
        root = DIR.parent;  profile_dir = DIR
    else:                                      # neither file exists yet
        root = DIR;         profile_dir = DIR  # flat, because the CLI never creates
                                               # the two-level layout
```

**The third branch is the one an earlier draft got wrong.** A two-output rule with only two
branches is a READ rule pressed into service as a WRITE rule: a flat directory whose `App.toml`
has not been created yet would be classed two-level, and the App-settings editor would then
create `src-tauri/App.toml` one level too high and misdetect that directory forever after. A GUI
profile directory before the GUI's first save has neither file either (`settings.rs:380-381` loads
defaults without writing).

**Accepted consequence of the third branch:** pointed at an unsaved GUI profile dir, the CLI
classes it flat and would write `<config>/<idx>/App.toml` while the GUI writes `<config>/App.toml`,
after which branch 1 wins for the CLI permanently. That is accepted because CLI profile switching
is out of scope and the CLI is never pointed at a GUI profile dir today.

**The editor writes `App.toml` wherever the resolver resolved `root`** - never at a path derived
independently. One resolution, used for both reading and writing.

**And the rule alone does not close the silent-default failure class.** `from_toml` still returns
defaults without complaint for a missing `ADB.toml` or game TOML in every layout
(`toml_settings.py:24-26`). So the menu **reports loudly at startup** which of the three files it
resolved and which are missing. That, not the branch count, is what makes a wrong directory
visible.

The flat/two-level test itself is the one `SettingsLoader.app_settings_path()` already makes
(`settings_loader.py:55-59`), generalised to return both values. In the flat case **root and
profile directory are the same directory**, and `active_profile` is ignored because there is
nowhere for a second profile to live.

**`--app-config-dir` therefore means "the directory the settings are in"**, which is what both
callers already pass. It does not change meaning.

## The repo TOMLs are upstream-tracked, and the CLI writes into them

**An earlier draft called these files the operator's tuned values, restored by `cf464b56`. That
was wrong, and the error came from reading the archived repo.** `git cat-file -t cf464b56` in
`WebDevBar/AdbAutoPlayer` returns `Not a valid object name`; that commit exists only in
`todor-wdb/adb-ap`, which `afk-bot.sh` stopped pointing at on 2026-09-06.

What is actually in the repo the CLI runs from:

| File | State vs `upstream/main` |
|---|---|
| `src-tauri/settings/App.toml` | byte-identical, upstream-tracked |
| `src-tauri/settings/ADB.toml` | byte-identical, upstream-tracked |
| `src-tauri/settings/AFKJourney.toml` | tracked, +7 lines: 3 auto-bet keys (`8158f0c2`), 4 WDB Modes keys (`cc2920d8`) |

So the operator's restored settings - the DAILY AFK LOOP and DAILY SIDE PUSH routines, `Days to
Scan = 3` - are in the archived checkout and **have not been what `afkadb` runs since 2026-09-06**.

Nothing in the GUI reads `src-tauri/settings/` - no reference in any `.rs` file, only
`main_cli.py:65-66` in Python. GUI defaults are **model defaults** (`toml_settings.py:24-26`
returns `cls()` when the file is absent). So there is still nothing to seed FROM, and the
no-seeding decision stands - but for this reason, not the old one.

**The consequence this design must own: the editor writes into git-tracked files that upstream
also changes.** Every `merge upstream/main` becomes a conflict or a silent overwrite of operator
edits, and the working tree is permanently dirty. This is the strongest argument for relocating
the CLI's config outside the repo - the very migration this spec defers.

**Open decision, owner's call:** migrate the config location as part of this work, or keep writing
into the repo and accept the merge friction. The rest of the design is unaffected either way,
because the resolver takes the directory as input.

## Requirements this must satisfy

From `docs/cli-revamp-notes.md`, 2026-08-19, in the operator's order:

1. **Pick a task** - list the registered tasks, choose one, instead of knowing command names.
2. **Define or edit the custom loops** - Custom Routine creation and editing, which today exists
   only in the GUI.
3. **Tail the log while running** - a bounded "last N lines" view during a run.

The same notes record that the CLI was **never proven to work end to end without the GUI**
(`cli-revamp-notes.md:32-33`). The-drey-setup's changelog shows runs did happen, but against the
wrong repository. **An end-to-end SSH run with no display is therefore a deliverable of this work,
not an assumption behind it.**

## Shape

```
afkadb                -> menu, when STDIN is a tty
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

**The gate tests stdin only, never stdout.** `afk-bot.sh:45` pipes stdout through `tee`, so
`sys.stdout.isatty()` is false on every wrapper run and gating on it would disable the menu
exactly where it is wanted.

**Both new forms require argparse changes.** `command` is today a REQUIRED positional constrained
by `choices=` (`cli/argparse_helper.py:35-55`), so bare `afkadb` and `afkadb help` are both rejected
by argparse before `main_cli.py:52` ever runs. The positional becomes optional with a default, and
`help` is added as an accepted value.

## The three things that will destroy operator data if done naively

### 1. Writing through a Pydantic model deletes keys

`TomlSettings.from_toml` validates with `extra="ignore"` (`toml_settings.py:34`), and `App.toml`
carries `notifications_enabled` (line 10) and `action_log_limit` (line 14) which **no model
declares in any language** - not Python (`app_settings.py:54,62-72`), not Rust, not the TypeScript
client, not Svelte. The Rust save path drops them too (`settings.rs:427-429`), so preserving them
is a guarantee the CLI makes and the GUI does not. Serialising a model back to TOML drops them.

The editor edits the dict `tomllib` produced and writes that. The model validates and describes
fields; it is never the source of what is written.

Consequence, stated rather than discovered later: a key absent from the model is **preserved but
not shown**. Those two App.toml keys are invisible in the editor. The Python and Rust models have
also already drifted - Rust `UISettings` has `minimize_should_go_to_tray` (`settings.rs:261`) and
Python's does not (`app_settings.py:62`) - so "the editor shows every setting" is a claim this
design does NOT make.

### 2. "It loaded" is not validation

`from_toml` catches **every** exception, not just `ValidationError` (`toml_settings.py:28-41`), and
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
same registry.

**An earlier draft claimed `AFKJourney.toml:20` contained two unregistered names. That was wrong** -
`"Claim AFK Rewards"` and `"Equip new Equipment"` are both registered, passing their label
POSITIONALLY (`custom_routine/claim_afk_rewards.py:13`, `equip_new_equipment.py:47`), which a grep
for `label=` misses.

The editor still preserves and marks names it does not recognise, but for the real reason: an
unrecognised task is skipped with `logging.error` at run time (`_task_mixin.py:41-43`), so a
routine can be quietly shorter than it looks, and an editor that silently dropped such entries
would hide that rather than surface it.

**Duplicates: preserve on load, warn, refuse to add.** `_task_mixin.py:38-46` keys tasks by name
in a dict, so the same task listed twice runs once - a routine that does not do what it displays.
But the GUI adds tasks unconditionally (`src/lib/form/components/TaskList.svelte:152-155,208-209`, with no filtering of the
choices list at `:280`), so a GUI-saved file can legitimately contain duplicates. An editor that
rejected such a file on load would be unopenable. It therefore loads it, marks the duplicate, and
refuses to add another.

**There are exactly three routines, and they are fixed slots.** `custom_routine_one/two/three`
(`settings.py:535-548`) back three registered commands. "Create" and "delete" mean fill and clear a
slot; there is no fourth to create. A fourth key in the TOML is preserved by the round-trip but can
never be run.

## The log tail

Task execution is synchronous (`execute.py:175-176`), so there is no second thread to render into. A
live-refreshing pane is **out of scope**.

What is specified: a deque handler modelled on `WarningContextFileHandler`
(`logging_setup.py:88-91`, which already uses a 200-record buffer) installed at the console
handler's level. It stores **records** and re-renders them, rather than capturing stdout, so the
tail is exactly what the console showed.

**Not after Ctrl+C.** `Execute.function` catches `KeyboardInterrupt` and calls `sys.exit(0)`
(`execute.py:251,278-282`), and the watchdog raises the same through `_thread.interrupt_main()`
(`:163`). Redisplaying a tail after an interrupt would mean changing that, which alters scripted
behaviour. The tail is offered after a run **completes**.

## The emulator check belongs to the task, not the front end

`afk-bot.sh:16-17` refuses to start Python unless the emulator is booted. That makes `afkadb help`
require a device it does not need, and a task chosen inside the menu skip the check entirely. The
check moves to immediately before a task runs, wherever it was chosen from. Settings editing never
requires a device.

**Decision: drop the pre-check rather than port it.** `AdbController` already fails on a missing
device, so a Python-side probe duplicates it. If a friendlier message is wanted later, the probe is
`AdbClientHelper.resolve_adb_device()` (`adb_client.py:49`) - not a new implementation.

**An earlier draft claimed a device-id mismatch between the script and `ADB.toml`. That was wrong.**
`afk-start.sh:25` launches with `-port 5554`, so `emulator-5554` and `127.0.0.1:5555` are the same
device (`afk-start.sh:97` says so outright). There is nothing to reconcile.

## Caches must be invalidated after every save

The GUI runs each task in a fresh subprocess (`__main__.py:332-345`); the menu reuses one
interpreter. Game settings, `adb_settings()`, and the ADB client and controller are cached per
profile (`tauri_context/cache.py`), with the CLI's profile key `None` - which clears all profiles
(`cache.py:53`), harmless here.

So editing `ADB.toml` and then running a task would use the OLD device. The GUI clears
`ADB_SETTINGS`, `ADB` and `GAME_SETTINGS` on save (`__main__.py:571-580`).

**Obstacle 1: `_cache_clear` is defined in `__main__.py:421`, which imports `pytauri`.** The CLI
cannot import that module. The function must MOVE to sit beside `CACHE_REGISTRY`
(`registries/registries.py:17`), with `__main__.py` importing it from there. Nothing else imports
it, and `registries.py` already imports `CacheGroup`, so the move is contained.

**Obstacle 2, and the more serious one: clearing `ADB_SETTINGS` is a no-op today.** The decorators
on `SettingsLoader.adb_settings()` are INVERTED relative to every other cached reader:

```python
# settings_loader.py:99-111 - wrong way round
@staticmethod
@profile_aware_cache(maxsize=1)          # outer: this is what holds the cache
def adb_settings():
    @register_cache(CacheGroup.ADB_SETTINGS)   # inner: a bare closure
    def _load(): ...
    return _load()

# the fix - note @staticmethod STAYS outermost
@staticmethod
@register_cache(CacheGroup.ADB_SETTINGS)
@profile_aware_cache(maxsize=1)
def adb_settings(): ...
```

**`@staticmethod` must remain the outermost decorator.** A `staticmethod` object does not proxy
attributes - `hasattr(staticmethod(f), "cache_clear")` is `False` - so hoisting `register_cache`
above it would register the wrapper object and re-create the same no-op in a new shape.
`register_cache` goes directly beneath `@staticmethod`, above `@profile_aware_cache`. The
already-correct example is `games/afk_journey/base.py:86-88`, where `@property` plays that role.

`register_cache` registers the inner `_load` closure, which has no `cache_clear` attribute, so
`_cache_clear`'s `getattr(func, "cache_clear", None)` (`__main__.py:427`) is `None` and the loop
does nothing. Verified by running it: after `_cache_clear(ADB_SETTINGS)` the reader still returned
the first value; calling the outer wrapper's own `cache_clear` returned the new one.
`app_settings()` (`:113-128`) has the same inversion.

Clearing the `ADB` group does not rescue it either - `get_adb_client` (`adb_client.py:27`) and
`_resolve_device` (`:135`) rebuild from `SettingsLoader.adb_settings()`, which stays stale. **So
"edit ADB.toml, then run a task" would use the old device even after the relocation.** The GUI has
never hit this because every task is a fresh subprocess (`__main__.py:332-345`).

**Fixing the decorator order is therefore part of this work, and ships first** - it is a standalone
bug fix with a regression test, independent of the menu.

**But swapping the decorators alone does not import.** `register_cache` currently reaches
`settings_loader` through a lazy in-function import (`:103`, `# noqa: PLC0415`). Applying it at
class-definition time needs a module-level import, and that closes a cycle:

```
decorators/__init__ -> register_command.py:18 -> util/__init__
                    -> execute.py:27 -> file_loader -> settings_loader -> decorators
```

**Resolution: move `register_cache` into `registries/`, beside `CACHE_REGISTRY`, and re-export it
from `decorators` for compatibility.** It already imports nothing but `models.decorators` and
`CACHE_REGISTRY` (`decorators/register_lru_cache.py:1-4`), so it satisfies the rule
`registries/__init__.py` states for itself - depend on `models` only. `settings_loader` then
imports from `registries`, which has no path back.

**Verified, not assumed.** The move was prototyped and imported from all three entry points -
`adb_auto_player.decorators`, `adb_auto_player.main_cli`, and `settings_loader` directly - and in
each the registry held `('SettingsLoader.adb_settings', True)` with `cache_clear` present, where
today it holds the closure with no such attribute. The prototype was then reverted.

This pairs naturally with obstacle 1: `_cache_clear` and `register_cache` end up in the same
module, which is where both belong.

Three notes so nobody trips on them:

- `SettingsLoader.app_settings()` (`settings_loader.py:113-128`, cache group `APP_SETTINGS`) reads
  a fourth file, `AdbAutoPlayer.toml`, not `App.toml`. Only `commands/debug.py:60` uses it.
- `Game.app_settings` is a `cached_property` (`game/game.py:70`) and is safe ONLY because
  `Execute.function` builds a fresh instance per run (`execute.py:238`). If the menu ever reuses a
  Game instance, that cache goes stale too. `GAME_SETTINGS` is keyed on `self` with `maxsize=1`
  (`base.py:86-91`), so a fresh instance per run already re-reads it - no clear needed.
- **`SummaryGenerator` is a singleton with persistent `entries` and no reset**
  (`util/summary_generator.py:9-31`). In one reused interpreter, run 2's summary would include
  run 1's counts. The menu must reset it between runs. Note the summary is printed only on Ctrl+C
  (`execute.py:279`), not on normal completion.

## New dependency

`tomli-w` is **not** in this project - absent from `src-tauri/pyproject.toml` and `uv.lock`, and
`import tomli_w` fails in the repo's single venv (`.venv` at the root). Adding it is part of the work, chosen over `tomlkit` because
the settings files contain no comments.

## What this delivers, and what it does not

Delivered:

- An SSH session with no display can pick and run any task, build a custom routine, and change any
  scalar or enum-list setting.
- Saving a file the operator did not edit changes no value in it.
- `afkadb c2` and every scripted invocation behave as they do today.

**Not delivered, stated plainly rather than implied:**

- **The GUI and the CLI do NOT end up reading the same files.** Migration is deferred (see above),
  and no GUI config dir exists on this machine. After this work the CLI still reads
  `src-tauri/settings/`. Unifying the location is a separate, deliberate migration.
- **Requirement 3 is only partly met.** `docs/cli-revamp-notes.md` asks to "view the last N lines
  DURING a run". Execution is synchronous (`execute.py:175-176`), so what is delivered is a replay
  AFTER a run. Since `--output terminal` already streams live, the replay adds little; the live
  bounded view is **deferred**, not solved.
- **The editor does not show every setting.** Keys absent from the Python models are preserved but
  invisible, and the Python and Rust models have drifted (`settings.rs:261` has
  `minimize_should_go_to_tray`, `app_settings.py:62` does not).
- **`afkadb c2` and every scripted invocation behave as they do today in outcome, not in timing.**
  Dropping the emulator pre-check moves the failure from the wrapper script to `AdbController`, so
  a run with no emulator fails later and with a different message.

## Cross-repo changes this requires

The menu cannot work without changes in `todor-wdb/the-drey-setup`, and they are part of the work:

- `machines/server/bin/afk-bot.sh:45` substitutes `--help` when given no arguments
  (`"${@:---help}"`). While that stands, `afkadb` can never reach a menu.
- `afk-bot.sh:16-17` refuses to start unless `emulator-5554` has booted. That check is dropped, per
  the section above.
- `afk-bot.sh:28` names the log file `-${1:-help}.log`, so a menu session would be saved as
  `...-help.log`. It needs a name for the no-argument case.
- The same line 45 pipes stdout through `tee >(sed ...)`. **The menu must stay line-oriented** -
  any cursor movement or redraw garbles the saved log.

## Ctrl+C must not kill the menu

`Execute.function` catches `KeyboardInterrupt` and calls `sys.exit(0)` (`execute.py:251,278-282`), and
the watchdog raises the same through `_thread.interrupt_main()` (`:163`). That `SystemExit`
propagates through `find_command_and_execute` and would take the whole menu down with the task.

**Decision: the menu loop catches `SystemExit` around a task run, inspects `.code`, and returns to
the menu.** Catching it blindly is not enough - `sys.exit(1)` is also raised for an unrecoverable
task error (`_task_mixin.py:114`) and for a device resolution mismatch
(`_screenshot_mixin.py:140`), and a bare catch would drop the operator back at the menu as if the
task had succeeded. A non-zero code is printed as a failure before returning.

**`KeyboardInterrupt` needs its own handling, in two places the exit path does not cover:** a
second Ctrl+C during the summary print (`execute.py:279-281`) or during the `finally` block
(`:315-320`) arrives as a raw `KeyboardInterrupt`, as does Ctrl+C at the menu's own `input()`. Both
are caught: the first returns to the menu, the second is treated as Quit.

This changes nothing for `afkadb <cmd>`, where the exit is still the process exiting.

## Out of scope

- No TUI framework, no mouse, no live-refreshing pane.
- No profile switching in the CLI.
- Nothing outside the three TOMLs.
- Reconciling the Python and Rust settings models, which is a real but separate defect.

## Implementation order

Each step is independently testable, and the first two are bug fixes that stand on their own.

1. **Move `register_cache` and `_cache_clear` into `registries/registries.py`**, re-exporting
   `register_cache` from `decorators`. This breaks the import cycle and must land before step 2.
2. **Decorator order in `settings_loader.py`** - `adb_settings()` and `app_settings()`, with a
   module-level `register_cache` import, plus a regression test asserting
   `_cache_clear(ADB_SETTINGS)` actually re-reads the file.
3. **Two-output resolver** with tests for all four inputs: flat, two-level, neither, nonexistent.
4. **`tomli-w` plus the round-trip module** - dict in, `model_validate` for validation, dict out -
   with semantic-preservation tests covering legacy string tasks and keys no model declares.
5. **argparse** - optional positional, `help` value, tty gate, stderr listing when not a tty.
6. **Menu loop** - the run path, `SystemExit` code inspection, `KeyboardInterrupt` handling,
   `SummaryGenerator` reset, cache clear after every save, startup report of resolved files.
7. **Scalar and enum-list editors** for the three TOMLs.
8. **Custom-routine slot editor** - registry choices, preserve-and-mark unknown names, refuse to
   add a duplicate, reorder within a slot.
9. **Deque log-tail handler**, offered after a completed run.
10. **`todor-wdb/the-drey-setup`** - `afk-bot.sh` default argument, drop the emulator pre-check,
    name the no-argument log.
11. **End-to-end SSH proof run**, which is requirement zero from the notes.
