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
import hashlib
import json
import os
import sqlite3
import uuid
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# The three pure functions, from the sibling module beside this file. NOT imported from
# `adb_auto_player` - this script runs standalone from the repo root and is loaded by
# path outside the package in the shipped build, so the package is not importable here.
sys.path.insert(0, HERE)
from canon_rows import canonical_trios, map_side_pair, trio_index_for  # noqa: E402

SCHEMA = os.path.join(HERE, "schema.sql")
VIEWS = os.path.join(HERE, "views.sql")
SCHEMA_VERSION = 6

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
    ("match", "push_rejected_reason", "TEXT"),
    ("match", "theme_id", "INTEGER REFERENCES theme(id)"),
    # The prediction made BEFORE the fight, kept so it can be scored afterwards. The
    # point is not the number but the disagreements: a match we called at 80% and lost
    # is the only evidence that says where the logic is wrong. Without recording it at
    # the time, that question can never be asked - the fitted model changes as data
    # arrives, so it cannot be reconstructed later.
    # `predicted_left` is DELIBERATELY absent. This list exists to upgrade databases
    # that predate a column, so leaving it here would re-add it EMPTY on the launch
    # after the reshape drops it - a schema that looks current with the value gone.
    ("match", "predicted_trio_1", "REAL"),
    ("match", "predicted_source", "TEXT"),
    ("match", "predicted_locked", "INTEGER"),
    ("match", "predicted_at", "TEXT"),
    # Canonical identity. `natural_key` still carries the OUTCOME, so the same fight
    # seen from both sides keys differently and the pool stores it twice. `comps_key`
    # is outcome-free, so the two spectators agree - but it is deliberately NOT
    # UNIQUE: a mirrored local pair must be allowed to coexist until the server
    # reconciles them, and a UNIQUE constraint here would make the client throw away
    # the very row the reconciliation needs.
    ("match", "comps_key", "TEXT"),
    # Set when the server retires this row in favour of another. Non-NULL means
    # "superseded" - the row is kept for provenance and excluded from analysis.
    ("match", "superseded_by", "INTEGER"),
    # Capture-time bounds of every occurrence merged into this row. A single capture
    # keeps min == max; a merge widens them, and that widened window is what later
    # occurrence matching reads.
    ("match", "captures_min_at", "TEXT"),
    ("match", "captures_max_at", "TEXT"),
    # Supersession rides a SEPARATE server sequence from `pull_cursor`: retiring a row
    # does not advance its seq, so one cursor cannot track both. Sharing them would
    # either re-read page one forever or silently miss later retirements once
    # tombstones paginate.
    ("install", "supersession_cursor", "TEXT"),
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
    # Schema v6. Added before the reshape classifies anything, and `canonical_state`
    # is the one column the "not done yet" predicate depends on.
    ("match", "winning_trio", "INTEGER"),
    ("match", "blue_trio", "INTEGER"),
    ("match", "trio_1_rating", "INTEGER"),
    ("match", "trio_2_rating", "INTEGER"),
    ("match", "trio_1_rank", "INTEGER"),
    ("match", "trio_2_rank", "INTEGER"),
    ("match", "canonical_state", "TEXT"),
    ("match_hero", "trio", "INTEGER"),
    ("match_odds", "trio_1_pool", "INTEGER"),
    ("match_odds", "trio_2_pool", "INTEGER"),
    ("match_odds", "trio_1_odds", "REAL"),
    ("match_odds", "trio_2_odds", "REAL"),
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
    (
        "gamma",
        "0.5556",
        "exponent 1/1.8; best on labelled ADB cells (median 0.9550->0.9718)",
    ),
    ("astc_block", "6", "every observed file is ASTC 6x6"),
    ("accept_score", "0.70", "minimum top score to accept an identification"),
    (
        "accept_margin",
        "0.10",
        "minimum margin over runner-up; this catches the real errors",
    ),
    (
        "scale_chain",
        "1.01,0.95,1.08",
        "locked_pick / draft_locked_pick scale fallback chain",
    ),
    ("scale_draft_card", "1.19,1.10,1.30", "draft_card scale fallback chain"),
    (
        "scale_summary_hero",
        "0.48,0.47,0.49,0.46,0.50",
        "measured: all six summary cards peak at 0.47-0.48",
    ),
]

