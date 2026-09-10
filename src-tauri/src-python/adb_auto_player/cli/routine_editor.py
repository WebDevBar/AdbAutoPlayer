"""Edit the three custom routine slots.

There are exactly three, backed by `custom_routine_one/two/three` and three
registered commands. "Create" and "delete" mean fill and clear a slot; a fourth key
in the TOML is preserved by the round trip but can never be run.

Two rules that look like fussiness and are not:

- **An unrecognised task name is kept and marked, never dropped.** At run time it is
  skipped with `logging.error` (`game/_task_mixin.py`), so the routine is quietly
  shorter than it looks. An editor that silently removed it would hide that; one
  that shows it marked makes it fixable.
- **Duplicates are preserved on load but refused on add.** Tasks are keyed by name in
  a dict at run time, so a name listed twice runs once - the routine does not do what
  it displays. But the GUI adds unconditionally, so a GUI-saved file can already
  contain duplicates, and refusing to OPEN such a file would make it uneditable.
"""

from pathlib import Path
from typing import Any

from adb_auto_player.file_loader import settings_file
from adb_auto_player.registries import CUSTOM_ROUTINE_REGISTRY

from . import prompt

SLOT_KEYS = ("custom_routine_one", "custom_routine_two", "custom_routine_three")

# The TOML uses the models' aliases, not the field names.
_SLOT_ALIASES = {
    "custom_routine_one": "Custom Routine 1",
    "custom_routine_two": "Custom Routine 2",
    "custom_routine_three": "Custom Routine 3",
}
_TASKS_ALIAS = "Task List"
_NAME_ALIAS = "name"


def _known_task_names(game: str) -> list[str]:
    """Every task name the registry will accept for this game."""
    return sorted(CUSTOM_ROUTINE_REGISTRY.get(game, {}).keys())


def _task_name(entry: Any) -> str:
    """A task entry is either a plain string (legacy) or a table with a name."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get(_NAME_ALIAS, entry.get("Name", "")))
    return str(entry)


def _slot_table(data: dict[str, Any], slot: str) -> dict[str, Any]:
    """Get or create the table for one slot, honouring the alias in the file."""
    alias = _SLOT_ALIASES[slot]
    for key in (alias, slot):
        existing = data.get(key)
        if isinstance(existing, dict):
            return existing
    data[alias] = {}
    return data[alias]


def _tasks(table: dict[str, Any]) -> list[Any]:
    for key in (_TASKS_ALIAS, "tasks"):
        value = table.get(key)
        if isinstance(value, list):
            return value
    return []


def _set_tasks(table: dict[str, Any], tasks: list[Any]) -> None:
    key = _TASKS_ALIAS if _TASKS_ALIAS in table or "tasks" not in table else "tasks"
    table[key] = tasks


def _label(entry: Any, known: list[str], seen: set[str]) -> str:
    """Render one task, marking what would not run as displayed."""
    name = _task_name(entry)
    marks = []
    if name not in known:
        marks.append("UNKNOWN - skipped at run time")
    if name in seen:
        marks.append("DUPLICATE - runs once")
    if isinstance(entry, dict) and entry.get("repeat") is False:
        marks.append("no repeat")
    return f"{name}{'  <' + '; '.join(marks) + '>' if marks else ''}"


def _edit_slot(data: dict[str, Any], slot: str, game: str) -> bool:
    """Edit one slot's task list. Returns True if anything changed."""
    table = _slot_table(data, slot)
    tasks = list(_tasks(table))
    known = _known_task_names(game)
    changed = False

    while True:
        seen: set[str] = set()
        labels = []
        for entry in tasks:
            labels.append(_label(entry, known, seen))
            seen.add(_task_name(entry))

        actions = [*labels, "+ Add a task", "Clear the slot"]
        index = prompt.choose(
            f"{_SLOT_ALIASES[slot]} - {len(tasks)} task(s)", actions, back="Done"
        )
        if index is prompt.CANCELLED or index == -1:
            _set_tasks(table, tasks)
            return changed

        if index == len(actions) - 2:  # Add
            present = {_task_name(t) for t in tasks}
            available = [n for n in known if n not in present]
            if not available:
                print("  every registered task is already in this routine")
                continue
            choice = prompt.choose("Add which task?", available)
            if choice is prompt.CANCELLED or choice == -1:
                continue
            tasks.append({_NAME_ALIAS: available[choice], "repeat": True})
            changed = True
            continue

        if index == len(actions) - 1:  # Clear
            if prompt.confirm(f"Clear all {len(tasks)} task(s)?") is True:
                tasks = []
                changed = True
            continue

        changed = _edit_task(tasks, index) or changed


_REMOVE, _MOVE_UP, _MOVE_DOWN, _TOGGLE_REPEAT = range(4)


def _edit_task(tasks: list[Any], index: int) -> bool:
    """Act on one task in the list. Returns True if anything changed."""
    entry = tasks[index]
    name = _task_name(entry)
    repeat = entry.get("repeat", True) if isinstance(entry, dict) else True

    action = prompt.choose(
        name,
        ["Remove", "Move up", "Move down", f"Repeat: {repeat} - toggle"],
    )
    if action is prompt.CANCELLED or action == -1:
        return False

    if action == _REMOVE:
        tasks.pop(index)
    elif action == _MOVE_UP and index > 0:
        tasks[index - 1], tasks[index] = tasks[index], tasks[index - 1]
    elif action == _MOVE_DOWN and index < len(tasks) - 1:
        tasks[index + 1], tasks[index] = tasks[index], tasks[index + 1]
    elif action == _TOGGLE_REPEAT:
        # A legacy plain string has to become a table to carry `repeat`. That is a
        # shape change, but only for the entry the operator deliberately edited.
        tasks[index] = {_NAME_ALIAS: name, "repeat": not repeat}
    else:
        return False
    return True


def edit(path: Path, game: str, on_saved=None) -> None:
    """Run the routine editor against a game's settings TOML.

    Args:
        path: The game settings file, e.g. AFKJourney.toml.
        game: Registry key for the game, e.g. "AFKJourney".
        on_saved: Called after a successful write, to invalidate caches.
    """
    try:
        data = settings_file.read(path)
    except Exception as exc:
        print(f"  cannot read {path}: {exc}")
        return

    if not _known_task_names(game):
        print(f"  no custom routine tasks registered for {game}")
        return

    dirty = False
    while True:
        labels = []
        for slot in SLOT_KEYS:
            count = len(_tasks(_slot_table(data, slot)))
            labels.append(f"{_SLOT_ALIASES[slot]} - {count} task(s)")

        index = prompt.choose(
            f"Custom Routines{' *' if dirty else ''}",
            labels,
            back="Save and go back" if dirty else "Back",
        )
        if index is prompt.CANCELLED:
            index = -1

        if index == -1:
            if not dirty:
                return
            try:
                settings_file.write(path, data)
            except Exception as exc:
                print(f"  NOT saved - {exc}")
                return
            print(f"  saved {path}")
            if on_saved is not None:
                on_saved()
            return

        dirty = _edit_slot(data, SLOT_KEYS[index], game) or dirty
