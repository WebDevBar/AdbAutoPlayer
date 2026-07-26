#!/usr/bin/env python3
"""Apply the schema to heroes.sqlite. Idempotent and non-destructive.

    python3 migrate.py [path/to/heroes.sqlite]

Creates any missing tables/columns/indexes and records the version. Running it on an
up-to-date database is a no-op. It never drops or rewrites existing rows.

Why this exists: the schema grew by ad-hoc ALTER TABLE while we were measuring things,
so a fresh checkout and a working database could disagree. This makes the schema
declarative (schema.sql) and the upgrade path repeatable.
"""

from __future__ import annotations

import datetime
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema.sql")
SCHEMA_VERSION = 3

# Columns added after a table's original CREATE. schema.sql has them in the CREATE for
# fresh databases; these entries upgrade databases that predate them.
ADD_COLUMNS = [
    ("hero", "external_id", "INTEGER"),
    ("hero", "game_icon", "TEXT"),
    ("hero", "wiki_icon", "TEXT"),
    ("hero", "icon_w", "INTEGER"),
    ("hero", "icon_h", "INTEGER"),
    ("hero", "last_seen", "TEXT"),
    ("hero_skin", "game_icon", "TEXT"),
    ("hero_skin", "wiki_icon", "TEXT"),
    ("hero_skin", "icon_w", "INTEGER"),
    ("hero_skin", "icon_h", "INTEGER"),
    ("hero_skin", "last_seen", "TEXT"),
    ("solstice_roster", "phys_def_pct", "INTEGER"),
    ("solstice_roster", "magic_def_pct", "INTEGER"),
    ("solstice_roster", "atk_spd_pct", "INTEGER"),
    ("match_hero", "stat_sword", "INTEGER"),
    ("match_hero", "stat_heart", "INTEGER"),
    ("match_hero", "stat_shield", "INTEGER"),
    ("match_hero", "power", "INTEGER"),
    ("match_hero", "identified_by", "TEXT"),
]

# Measured defaults. Only inserted if absent - never overwrites tuned values.
DEFAULT_CONFIG = [
    ("icon_source", "game", "decoded from files/data/ui/icon (AST/LZ4/ASTC 6x6)"),
    ("icon_priority", "game,wiki", "game_icon is PRIMARY; wiki_icon is fallback only"),
    ("gamma", "0.5556", "exponent 1/1.8; best on labelled ADB cells (median 0.9550->0.9718)"),
    ("astc_block", "6", "every observed file is ASTC 6x6"),
    ("accept_score", "0.70", "minimum top score to accept an identification"),
    ("accept_margin", "0.10", "minimum margin over runner-up; this catches the real errors"),
    ("scale_chain", "1.01,0.95,1.08", "locked_pick / draft_locked_pick scale fallback chain"),
    ("scale_draft_card", "1.19,1.10,1.30", "draft_card scale fallback chain"),
]

# Screens Mode A reads. crop_* are the screen-level defaults a per-hero transform may
# override. Summary values are measured; the two spectate screens are seeded without a
# crop until Task 6 measures them.
DEFAULT_SCREENS = [
    ("solstice_summary", "Post-match summary: both comps, winner, per-hero stats",
     "1080x1920", 26, 18, 30),
    ("spectate_draft_picks", "Spectate draft: the six pick slots in the top strip",
     "1080x1920", None, None, None),
    ("spectate_prematch", "Spectate prematch: six locked cards, three per side",
     "1080x1920", None, None, None),
]


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "heroes.sqlite")
    fresh = not os.path.exists(db)
    con = sqlite3.connect(db)

    con.executescript(open(SCHEMA).read())          # CREATE IF NOT EXISTS throughout

    added = []
    for table, col, decl in ADD_COLUMNS:
        if table_exists(con, table) and col not in columns(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            added.append(f"{table}.{col}")

    inserted = 0
    for key, value, note in DEFAULT_CONFIG:
        cur = con.execute(
            "INSERT OR IGNORE INTO library_config(key,value,note) VALUES(?,?,?)",
            (key, value, note),
        )
        inserted += cur.rowcount

    for slug, description, base_resolution, half_w, top, bottom in DEFAULT_SCREENS:
        con.execute(
            "INSERT OR IGNORE INTO screen"
            "(slug,description,base_resolution,crop_half_w,crop_top,crop_bottom)"
            " VALUES(?,?,?,?,?,?)",
            (slug, description, base_resolution, half_w, top, bottom),
        )

    now = datetime.datetime.now().isoformat(timespec="seconds")
    con.execute(
        "INSERT OR IGNORE INTO schema_version(version,applied_at,note) VALUES(?,?,?)",
        (SCHEMA_VERSION, now, "initial declarative schema"),
    )
    con.commit()

    print(f"  database    : {db}{' (created)' if fresh else ''}")
    print(f"  schema      : v{SCHEMA_VERSION}")
    print(f"  columns added: {added or 'none'}")
    print(f"  config rows inserted: {inserted}")
    for t in ("hero", "hero_alias", "hero_skin", "hero_skill", "solstice_roster",
              "cell_registry", "art_transform", "library_config"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:16s} {n:>5} rows")


if __name__ == "__main__":
    main()