# Screens Mode A reads. crop_* are the screen-level defaults a per-hero transform may
# override. Summary values are measured; the two spectate screens are seeded without a
# crop until Task 6 measures them.
DEFAULT_SCREENS = [
    (
        "solstice_summary",
        "Post-match summary: both comps, winner, per-hero stats",
        "1080x1920",
        26,
        18,
        30,
    ),
    (
        "spectate_draft_picks",
        "Spectate draft: the six pick slots in the top strip",
        "1080x1920",
        None,
        None,
        None,
    ),
    (
        "spectate_prematch",
        "Spectate prematch: six locked cards, three per side",
        "1080x1920",
        None,
        None,
        None,
    ),
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
    (
        "solstice_summary",
        "summary_left_1",
        "summary_hero",
        70,
        456,
        110,
        496,
        "left",
        1,
    ),
    (
        "solstice_summary",
        "summary_left_2",
        "summary_hero",
        70,
        546,
        110,
        586,
        "left",
        2,
    ),
    (
        "solstice_summary",
        "summary_left_3",
        "summary_hero",
        70,
        636,
        110,
        676,
        "left",
        3,
    ),
    (
        "solstice_summary",
        "summary_right_1",
        "summary_hero",
        70,
        1103,
        110,
        1143,
        "right",
        1,
    ),
    (
        "solstice_summary",
        "summary_right_2",
        "summary_hero",
        70,
        1195,
        110,
        1235,
        "right",
        2,
    ),
    (
        "solstice_summary",
        "summary_right_3",
        "summary_hero",
        70,
        1287,
        110,
        1327,
        "right",
        3,
    ),
]

# spectate_draft_picks: top strip, from live/match01/raw/000039317.png.
# Cards span y 400-530; the "Lvl 240" badge covers the bottom ~30px, so the art ends
# at 495. Centres x: 120/260/400 (blue) and 678/822/965 (red).
DEFAULT_DRAFT_PICK_CELLS = [
    (
        "spectate_draft_picks",
        "draft_pick_left_1",
        "draft_pick",
        75,
        410,
        165,
        495,
        "left",
        1,
    ),
    (
        "spectate_draft_picks",
        "draft_pick_left_4",
        "draft_pick",
        215,
        410,
        305,
        495,
        "left",
        4,
    ),
    (
        "spectate_draft_picks",
        "draft_pick_left_5",
        "draft_pick",
        355,
        410,
        445,
        495,
        "left",
        5,
    ),
    (
        "spectate_draft_picks",
        "draft_pick_right_2",
        "draft_pick",
        633,
        410,
        723,
        495,
        "right",
        2,
    ),
    (
        "spectate_draft_picks",
        "draft_pick_right_3",
        "draft_pick",
        777,
        410,
        867,
        495,
        "right",
        3,
    ),
    (
        "spectate_draft_picks",
        "draft_pick_right_6",
        "draft_pick",
        920,
        410,
        1010,
        495,
        "right",
        6,
    ),
]

# spectate_prematch: from live/match01/raw/000104002.png. Cards span y 940-1120 with the
# level badge at the bottom, so the art is y 965-1085. Centres x: 132/270/405 (blue) and
# 677/810/945 (red).
DEFAULT_PREMATCH_CELLS = [
    # y0 is 1005, NOT 965. Measured on device 2026-07-26: the card spans y954-1125 and
    # the star crown occupies its top ~40px, so a window starting at 965 crops the crown
    # into the template and depresses the score. With 965 only 3 of 6 cells identified
    # (0.53-0.78); with 1005 all 6 do, at 0.94-0.99 with margins 0.36-0.50.
    (
        "spectate_prematch",
        "prematch_left_1",
        "prematch_pick",
        87,
        1005,
        177,
        1085,
        "left",
        1,
    ),
    (
        "spectate_prematch",
        "prematch_left_2",
        "prematch_pick",
        225,
        1005,
        315,
        1085,
        "left",
        2,
    ),
    (
        "spectate_prematch",
        "prematch_left_3",
        "prematch_pick",
        360,
        1005,
        450,
        1085,
        "left",
        3,
    ),
    (
        "spectate_prematch",
        "prematch_right_1",
        "prematch_pick",
        632,
        1005,
        722,
        1085,
        "right",
        1,
    ),
    (
        "spectate_prematch",
        "prematch_right_2",
        "prematch_pick",
        765,
        1005,
        855,
        1085,
        "right",
        2,
    ),
    (
        "spectate_prematch",
        "prematch_right_3",
        "prematch_pick",
        900,
        1005,
        990,
        1085,
        "right",
        3,
    ),
]


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def apply(db: str, quiet: bool = False) -> dict:
    """Bring `db` up to the current schema. Idempotent; never drops or rewrites rows.

    Callable as well as runnable, because THE CLIENT NEEDS IT. A shipped build never
    executes this file as a script, and `solstice_db_path` returns an existing user
    database untouched - so before this, a database kept whatever schema it was seeded
    with, forever. A contributor who installed before `match.predicted_left` existed had
    every single match fail at the write with "no such column", and updating the app did
    not help, because nothing ever added the column. Observed in the wild, 2026-07-28.

    Returns a summary of what changed, so a caller can log it without parsing stdout.
    """
    fresh = not os.path.exists(db)
    con = sqlite3.connect(db)
    try:
        return _apply(con, db, fresh, quiet)
    finally:
        # Closing matters even when this raises. A half-applied migration that keeps a
        # write lock makes the NEXT statement fail with "database is locked", which
        # sends the reader hunting for a concurrency bug that does not exist.
        con.close()


