"""Dry-run Mode C's draft logging over adb, with no install and no GUI.

Runs the same decisions the mixin makes - detect the draft with the draft_anchor
template, read `draft_pick` while it is up, read `prematch_pick` once it is gone, merge
the two - and prints the lines Mode C would log. Nothing is written to the database and
the device is never touched beyond a screencap.

    uv run python scripts/dry_run_draft_log.py [seconds]

Start it, then spectate a match. It exits after the timeout or once a locked line has
been printed.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adb_auto_player.games.afk_journey.services.solstice.config import (  # noqa: E402
    SolsticeConfig,
)
from adb_auto_player.games.afk_journey.services.solstice.draftlog import (  # noqa: E402
    PickRead,
    better,
    format_final,
    format_pick,
    newly_locked,
)
from adb_auto_player.games.afk_journey.services.solstice.icons import (  # noqa: E402
    IconLibrary,
)
from adb_auto_player.games.afk_journey.services.solstice.paths import (  # noqa: E402
    solstice_db_path,
    solstice_icon_dir,
)
from adb_auto_player.games.afk_journey.services.solstice.vision import (  # noqa: E402
    extract_cell,
    identify_cell,
)

TEMPLATES = (
    Path(__file__).resolve().parents[1]
    / "adb_auto_player/games/afk_journey/templates/event/solstice_clash"
)
# The app matches this template through its own framework; here it is a plain
# normalised correlation, so the threshold is stated rather than inherited.
DRAFT_ANCHOR_MIN = 0.90
POLL_SECONDS = 0.4
FRAME = Path("/tmp/aap-dry-run-frame.png")


def grab() -> "cv2.typing.MatLike | None":
    """One device frame. Via the filesystem: `exec-out` mixes stderr into the PNG."""
    subprocess.run(
        ["adb", "shell", "screencap", "-p", "/sdcard/_dry.png"],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["adb", "pull", "/sdcard/_dry.png", str(FRAME)],
        capture_output=True,
        check=False,
    )
    return cv2.imread(str(FRAME))


def anchor_score(frame, name: str) -> float:
    template = cv2.imread(str(TEMPLATES / name), cv2.IMREAD_GRAYSCALE)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if template is None or template.shape[0] > gray.shape[0]:
        return 0.0
    return float(cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED).max())


def read(frame, cell_type: str, cfg, library) -> list[PickRead]:
    out: list[PickRead] = []
    heroes = cfg.heroes()
    for cell in cfg.cells(cell_type):
        if cell.slot is None or cell.side is None:
            continue
        found = identify_cell(extract_cell(frame, cell), cell_type, library, cfg)
        hero = heroes.get(found.slug) if found.slug else None
        out.append(
            PickRead(
                slot=cell.slot,
                side=cell.side,
                cell_type=cell_type,
                slug=found.slug,
                name=hero.name if hero else found.slug,
                score=found.score,
                margin=found.margin,
            )
        )
    return out


def say(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {message}", flush=True)


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    cfg = SolsticeConfig.load(solstice_db_path())
    library = IconLibrary.build(cfg, solstice_icon_dir())
    say(f"library {len(library.entries())} entries; watching for {seconds:.0f}s")

    seen: dict[int, str] = {}
    draft_reads: list[PickRead] = []
    saw_draft = False
    slowest = 0.0
    deadline = time.monotonic() + seconds

    while time.monotonic() < deadline:
        frame = grab()
        if frame is None:
            time.sleep(POLL_SECONDS)
            continue

        started = time.perf_counter()
        score = anchor_score(frame, "draft_anchor.png")
        on_draft = score >= DRAFT_ANCHOR_MIN

        if on_draft:
            if not saw_draft:
                say(f"[SC-54] draft screen (anchor {score:.3f})")
                saw_draft = True
            draft_reads = read(frame, "draft_pick", cfg, library)
            for pick in newly_locked(seen, draft_reads):
                say(format_pick(pick))
            slowest = max(slowest, time.perf_counter() - started)
            time.sleep(POLL_SECONDS)
            continue

        if saw_draft:
            say(f"[SC-55] draft gone - slowest read {slowest * 1000:.0f}ms")
            # Wait for the locked screen to settle rather than reading the first frame
            # after the draft, which is usually a transition. Measured: prematch_anchor
            # scores only 0.377 on this screen with a plain correlation, so the reads
            # themselves are the signal - four of six identified means we are there.
            locked: list[PickRead] = []
            settle = time.monotonic() + 15.0
            while time.monotonic() < settle:
                candidate = read(frame, "prematch_pick", cfg, library)
                if sum(r.identified for r in candidate) >= 4:
                    locked = candidate
                    break
                locked = candidate if not locked else locked
                time.sleep(POLL_SECONDS)
                nxt = grab()
                if nxt is not None:
                    frame = nxt
            merged = {r.slot: r for r in draft_reads}
            for r in locked:
                merged[r.slot] = better(merged.get(r.slot), r)
            say("[SC-58] locked picks screen")
            say(format_final(list(merged.values())))
            recovered = sum(
                1
                for slot, r in merged.items()
                if r.identified
                and not any(x.slot == slot and x.identified for x in locked)
            )
            say(f"[SC-59] {sum(r.identified for r in merged.values())}/6 identified, "
                f"{recovered} recovered from the draft")
            return

        time.sleep(POLL_SECONDS)

    say("[SC-56] no draft screen seen")


if __name__ == "__main__":
    main()
