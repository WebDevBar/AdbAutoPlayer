"""Where the Solstice Clash database lives.

Two different files, and conflating them is the bug this module exists to avoid:

- The **bundled** database ships with the application. It carries the reference
  data every install needs - the hero roster, screen cell geometry, art
  transforms - and on a packaged install it sits in a read-only location.
- The **user** database is where a contributor's own collected matches go. It
  must be writable and must survive an upgrade that replaces the bundled one.

On first run the bundled file is copied to the user location and then migrated.
After that the bundled copy is only a fallback for a fresh install.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from adb_auto_player.util import RuntimeInfo

_RELATIVE = Path("solstice_clash") / "heroes.sqlite"


def user_data_dir() -> Path:
    """The per-user, writable application data directory.

    Hardcoding a developer's checkout path here meant the mode could only ever
    run on one machine - it failed immediately on anyone else's install, which
    is the whole point of shipping this to other people.
    """
    override = os.environ.get("ADB_SOLSTICE_DATA_DIR")
    if override:
        return Path(override).expanduser()

    if RuntimeInfo.is_windows():
        base = os.environ.get("APPDATA") or "~/AppData/Roaming"
    elif RuntimeInfo.is_mac():
        base = "~/Library/Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base).expanduser() / "AdbAutoPlayer"


def bundled_db() -> Path | None:
    """The read-only database shipped with the application, if we can find it.

    Checked in order: an explicit override, the packaged resource directory
    beside the executable, and the repo checkout for a development run.
    """
    candidates: list[Path] = []

    override = os.environ.get("ADB_SOLSTICE_BUNDLED_DB")
    if override:
        candidates.append(Path(override).expanduser())

    # Packaged: <install root>/data/solstice_clash/heroes.sqlite, with the python
    # package several levels below the install root.
    here = Path(__file__).resolve()
    for parents_up in (7, 8, 9):
        if len(here.parents) > parents_up:
            candidates.append(here.parents[parents_up] / "data" / _RELATIVE)

    # Development checkout.
    for parent in here.parents:
        if (parent / "data" / _RELATIVE).exists():
            candidates.append(parent / "data" / _RELATIVE)
            break

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def solstice_db_path() -> Path:
    """The writable database this install should use, seeded on first run.

    Seeding copies rather than symlinks: the user's collected matches must not
    live inside a package directory that an upgrade or an uninstall can remove.
    """
    target = user_data_dir() / _RELATIVE
    if target.is_file():
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    seed = bundled_db()
    if seed is not None:
        # copy, not move - the bundled file stays put for the next fresh install
        shutil.copy2(seed, target)
    return target