def _backfill_comps_key(con: sqlite3.Connection) -> int:
    """Give every already-recorded match the identity the new code looks it up by.

    NOT cosmetic. `comps_key` arrived NULL on every existing row, and two things now key
    off it: the push gate (`comps_key IS NOT NULL`), and the SC-41 backstop, which asks
    `match_by_comps_key` whether a match is already recorded. With every row NULL the
    backstop recognises NOTHING, so an upgraded install would re-record matches it
    already has - manufacturing the exact duplicates this whole change exists to remove.

    Only complete matches get a key, matching `is_complete`: three identified heroes a
    side and a decided outcome. The event slug comes from `event_id`, falling back
    through `theme_id`, and a row that resolves to neither is left NULL rather than
    guessed - 120 of 1200 rows on the operator's database reach it via the fallback.

    Idempotent: it only touches rows where `comps_key IS NULL`.

    Args:
        con: Open connection, mid-migration.

    Returns:
        How many rows were given a key.
    """
    if not table_exists(con, "match") or "comps_key" not in columns(con, "match"):
        return 0
    # SHAPE-AWARE, and it must WORK on both shapes rather than skipping one. Skipping
    # the canonical shape would leave every pulled row keyless forever - which is
    # precisely the regression this backfill exists to prevent, reintroduced one
    # schema later.
    legacy = "side" in columns(con, "match_hero")
    if legacy:
        group_a, group_b = "side = 'left'", "side = 'right'"
        decided = "m.outcome IN ('left','right')"
    else:
        group_a, group_b = "trio = 1", "trio = 2"
        decided = "m.winning_trio IS NOT NULL"

    rows = con.execute(
        f"""
        SELECT m.id,
               COALESCE(e1.slug, e2.slug) AS event_slug,
               (SELECT group_concat(hero_slug) FROM (
                    SELECT hero_slug FROM match_hero
                    WHERE match_id = m.id AND {group_a} AND hero_slug IS NOT NULL
                    ORDER BY hero_slug)) AS left_slugs,
               (SELECT group_concat(hero_slug) FROM (
                    SELECT hero_slug FROM match_hero
                    WHERE match_id = m.id AND {group_b} AND hero_slug IS NOT NULL
                    ORDER BY hero_slug)) AS right_slugs
        FROM match m
        LEFT JOIN event e1 ON e1.id = m.event_id
        LEFT JOIN theme t  ON t.id  = m.theme_id
        LEFT JOIN event e2 ON e2.id = t.event_id
        WHERE m.comps_key IS NULL AND {decided}
        """
    ).fetchall()

    done = 0
    for match_id, event_slug, left_slugs, right_slugs in rows:
        if not event_slug or not left_slugs or not right_slugs:
            continue
        left = left_slugs.split(",")
        right = right_slugs.split(",")
        if len(left) != 3 or len(right) != 3:
            continue
        # Kept in step with matchkey.comps_key. Duplicated rather than imported because
        # this file is loaded standalone by the client, outside the package.
        a, b = sorted([",".join(sorted(left)), ",".join(sorted(right))])
        digest = hashlib.sha256(f"{event_slug}|{a}|{b}".encode()).hexdigest()
        con.execute(
            "UPDATE match SET comps_key=? WHERE id=?", (f"sha256:{digest}", match_id)
        )
        done += 1
    return done


def _load_orientation_sidecar() -> dict:
    """The committed audit verdicts, keyed by comps_key.

    Keyed by comps_key rather than natural_key because the key survives everything:
    it is orientation-free and outcome-free by construction.

    Returns:
        {comps_key: verdict}, or {} when the sidecar is not bundled.
    """
    path = os.path.join(HERE, "trio-orientation-by-comps-key.json")
    if not os.path.isfile(path):
        # REFUSE, never fall back. Returning {} here would make every legacy match
        # look unaudited, permanently NULL its ratings, ranks, prediction and odds,
        # and then DROP the columns those came from - silent, irreversible data loss
        # on nothing worse than a packaging slip. A loud failure is recoverable; this
        # would not be.
        raise RuntimeError(
            f"the orientation sidecar is missing from {HERE}. The reshape is "
            "irreversible and cannot run without it - it would treat every audited "
            "row as unaudited and discard its draft-relative values for good."
        )
    with open(path) as handle:
        payload = json.load(handle)
    out = {}
    for key, entries in (payload.get("verdicts") or {}).items():
        # One entry per key in practice - the generator reported 0 keys with multiple
        # occurrences - so a single verdict is unambiguous. More than one and we
        # decline rather than pick: ambiguity is exactly where a wrong guess binds one
        # match's ratings to another's trios.
        if len(entries) == 1:
            out[key] = entries[0]["verdict"]
    return out


