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
import uuid
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "schema.sql")
SCHEMA_VERSION = 4

# Columns added after a table's original CREATE. schema.sql has them in the CREATE for
# fresh databases; these entries upgrade databases that predate them.
ADD_COLUMNS = [
    # Event/theme normalisation. The old free-text `match.theme` stays for
    # provenance; these carry the resolved identity.
    ("match", "event_id", "INTEGER REFERENCES event(id)"),
    ("match", "origin", "TEXT NOT NULL DEFAULT 'local'"),
    ("match", "contributor_uuid", "TEXT"),
    ("match", "remote_received_at", "TEXT"),
    ("match", "theme_resolved_by", "TEXT"),
    ("match", "pushed_at", "TEXT"),
    ("match", "theme_id", "INTEGER REFERENCES theme(id)"),
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

# Column renames for the 2026-07-27 blue/red -> left/right position rename. Guarded on
# both ends: only runs while the old name is still there and the new one is not, so a
# second run (or a fresh database that never had the old names) is a no-op.
RENAME_COLUMNS = [
    ("match", "blue_player", "left_player"),
    ("match", "blue_rating", "left_rating"),
    ("match", "blue_rank", "left_rank"),
    ("match", "red_player", "right_player"),
    ("match", "red_rating", "right_rating"),
    ("match", "red_rank", "right_rank"),
    ("match_odds", "blue_pool", "left_pool"),
    ("match_odds", "red_pool", "right_pool"),
    ("match_odds", "blue_odds", "left_odds"),
    ("match_odds", "red_odds", "right_odds"),
]

# Tables whose 'side' column used 'blue' | 'red' and now uses 'left' | 'right'. Checked
# for column presence first - match_pool has no side column at all.
SIDE_TABLES = ("cell_registry", "match_hero", "match_pool", "identification_audit")

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
    ("scale_summary_hero", "0.48,0.47,0.49,0.46,0.50",
     "measured: all six summary cards peak at 0.47-0.48"),
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

# Measured on summary_01.png at 1080x1920. Card centres: x=90, ally y=476/566/656,
# enemy y=1123/1215/1307. The brief's original bounds (centre +-52, matching the full
# card art+frame) never scored above ~0.35 against the plain hero icon at ANY scale -
# the icon has no card frame, crown or badge, so a crop that includes them cannot
# correlate with it regardless of scale. Re-measured by sweeping crop half-width x
# scale against summary_01's six known heroes: half=20 (this file) peaks at 0.86-0.93
# with a scale of 0.48, comfortably inside the accept_score/accept_margin gates.
# Bounds below are centre +-20, tight enough to exclude the frame entirely.
DEFAULT_SUMMARY_CELLS = [
    ("solstice_summary", "summary_left_1", "summary_hero", 70, 456, 110, 496, "left", 1),
    ("solstice_summary", "summary_left_2", "summary_hero", 70, 546, 110, 586, "left", 2),
    ("solstice_summary", "summary_left_3", "summary_hero", 70, 636, 110, 676, "left", 3),
    ("solstice_summary", "summary_right_1", "summary_hero", 70, 1103, 110, 1143, "right", 1),
    ("solstice_summary", "summary_right_2", "summary_hero", 70, 1195, 110, 1235, "right", 2),
    ("solstice_summary", "summary_right_3", "summary_hero", 70, 1287, 110, 1327, "right", 3),
]

# spectate_draft_picks: top strip, from live/match01/raw/000039317.png.
# Cards span y 400-530; the "Lvl 240" badge covers the bottom ~30px, so the art ends
# at 495. Centres x: 120/260/400 (blue) and 678/822/965 (red).
DEFAULT_DRAFT_PICK_CELLS = [
    ("spectate_draft_picks", "draft_pick_left_1",  "draft_pick",  75, 410, 165, 495, "left", 1),
    ("spectate_draft_picks", "draft_pick_left_4",  "draft_pick", 215, 410, 305, 495, "left", 4),
    ("spectate_draft_picks", "draft_pick_left_5",  "draft_pick", 355, 410, 445, 495, "left", 5),
    ("spectate_draft_picks", "draft_pick_right_2", "draft_pick", 633, 410, 723, 495, "right", 2),
    ("spectate_draft_picks", "draft_pick_right_3", "draft_pick", 777, 410, 867, 495, "right", 3),
    ("spectate_draft_picks", "draft_pick_right_6", "draft_pick", 920, 410, 1010, 495, "right", 6),
]

# spectate_prematch: from live/match01/raw/000104002.png. Cards span y 940-1120 with the
# level badge at the bottom, so the art is y 965-1085. Centres x: 132/270/405 (blue) and
# 677/810/945 (red).
DEFAULT_PREMATCH_CELLS = [
    # y0 is 1005, NOT 965. Measured on device 2026-07-26: the card spans y954-1125 and
    # the star crown occupies its top ~40px, so a window starting at 965 crops the crown
    # into the template and depresses the score. With 965 only 3 of 6 cells identified
    # (0.53-0.78); with 1005 all 6 do, at 0.94-0.99 with margins 0.36-0.50.
    ("spectate_prematch", "prematch_left_1",  "prematch_pick",  87, 1005, 177, 1085, "left", 1),
    ("spectate_prematch", "prematch_left_2",  "prematch_pick", 225, 1005, 315, 1085, "left", 2),
    ("spectate_prematch", "prematch_left_3",  "prematch_pick", 360, 1005, 450, 1085, "left", 3),
    ("spectate_prematch", "prematch_right_1", "prematch_pick", 632, 1005, 722, 1085, "right", 1),
    ("spectate_prematch", "prematch_right_2", "prematch_pick", 765, 1005, 855, 1085, "right", 2),
    ("spectate_prematch", "prematch_right_3", "prematch_pick", 900, 1005, 990, 1085, "right", 3),
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

    # 2026-07-27 blue/red -> left/right position rename. A match was already recorded
    # under the old names, so existing databases (including the shipped heroes.sqlite)
    # need both the columns and the stored values migrated, not just fresh installs.
    renamed = []
    for table, old_col, new_col in RENAME_COLUMNS:
        if (
            table_exists(con, table)
            and old_col in columns(con, table)
            and new_col not in columns(con, table)
        ):
            con.execute(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}")
            renamed.append(f"{table}.{old_col}->{new_col}")

    for table in SIDE_TABLES:
        if table_exists(con, table) and "side" in columns(con, table):
            con.execute(f"UPDATE {table} SET side='left' WHERE side='blue'")
            con.execute(f"UPDATE {table} SET side='right' WHERE side='red'")

    if table_exists(con, "match") and "outcome" in columns(con, "match"):
        con.execute("UPDATE match SET outcome='left' WHERE outcome='blue'")
        con.execute("UPDATE match SET outcome='right' WHERE outcome='red'")

    if table_exists(con, "cell_registry"):
        # REPLACE is a no-op on rows without the substring, so this is safe to run on
        # every migration, not just ones that still carry the old names.
        con.execute(
            "UPDATE cell_registry SET cell_name=REPLACE(cell_name,'_blue','_left')"
        )
        con.execute(
            "UPDATE cell_registry SET cell_name=REPLACE(cell_name,'_red','_right')"
        )

    for cells in (
        DEFAULT_SUMMARY_CELLS, DEFAULT_DRAFT_PICK_CELLS, DEFAULT_PREMATCH_CELLS,
    ):
        for screen, name, cell_type, x0, y0, x1, y1, side, slot in cells:
            con.execute(
                "INSERT OR IGNORE INTO cell_registry"
                "(screen,cell_name,cell_type,x0,y0,x1,y1,side,slot,base_resolution,"
                " verified_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,'1080x1920',datetime('now'))",
                (screen, name, cell_type, x0, y0, x1, y1, side, slot),
            )

    # Correct prematch cells seeded before the 2026-07-26 measurement. INSERT OR IGNORE
    # above cannot update an existing row, so databases carrying the old y0=965 need this.
    con.execute(
        "UPDATE cell_registry SET y0=1005 WHERE cell_type='prematch_pick' AND y0=965"
    )

    # The install table shipped briefly with sync state named last_sync_at /
    # sync_cursor. Push and pull need SEPARATE marks - a client collects while a
    # pull runs, so a pull cursor says nothing about what has been pushed - and
    # the old names invited exactly that conflation. Rename in place; ALTER TABLE
    # RENAME COLUMN is available in the SQLite we ship.
    if table_exists(con, "install"):
        install_cols = columns(con, "install")
        for old_col, new_col in (
            ("last_sync_at", "last_push_at"),
            ("sync_cursor", "pull_cursor"),
        ):
            if old_col in install_cols and new_col not in install_cols:
                con.execute(
                    f"ALTER TABLE install RENAME COLUMN {old_col} TO {new_col}"
                )
                renamed.append(f"install.{old_col}->{new_col}")

    # Created HERE, not in schema.sql: it references pushed_at, and schema.sql is
    # executed BEFORE ADD_COLUMNS adds that column to pre-existing databases.
    # A partial index on a not-yet-existing column aborts the whole script.
    if "pushed_at" in columns(con, "match"):
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_match_pushable ON match(pushed_at)"
            " WHERE origin='local' AND natural_key IS NOT NULL"
        )

    # This install's identity - generated once, then never touched again.
    # uuid4 is right here: it needs to be unique across machines that never talk
    # to each other, with no coordination and no central issuer.
    con.execute(
        "INSERT OR IGNORE INTO install(id,instance_uuid,created_at) VALUES(1,?,?)",
        (str(uuid.uuid4()), datetime.datetime.now(datetime.UTC).isoformat(
            timespec="seconds")),
    )

    # ---------------------------------------------------------------------
    # Events and themes.
    #
    # Deliberately DATA, not code: a new theme is a row, and adding one must not
    # need a release. Dates are the authority - `match.theme` is only what OCR
    # happened to read off the event screen, and a match filed under the wrong
    # theme is worse than one filed under none, because themes change the roster
    # balance and data is only comparable within one.
    # ---------------------------------------------------------------------
    con.execute(
        "INSERT OR IGNORE INTO event(slug,name,game) VALUES(?,?,?)",
        ("solstice-clash", "Solstice Clash", "afk-journey"),
    )
    event_id = con.execute(
        "SELECT id FROM event WHERE slug='solstice-clash'"
    ).fetchone()[0]

    # starts_at inclusive, ends_at EXCLUSIVE, UTC. Windows left NULL where the
    # boundary was never observed - an unknown boundary must not masquerade as a
    # known one. Fill them in as they are confirmed; that is the whole point of
    # keeping this in a table.
    themes = [
        # slug,               name,                starts_at, ends_at,    default
        ("unknown",           "Unknown / Default", None,      None,       1),
        ("fierce-duel",       "Fierce Duel",       None,      None,       0),
        ("converging-paths",  "Converging Paths",  None,      "2026-07-28T00:00:00Z", 0),
        ("flourishing-wilds", "Flourishing Wilds", None,      None,       0),
        ("tactical-grounds",  "Tactical Grounds",  None,      None,       0),
    ]
    for slug, name, starts, ends, is_default in themes:
        con.execute(
            "INSERT OR IGNORE INTO theme"
            "(event_id,slug,name,starts_at,ends_at,is_default) VALUES(?,?,?,?,?,?)",
            (event_id, slug, name, starts, ends, is_default),
        )

    # Backfill matches recorded before the tables existed. Matched on the raw OCR
    # name, which is all those rows have; from here on theme_id is set at capture.
    con.execute(
        "UPDATE match SET event_id=? WHERE event_id IS NULL", (event_id,)
    )
    con.execute(
        "UPDATE match SET theme_id=("
        "  SELECT t.id FROM theme t"
        "  WHERE t.event_id=? AND lower(t.name)=lower(match.theme)"
        ") WHERE theme_id IS NULL AND theme IS NOT NULL",
        (event_id,),
    )
    # Anything still unresolved goes to the event's default rather than staying
    # NULL, so "which theme is this?" always has an answer.
    con.execute(
        "UPDATE match SET theme_id=("
        "  SELECT id FROM theme WHERE event_id=? AND is_default=1"
        ") WHERE theme_id IS NULL",
        (event_id,),
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
    print(f"  columns renamed: {renamed or 'none'}")
    print(f"  config rows inserted: {inserted}")
    for t in ("hero", "hero_alias", "hero_skin", "hero_skill", "solstice_roster",
              "cell_registry", "art_transform", "library_config"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:16s} {n:>5} rows")


if __name__ == "__main__":
    main()
