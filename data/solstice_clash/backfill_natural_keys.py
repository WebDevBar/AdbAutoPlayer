#!/usr/bin/env python3
"""Backfill natural_key for matches recorded before the key was wired up.

Every component is already stored, so no data has to be recovered - but a match
is only keyed when it is COMPLETE (three identified heroes a side, decisive
outcome). Incomplete rows keep NULL and are simply never pushed.

Idempotent: rows that already have a key are skipped.
"""

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src-tauri" / "src-python"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "matchkey",
    HERE.parent.parent
    / "src-tauri/src-python/adb_auto_player/games/afk_journey/services/solstice/matchkey.py",
)
_mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mk)


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else str(HERE / "heroes.sqlite")
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")

    rows = con.execute(
        "SELECT id, captured_at, outcome FROM match"
        " WHERE natural_key IS NULL AND outcome IN ('left','right')"
    ).fetchall()

    keyed = skipped = 0
    for match_id, captured_at, outcome in rows:
        heroes = con.execute(
            "SELECT side, hero_slug, status FROM match_hero WHERE match_id=?",
            (match_id,),
        ).fetchall()
        if any(st != "identified" or not slug for _, slug, st in heroes):
            skipped += 1
            continue
        left = [slug for side, slug, _ in heroes if side == "left"]
        right = [slug for side, slug, _ in heroes if side == "right"]
        if not _mk.is_complete(left, right, outcome):
            skipped += 1
            continue
        con.execute(
            "UPDATE match SET natural_key=? WHERE id=?",
            (_mk.natural_key(outcome, left, right, captured_at), match_id),
        )
        keyed += 1

    con.commit()
    total, with_key = con.execute(
        "SELECT COUNT(*), COUNT(natural_key) FROM match"
    ).fetchone()
    print(f"keyed {keyed}, skipped {skipped} (incomplete)")
    print(f"matches: {total}, with a key: {with_key}")


if __name__ == "__main__":
    main()