def _reshape_to_trios(con: sqlite3.Connection) -> int:
    """Repair the mirrored rows AND reshape onto trios, in ONE step.

    These cannot be sequenced by asking. `_ensure_schema` runs on every launch, so the
    contributor's build reshapes the moment it starts - and a reshape that ran first
    would derive `blue_trio` from a legacy `left` that is WRONG for exactly the 76 rows
    the audit identified, irreversibly. So the migration consumes the audit itself.

    The rule, which review got wrong twice:

    - The WINNER needs no verdict. Heroes and outcome are read from the same panel pair
      in the same pass, so "the trio in this panel won" survives a swap intact.
    - Only `blue_trio` and the DRAFT-relative group - ratings, ranks, prediction, odds,
      read from the header and the draft - need one, and they invert together.
    - No verdict means NULL. We do not guess: 8% of audited rows were mirrored.

    Args:
        con: Open connection, mid-migration.

    Returns:
        How many rows were classified.
    """
    if not table_exists(con, "match") or "canonical_state" not in columns(con, "match"):
        return 0
    legacy = "side" in columns(con, "match_hero")
    pending = con.execute(
        "SELECT COUNT(*) FROM match WHERE canonical_state IS NULL"
    ).fetchone()[0]

    if not legacy:
        # Already the canonical shape, but a NULL here is NOT automatically fine.
        # record_match, record_heroes and finalise_summary commit separately, so a
        # crash between them leaves a row on the canonical schema with no heroes, no
        # pointers, or both. Blessing those as 'canonical' would let a half-written
        # match into the fit and the pool - so each one is CLASSIFIED on its merits,
        # exactly as a legacy row is.
        if pending:
            _classify_canonical_leftovers(con)
        return 0

    # The SHAPE change is NOT conditional on there being rows to convert. An empty or
    # already-classified legacy database has nothing pending, and an early return here
    # would leave it on `side` and UNIQUE(match_id, side, slot) forever - which is
    # exactly what the committed seed database did, and every ON CONFLICT against the
    # new target then failed. Row conversion is skipped when there is none; the rebuild
    # and the drops always run.

    verdicts = _load_orientation_sidecar()

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS legacy_side_snapshot(
          match_id       INTEGER PRIMARY KEY,
          sides_json     TEXT,
          outcome        TEXT,
          predicted_left REAL,
          left_rating    INTEGER, right_rating INTEGER,
          left_rank      INTEGER, right_rank   INTEGER,
          odds_json      TEXT,
          captured_at    TEXT
        )
        """
    )

    match_cols = columns(con, "match")
    odds_cols = columns(con, "match_odds") if table_exists(con, "match_odds") else set()

    def col(name, default="NULL"):
        # Guarded per column: a database old enough to predate `predicted_left`
        # exists - the collaborator's did, and it failed with "no such column".
        return f"m.{name}" if name in match_cols else default

    rows = con.execute(
        f"""
        SELECT m.id, m.comps_key, {col("outcome")}, {col("predicted_left")},
               {col("left_rating")}, {col("right_rating")},
               {col("left_rank")}, {col("right_rank")}
          FROM match m
        """
    ).fetchall()

    heroes: dict[int, list[tuple]] = {}
    for match_id, side, slot, slug in con.execute(
        "SELECT match_id, side, slot, hero_slug FROM match_hero"
    ):
        heroes.setdefault(match_id, []).append((side, slot, slug))

    odds: dict[int, list[dict]] = {}
    if {"left_pool", "right_pool"} <= odds_cols:
        for row in con.execute(
            "SELECT match_id, sampled_at, left_pool, right_pool, left_odds, right_odds"
            "  FROM match_odds"
        ):
            odds.setdefault(row[0], []).append(
                {
                    "sampled_at": row[1],
                    "left_pool": row[2],
                    "right_pool": row[3],
                    "left_odds": row[4],
                    "right_odds": row[5],
                }
            )

    classified = 0
    for (
        match_id,
        comps,
        outcome,
        predicted_left,
        left_rating,
        right_rating,
        left_rank,
        right_rank,
    ) in rows:
        mine = heroes.get(match_id, [])
        con.execute(
            "INSERT OR REPLACE INTO legacy_side_snapshot(match_id, sides_json, outcome,"
            " predicted_left, left_rating, right_rating, left_rank, right_rank,"
            " odds_json, captured_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,(SELECT captured_at FROM match WHERE id=?))",
            (
                match_id,
                json.dumps(
                    [{"side": s, "slot": sl, "hero_slug": g} for s, sl, g in mine]
                ),
                outcome,
                predicted_left,
                left_rating,
                right_rating,
                left_rank,
                right_rank,
                json.dumps(odds.get(match_id, [])),
                match_id,
            ),
        )

        by_side = {"left": [], "right": []}
        for side, _slot, slug in mine:
            if slug and side in by_side:
                by_side[side].append(slug)

        try:
            trio_1, _trio_2 = canonical_trios(by_side)
        except ValueError:
            # Cannot form two complete trios. TERMINAL, not pending - which is what
            # stops the predicate looping on it forever.
            con.execute(
                "UPDATE match SET canonical_state='unrepresentable' WHERE id=?",
                (match_id,),
            )
            classified += 1
            continue

        left_is = trio_index_for("left", trio_1, by_side)
        right_is = 2 if left_is == 1 else 1

        winning_trio = None
        if outcome == "left":
            winning_trio = left_is
        elif outcome == "right":
            winning_trio = right_is

        verdict = verdicts.get(comps)
        if verdict == "mirrored":
            blue_trio = right_is
        elif verdict == "agree":
            blue_trio = left_is
        else:
            blue_trio = None

        if blue_trio is None:
            t1_rating = t2_rating = t1_rank = t2_rank = predicted = None
        else:
            t1_rating, t2_rating = map_side_pair(left_rating, right_rating, blue_trio)
            t1_rank, t2_rank = map_side_pair(left_rank, right_rank, blue_trio)
            predicted = predicted_left
            if predicted is not None and blue_trio == 2:
                predicted = 1.0 - predicted

        con.execute(
            "UPDATE match SET winning_trio=?, blue_trio=?, trio_1_rating=?,"
            " trio_2_rating=?, trio_1_rank=?, trio_2_rank=?, predicted_trio_1=?,"
            " canonical_state='canonical' WHERE id=?",
            (
                winning_trio,
                blue_trio,
                t1_rating,
                t2_rating,
                t1_rank,
                t2_rank,
                predicted,
                match_id,
            ),
        )
        for side, slot, slug in mine:
            con.execute(
                "UPDATE match_hero SET trio=? WHERE match_id=? AND side=? AND slot=?",
                (1 if slug in trio_1 else 2, match_id, side, slot),
            )
        if blue_trio is not None:
            for sample in odds.get(match_id, []):
                p1, p2 = map_side_pair(
                    sample["left_pool"], sample["right_pool"], blue_trio
                )
                o1, o2 = map_side_pair(
                    sample["left_odds"], sample["right_odds"], blue_trio
                )
                con.execute(
                    "UPDATE match_odds SET trio_1_pool=?, trio_2_pool=?,"
                    " trio_1_odds=?, trio_2_odds=? WHERE match_id=? AND sampled_at=?",
                    (p1, p2, o1, o2, match_id, sample["sampled_at"]),
                )
        classified += 1

    left_null = con.execute(
        "SELECT COUNT(*) FROM match WHERE canonical_state IS NULL"
    ).fetchone()[0]
    if left_null:
        raise RuntimeError(
            f"{left_null} match rows left unclassified; a partially reshaped database "
            "is worse than an unmigrated one"
        )

    bad = con.execute(
        """
        SELECT COUNT(*) FROM (
          SELECT match_id,
                 MIN(CASE WHEN trio = 1 THEN hero_slug END) AS a,
                 MIN(CASE WHEN trio = 2 THEN hero_slug END) AS b
            FROM match_hero WHERE hero_slug IS NOT NULL GROUP BY match_id
        ) t WHERE t.a IS NOT NULL AND t.b IS NOT NULL AND t.a > t.b
        """
    ).fetchone()[0]
    if bad:
        raise RuntimeError(f"{bad} matches are not canonically ordered")

    _drop_legacy_columns(con)
    return classified


def _drop_legacy_columns(con: sqlite3.Connection) -> None:
    """Remove the columns the trio shape replaced.

    SQLite 3.35+ supports ALTER TABLE DROP COLUMN, which is what the bundled runtime
    has. Each drop is guarded, so a re-run is a no-op rather than an error.

    Args:
        con: Open connection, mid-migration.
    """
    # Indexes AND VIEWS that reference a dropped column BLOCK the drop - SQLite
    # validates every remaining definition afterwards and aborts on the first that no
    # longer resolves. `hero_matchup` is rebuilt from views.sql at the end of _apply,
    # against the finished shape.
    for index in ("idx_match_outcome", "idx_match_hero_side"):
        con.execute(f"DROP INDEX IF EXISTS {index}")
    con.execute("DROP VIEW IF EXISTS hero_matchup")

    _rebuild_match_hero(con)

    for table, names in (
        (
            "match",
            (
                "outcome",
                "predicted_left",
                "left_rating",
                "right_rating",
                "left_rank",
                "right_rank",
            ),
        ),
        # match_hero is NOT here - `side` is part of UNIQUE(match_id, side, slot), and
        # SQLite refuses to drop a constrained column. It gets a full table rebuild.
        ("match_odds", ("left_pool", "right_pool", "left_odds", "right_odds")),
    ):
        if not table_exists(con, table):
            continue
        present = columns(con, table)
        for name in names:
            if name in present:
                con.execute(f"ALTER TABLE {table} DROP COLUMN {name}")


def _reconcile_local_synced_pairs(con: sqlite3.Connection) -> int:
    """Retire synced copies of matches this install also watched itself.

    Four local/synced pairs share a comps_key with neither marked superseded, so three
    of them are counted TWICE in the fit. They arose from pulling a match another
    contributor had pushed and then spectating it ourselves; the SC-41 backstop stops
    new ones, but nothing reconciles those already on disk.

    The +/-2 minute window is the test, NOT comps_key alone. Ids 1 and 45 share a key
    and are 31.6 hours apart - a genuine rematch, correctly two rows - and a
    reconciliation that ignored the window would destroy it.

    The LOCAL row survives, because it is draft-anchored: it is the one that knows
    which trio was ours.

    Args:
        con: Open connection, mid-migration.

    Returns:
        How many rows were retired.
    """
    if not table_exists(con, "match") or "comps_key" not in columns(con, "match"):
        return 0
    if "superseded_by" not in columns(con, "match"):
        return 0

    groups: dict[str, list[tuple]] = {}
    for row in con.execute(
        "SELECT id, comps_key, captured_at, origin FROM match"
        " WHERE comps_key IS NOT NULL AND superseded_by IS NULL"
    ):
        groups.setdefault(row[1], []).append(row)

    retired = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        locals_ = [m for m in members if m[3] == "local"]
        synced = [m for m in members if m[3] == "synced"]
        if not locals_ or not synced:
            continue
        for keeper in locals_:
            at = _parse_iso(keeper[2])
            if at is None:
                continue
            for other in synced:
                other_at = _parse_iso(other[2])
                if other_at is None:
                    continue
                if abs((other_at - at).total_seconds()) > _PAIR_WINDOW_SECONDS:
                    continue
                cur = con.execute(
                    "UPDATE match SET superseded_by=?"
                    " WHERE id=? AND superseded_by IS NULL",
                    (keeper[0], other[0]),
                )
                retired += cur.rowcount
    return retired


def _parse_iso(value):
    """A stored timestamp, or None when it cannot be read.

    Args:
        value: The stored `captured_at`.

    Returns:
        An aware datetime, or None.
    """
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed


# The same proximity rule the occurrence clustering uses. A duplicate capture of one
# match lands inside it; a rematch does not.
_PAIR_WINDOW_SECONDS = 120


def _classify_canonical_leftovers(con: sqlite3.Connection) -> int:
    """Settle rows left unclassified on a database that is already canonical.

    These come from an INTERRUPTED recording: `record_match`, `record_heroes` and
    `finalise_summary` commit separately, so a crash between them leaves a row with
    `canonical_state IS NULL` and possibly no heroes and no pointers at all.

    Each is judged on its merits rather than waved through: two complete trios, in
    canonical order, with every non-NULL pointer naming a trio that exists. Anything
    else is `unrepresentable` - terminal, excluded from the fit and from sync, and
    honest about what it is.

    Args:
        con: Open connection, mid-migration.

    Returns:
        How many rows were classified.
    """
    rows = con.execute(
        "SELECT id, winning_trio, blue_trio FROM match WHERE canonical_state IS NULL"
    ).fetchall()
    if not rows:
        return 0

    grouped: dict[int, dict[int, list[str]]] = {}
    for match_id, trio, slug in con.execute(
        "SELECT match_id, trio, hero_slug FROM match_hero WHERE hero_slug IS NOT NULL"
    ):
        grouped.setdefault(match_id, {1: [], 2: []}).setdefault(trio, []).append(slug)

    for match_id, winning_trio, blue_trio in rows:
        trios = grouped.get(match_id, {1: [], 2: []})
        first, second = sorted(trios.get(1, [])), sorted(trios.get(2, []))
        ok = (
            len(first) == _TRIO_SIZE
            and len(second) == _TRIO_SIZE
            and not (set(first) & set(second))
            and first < second
        )
        if ok:
            for pointer in (winning_trio, blue_trio):
                if pointer is not None and pointer not in (1, 2):
                    ok = False
        state = "canonical" if ok else "unrepresentable"
        con.execute("UPDATE match SET canonical_state=? WHERE id=?", (state, match_id))
    return len(rows)


# Every Solstice Clash composition is exactly three heroes.
_TRIO_SIZE = 3


def _rebuild_match_hero(con: sqlite3.Connection) -> None:
    """Rewrite match_hero into the trio shape.

    A rebuild rather than a DROP COLUMN, because `side` is part of
    UNIQUE(match_id, side, slot) and SQLite refuses to drop a constrained column. The
    new table carries the full constraint set: trio and slot domains, one slot per
    trio, and one appearance per hero per MATCH - the last being what stops two
    identical trios, which would make both pointers meaningless.

    Args:
        con: Open connection, mid-migration.
    """
    if "side" not in columns(con, "match_hero"):
        return
    con.execute(
        """
        CREATE TABLE match_hero_new(
          id            INTEGER PRIMARY KEY,
          match_id      INTEGER NOT NULL REFERENCES match(id) ON DELETE CASCADE,
          trio          INTEGER NOT NULL CHECK(trio IN (1,2)),
          slot          INTEGER NOT NULL CHECK(slot IN (1,2,3)),
          hero_slug     TEXT REFERENCES hero(slug),
          art_ref       TEXT,
          status        TEXT NOT NULL,
          score         REAL,
          margin        REAL,
          cell_type       TEXT,
          cell_name       TEXT,
          candidate_scope TEXT,
          pool_miss       INTEGER,
          runner_up_slug  TEXT,
          runner_up_score REAL,
          crop_path       TEXT,
          frame_path      TEXT,
          stat_sword      INTEGER,
          stat_heart      INTEGER,
          stat_shield     INTEGER,
          power           INTEGER,
          identified_by   TEXT,
          UNIQUE(match_id, trio, slot),
          UNIQUE(match_id, hero_slug)
        )
        """
    )
    # Rows the reshape could not classify have no trio and cannot enter the canonical
    # shape. Their evidence is already in legacy_side_snapshot.
    con.execute(
        """
        INSERT INTO match_hero_new(id, match_id, trio, slot, hero_slug, art_ref, status,
          score, margin, cell_type, cell_name, candidate_scope, pool_miss,
          runner_up_slug, runner_up_score, crop_path, frame_path, stat_sword,
          stat_heart, stat_shield, power, identified_by)
        SELECT id, match_id, trio, slot, hero_slug, art_ref, status,
               score, margin, cell_type, cell_name, candidate_scope, pool_miss,
               runner_up_slug, runner_up_score, crop_path, frame_path, stat_sword,
               stat_heart, stat_shield, power, identified_by
          FROM match_hero
         WHERE trio IN (1,2) AND slot IN (1,2,3)
        """
    )
    con.execute("DROP TABLE match_hero")
    con.execute("ALTER TABLE match_hero_new RENAME TO match_hero")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_match_hero_match ON match_hero(match_id)"
    )


def _apply(con: sqlite3.Connection, db: str, fresh: bool, quiet: bool) -> dict:
    con.executescript(open(SCHEMA).read())  # CREATE IF NOT EXISTS throughout
    # views.sql is executed at the END of this function, AFTER _reshape_to_trios.
    # `hero_matchup` reads `winning_trio` and `match_hero.trio`, which do not exist on
    # a database that still has `side` - so building it here would abort the whole
    # script on every legacy database, before the reshape that creates those columns.

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

    _backfill_comps_key(con)
    _reshape_to_trios(con)
    _reconcile_local_synced_pairs(con)

    # AFTER the reshape, for the same reason idx_match_pushable is created here: the
    # columns do not exist until it has run.
    if "trio" in columns(con, "match_hero"):
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_match_hero_trio"
            " ON match_hero(match_id, trio, hero_slug)"
        )
    if "winning_trio" in columns(con, "match"):
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_match_winning_trio ON match(winning_trio)"
        )
    con.executescript(open(VIEWS).read())

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
        DEFAULT_SUMMARY_CELLS,
        DEFAULT_DRAFT_PICK_CELLS,
        DEFAULT_PREMATCH_CELLS,
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
                con.execute(f"ALTER TABLE install RENAME COLUMN {old_col} TO {new_col}")
                renamed.append(f"install.{old_col}->{new_col}")

    # Created HERE, not in schema.sql: it references pushed_at, and schema.sql is
    # executed BEFORE ADD_COLUMNS adds that column to pre-existing databases.
    # A partial index on a not-yet-existing column aborts the whole script.
    if "pushed_at" in columns(con, "match"):
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_match_pushable ON match(pushed_at)"
            " WHERE origin='local' AND natural_key IS NOT NULL"
        )

    # Same reason as idx_match_pushable: comps_key is added by ADD_COLUMNS above, which
    # runs AFTER schema.sql, so an index on it cannot live in schema.sql without
    # aborting that script on every database that predates the column. NOT unique - see
    # the column comment; the lookup is by value, so it only has to avoid a scan.
    if "comps_key" in columns(con, "match"):
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_match_comps_key ON match(comps_key)"
        )

    # This install's identity - generated once, then never touched again.
    # uuid4 is right here: it needs to be unique across machines that never talk
    # to each other, with no coordination and no central issuer.
    con.execute(
        "INSERT OR IGNORE INTO install(id,instance_uuid,created_at) VALUES(1,?,?)",
        (
            str(uuid.uuid4()),
            datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        ),
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
    # PROVENANCE: the theme names and effects come from the wiki API, not from the
    # screen - see docs/solstice-clash/README.md section 2, with the raw page saved as
    # data/solstice_clash/solstice_clash_wiki.txt. Dates come from the in-game Themes
    # screen, which shows the current theme and a "Starts in Nh" countdown for the next.
    #
    # Transcribing that list by hand cost us one: the wiki lists FIVE themes and this
    # table seeded four - "Tranquil Grounds" was simply missed, and a match played under
    # it would have resolved to Unknown / Default.
    themes = [
        # slug,               name,                starts_at, ends_at,    default
        ("unknown", "Unknown / Default", None, None, 1),
        ("tranquil-grounds", "Tranquil Grounds", None, None, 0),
        ("fierce-duel", "Fierce Duel", None, None, 0),
        # Rotates 02:00 Europe/Skopje on 2026-07-29, which is exactly midnight UTC -
        # and therefore also an hour-bucket boundary, so no match straddles it.
        ("converging-paths", "Converging Paths", None, "2026-07-29T00:00:00Z", 0),
        # Both boundaries confirmed on the in-game Themes screen 2026-07-29 00:25 UTC:
        # Flourishing Wilds shown as Current, Tactical Grounds as "Starts in 2d 23h",
        # which lands on 2026-08-01 midnight UTC - 02:00 Europe/Skopje, the same wall
        # clock every rotation has used, and an hour-bucket boundary so no match
        # straddles it. A three-day cadence, matching Converging Paths.
        (
            "flourishing-wilds",
            "Flourishing Wilds",
            "2026-07-29T00:00:00Z",
            "2026-08-01T00:00:00Z",
            0,
        ),
        ("tactical-grounds", "Tactical Grounds", "2026-08-01T00:00:00Z", None, 0),
        # The operator expects the final theme of the event to run two days rather than
        # three. That is inference from the event end date, not an observed boundary, so
        # it is written here and NOT in the table: a guessed window would file matches
        # under a theme nobody confirmed, which is the failure the NULLs exist to prevent.
    ]
    for slug, name, starts, ends, is_default in themes:
        con.execute(
            "INSERT OR IGNORE INTO theme"
            "(event_id,slug,name,starts_at,ends_at,is_default) VALUES(?,?,?,?,?,?)",
            (event_id, slug, name, starts, ends, is_default),
        )
        # A boundary confirmed later must reach a database that already has the row.
        # INSERT OR IGNORE alone silently kept the old NULL, so filling in a rotation
        # date here changed nothing where it mattered - the install already collecting.
        # Only NULL is filled: a recorded boundary is never overwritten, because that
        # would silently re-file matches already attributed to a theme.
        if starts:
            con.execute(
                "UPDATE theme SET starts_at=? WHERE slug=? AND starts_at IS NULL",
                (starts, slug),
            )
        if ends:
            con.execute(
                "UPDATE theme SET ends_at=? WHERE slug=? AND ends_at IS NULL",
                (ends, slug),
            )

    # Backfill matches recorded before the tables existed. Matched on the raw OCR
    # name, which is all those rows have; from here on theme_id is set at capture.
    con.execute("UPDATE match SET event_id=? WHERE event_id IS NULL", (event_id,))
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

    summary = {
        "db": db,
        "fresh": fresh,
        "version": SCHEMA_VERSION,
        "columns_added": added,
        "columns_renamed": renamed,
        "config_rows_inserted": inserted,
    }
    if quiet:
        return summary

    print(f"  database    : {db}{' (created)' if fresh else ''}")
    print(f"  schema      : v{SCHEMA_VERSION}")
    print(f"  columns added: {added or 'none'}")
    print(f"  columns renamed: {renamed or 'none'}")
    print(f"  config rows inserted: {inserted}")
    for t in (
        "hero",
        "hero_alias",
        "hero_skin",
        "hero_skill",
        "solstice_roster",
        "cell_registry",
        "art_transform",
        "library_config",
    ):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:16s} {n:>5} rows")
    return summary


def main() -> None:
    apply(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "heroes.sqlite"))


if __name__ == "__main__":
    main()
