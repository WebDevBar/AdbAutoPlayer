"""The interactive menu.

Running a task from here is the SAME call `afkadb <command>` makes -
`Execute.find_command_and_execute` with the same task table. The menu only supplies
the name; it does not reimplement execution, and there is no menu-only code path a
scripted run would miss.

Three things differ from a one-shot CLI run and each is handled explicitly:

- **`SystemExit` must not take the menu down with the task.** A task exits 0 on
  Ctrl+C, but ALSO exits 1 on an unrecoverable error or a resolution mismatch. The
  code is inspected so a failure is reported as one rather than looking like success.
- **One interpreter serves many runs.** Caches that a fresh subprocess used to
  discard are cleared after every save, and `SummaryGenerator` - a singleton with no
  reset - is emptied between runs, or run two's summary includes run one's counts.
- **Ctrl+C at a prompt means "go back", not "quit the process".**
"""

import logging
import sys
from pathlib import Path

from adb_auto_player.file_loader import SettingsLoader
from adb_auto_player.models.decorators import CacheGroup
from adb_auto_player.registries import cache_clear
from adb_auto_player.task_loader import get_game_tasks
from adb_auto_player.util import Execute, SummaryGenerator

from . import log_tail, prompt, routine_editor, settings_editor

_GAME = "AFKJourney"

# Main menu entries, named so the dispatch below is not a run of magic indices.
_RUN_TASK, _ROUTINES, _GAME_SETTINGS, _APP_SETTINGS = range(4)
_TAIL_LINES = 40


def _reset_between_runs() -> None:
    """Undo the state a fresh subprocess used to discard for us."""
    SummaryGenerator().entries.clear()


def _clear_settings_caches() -> None:
    """Drop cached settings so the next task reads what was just saved."""
    for group in (
        CacheGroup.ADB_SETTINGS,
        CacheGroup.APP_SETTINGS,
        CacheGroup.GAME_SETTINGS,
        CacheGroup.ADB,
    ):
        cache_clear(group)


def _report_resolved_files(root: Path, profile_dir: Path) -> None:
    """Say which files were found and which were not.

    `from_toml` returns defaults for a missing file without complaining, so a wrong
    --app-config-dir otherwise looks exactly like a working one until a task behaves
    oddly. This is the only thing that makes it visible.
    """
    print(f"\nConfig root:  {root}")
    print(f"Profile dir:  {profile_dir}")
    expected = (
        (root, "App.toml"),
        (profile_dir, "ADB.toml"),
        (profile_dir, f"{_GAME}.toml"),
    )
    missing = [name for directory, name in expected if not (directory / name).is_file()]
    for directory, name in expected:
        mark = "ok     " if (directory / name).is_file() else "MISSING"
        print(f"  {mark} {directory / name}")
    if missing:
        print(
            f"  {len(missing)} file(s) missing - those settings fall back to defaults."
        )


def _run_task(command: str) -> None:
    """Run one task, surviving whatever it does on the way out."""
    _reset_between_runs()
    tail = log_tail.install()
    try:
        result = Execute.find_command_and_execute(command, get_game_tasks())
        if isinstance(result, BaseException):
            logging.error(result, exc_info=result)
    except SystemExit as exit_signal:
        # Ctrl+C during a task is sys.exit(0); an unrecoverable error and a
        # resolution mismatch are sys.exit(1). Catching without inspecting would
        # return to the menu as though a failed run had succeeded.
        code = exit_signal.code
        if code not in (0, None):
            print(f"\n  task exited with code {code}")
        else:
            print("\n  task interrupted")
    except KeyboardInterrupt:
        # A second Ctrl+C, landing in the summary print or the cleanup block, does
        # not go through the exit path above.
        print("\n  interrupted")
    finally:
        logging.getLogger().removeHandler(tail)

    if (
        tail.records
        and prompt.confirm(f"Show the last {_TAIL_LINES} log lines?") is True
    ):
        for line in tail.render(_TAIL_LINES):
            print(line)


def _choose_and_run() -> None:
    """Pick a task from the registry and run it."""
    tasks = get_game_tasks()
    names = sorted({cmd.name for commands in tasks.values() for cmd in commands})
    if not names:
        print("  no tasks registered")
        return
    index = prompt.choose("Run a task", names)
    if index is prompt.CANCELLED or index == -1:
        return
    _run_task(names[index])


def _app_settings_menu(root: Path, profile_dir: Path) -> None:
    """App.toml and ADB.toml are separate root models, so separate editors."""
    from adb_auto_player.models.pydantic import AdbSettings  # noqa: PLC0415
    from adb_auto_player.models.pydantic.app_settings import AppSettings  # noqa: PLC0415

    while True:
        index = prompt.choose(
            "App settings",
            [f"App.toml   ({root})", f"ADB.toml   ({profile_dir})"],
        )
        if index is prompt.CANCELLED or index == -1:
            return
        if index == 0:
            settings_editor.edit(root / "App.toml", AppSettings, _clear_settings_caches)
        else:
            settings_editor.edit(
                profile_dir / "ADB.toml", AdbSettings, _clear_settings_caches
            )


def run() -> int:
    """Show the menu until the operator quits.

    Returns:
        The process exit code.
    """
    if not sys.stdin.isatty():
        # stdin, never stdout: afk-bot.sh pipes stdout through tee, so gating on
        # stdout.isatty() would disable the menu exactly where it is wanted.
        print(
            "afkadb: no menu without a terminal. Run `afkadb help` for the task list.",
            file=sys.stderr,
        )
        return 2

    root, profile_dir = SettingsLoader.resolve_layout()
    _report_resolved_files(root, profile_dir)

    while True:
        index = prompt.choose(
            "AdbAutoPlayer",
            ["Run a task", "Custom Routines", "Game Settings", "App settings"],
            back="Quit",
        )
        if index is prompt.CANCELLED or index == -1:
            return 0

        if index == _RUN_TASK:
            _choose_and_run()
        elif index == _ROUTINES:
            routine_editor.edit(
                profile_dir / f"{_GAME}.toml", _GAME, _clear_settings_caches
            )
        elif index == _GAME_SETTINGS:
            from adb_auto_player.games.afk_journey.settings import Settings  # noqa: PLC0415

            settings_editor.edit(
                profile_dir / f"{_GAME}.toml", Settings, _clear_settings_caches
            )
        elif index == _APP_SETTINGS:
            _app_settings_menu(root, profile_dir)
