#!/usr/bin/env python3
"""Strip per-machine data from the database that ships to contributors.

The bundled heroes.sqlite carries reference data every install needs - the hero
roster, screen cell geometry, art transforms, hero skills. None of that is
obtainable by syncing: the pooled API serves MATCHES ONLY, so the bundle stays
mandatory.

What must not travel is everything specific to the machine that built it:

- `install` - the instance UUID. migrate.py uses INSERT OR IGNORE against a
  CHECK(id = 1), so a shipped row is never replaced and EVERY contributor would
  claim the same identity, breaking attribution and per-install revocation.
- `match` / `match_hero` / `match_pool` / `match_odds` - collected matches. They
  ship as origin='local' with pushed_at NULL, so a fresh install would re-push
  someone else's 44 matches on its first sync. The pool already holds them and
  hands them back branded with the original contributor's UUID.
- `identification_audit` - per-machine identification evidence, and a directory
  leak: 548 of its rows held absolute paths under the developer's vault. It is
  also the confirmation source for the hero_screen_transform triggers, so
  shipping it lets one machine's evidence "confirm" another machine's geometry.
- `hero_screen_transform` - crop geometry tuned to one screen.

Run after any reference refresh (build_hero_db.py), before committing.
"""

import sqlite3
import sys
from pathlib import Path

# Emptied before shipping. Ordered children-first so foreign keys stay satisfied
# even with enforcement on.
PER_MACHINE_TABLES = (
    "match_hero",
    "match_pool",
    "match_odds",
    "hero_screen_transform",
    "identification_audit",
    "match",
    "install",
)


def strip(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON")

    print(f"stripping {db_path}")
    for table in PER_MACHINE_TABLES:
        before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.execute(f"DELETE FROM {table}")
        print(f"  {table:24s} {before:>5} -> 0")
    con.commit()

    # Reclaim the pages, so the shipped file does not carry deleted rows in its
    # freelist where anyone could read them back.
    con.execute("VACUUM")
    con.close()

    kept = sqlite3.connect(db_path)
    print("\n  reference data kept:")
    for table in ("hero", "hero_skin", "hero_skill", "hero_alias",
                  "solstice_roster", "cell_registry", "art_transform",
                  "library_config", "screen", "event", "theme"):
        n = kept.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table:20s} {n:>5}")
    print("\n  integrity:", kept.execute("PRAGMA integrity_check").fetchone()[0])


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parent / "heroes.sqlite"
    )
    strip(target)
