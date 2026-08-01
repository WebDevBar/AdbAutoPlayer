"""Match recording.

These tables are LOCALLY EARNED - `build_hero_db.py` never touches them. A wiki refresh
rebuilds only the reference tables - `hero`, `hero_skin`, `hero_skill` and
`solstice_roster`. The match tables are never touched, which is the load-bearing half.

Two rules that come from real failures:

- **Every slot is stored, including unknown ones.** Dropping an unidentified slot would
  make a 3v3 look like a 2v3 and silently corrupt the training data.
- **The pool is stored, not just the picks.** Which heroes were *available and not
  picked* is a real signal, and a pick absent from the pool is a detected error.
"""

from __future__ import annotations

import importlib.util
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from .matchkey import comps_key
from .odds import themes_sharing_modifiers
from .paths import resource_file


@dataclass(frozen=True)
class MatchRecord:
    source: str  # 'compete' | 'spectate'
    captured_at: str
    natural_key: str | None = None  # NULL until the match has enough stable facts
    theme: str | None = None       # RAW OCR read, provenance only
    event_id: int | None = None    # resolved - see MatchStore.resolve_theme
    theme_id: int | None = None    # resolved - the source of truth, not `theme`
    theme_resolved_by: str | None = None  # 'window' | 'ocr' | 'default'
    balance_epoch: str | None = None
    left_player: str | None = None
    left_rating: int | None = None
    left_rank: int | None = None
    right_player: str | None = None
    right_rating: int | None = None
    right_rank: int | None = None
    outcome: str | None = None  # 'left' | 'right' | 'draw' | None
    outcome_source: str | None = None


@dataclass(frozen=True)
class HeroSlot:
    side: str  # 'left' | 'right'
    slot: int
    hero_slug: str | None  # None when status == 'unknown'
    art_ref: str | None
    status: str  # 'identified' | 'unknown'
    score: float | None = None
    margin: float | None = None
    # Provenance: candidate_scope/pool_miss separate "the pool read was wrong" from
    # "a legitimate hero outside the pool".
    cell_type: str | None = None
    cell_name: str | None = None
    candidate_scope: str | None = None
    pool_miss: int | None = None
    runner_up_slug: str | None = None
    runner_up_score: float | None = None
    crop_path: str | None = None
    frame_path: str | None = None
    # From the post-match summary. Named for the column ICONS (sword/heart/shield), not
    # for a guess at their meaning - the shield column's semantics are unconfirmed.
    stat_sword: int | None = None
    stat_heart: int | None = None
    stat_shield: int | None = None
    power: int | None = None        # long-press popup only
    identified_by: str | None = None  # 'image' | 'longpress_ocr'


@dataclass(frozen=True)
class PoolSlot:
    slot: int  # 1..20, row-major
    hero_slug: str | None
    art_ref: str | None
    status: str  # 'identified' | 'unknown' | 'banned'
    banned: int = 0
    score: float | None = None
    margin: float | None = None
    runner_up_slug: str | None = None
    runner_up_score: float | None = None
    crop_path: str | None = None
    frame_path: str | None = None


@dataclass(frozen=True)
class OddsSample:
    sampled_at: str
    left_pool: int | None = None
    right_pool: int | None = None
    left_odds: float | None = None
    right_odds: float | None = None
    spectators: int | None = None


@dataclass(frozen=True)
class AuditRow:
    """One identification, recorded whether or not the two channels agreed.

    Agreements are recorded too. Logging only misfires yields a numerator with no
    denominator: three errors means nothing without knowing if it was three in fifty or
    three in five thousand.
    """

    screen_slug: str
    side: str
    slot: int
    image_slug: str | None
    image_art_ref: str | None
    image_score: float
    image_margin: float
    ocr_slug: str | None
    frame_path: str | None
    match_id: int | None = None


# Valid values. The schema documents these in comments only, so the store enforces them:
# without this a Phase 2 caller could persist source='comptee' or side='blu' silently.
_SOURCES = frozenset(
    {
        "compete",
        "spectate",
        "spectate_summary",
        # Mode B: the details screen of a match the USER played, recorded
        # passively without touching the device. Parallel to spectate_summary -
        # same screen, same parser, different observer.
        "compete_summary",
    }
)
_SIDES = frozenset({"left", "right"})
_HERO_STATUSES = frozenset({"identified", "unknown"})
_POOL_STATUSES = frozenset({"identified", "unknown", "banned"})
_OUTCOMES = frozenset({"left", "right", "draw"})
POOL_SIZE = 20  # the draft grid is always 5x4
_SIDE_SIZE = 3  # every Solstice Clash comp is exactly three heroes

# The only event this client records. Was a bare literal in three places; the
# mixin needs it too, and a fourth copy is how they start disagreeing.
EVENT_SLUG = "solstice-clash"

# Two captures belong to the SAME occurrence when they fall within this many
# seconds of each other. Single-linkage: a capture within the window of two
# clusters bridges them into one, which is what makes the result independent of
# arrival order.
#
# MUST match WINDOW_SECONDS in app/occurrence.py on the server. `comps_key`
# carries no time at all - deliberately, because a bucket splits at its
# boundaries - so this window is the ONLY thing separating one match seen twice
# from a genuine rematch between the same two trios. If the client's window and
# the server's disagree, the client either withholds bridging evidence or pushes
# a duplicate the server then has to unpick.
WINDOW_SECONDS = 120


def _parse_stamp(value) -> datetime | None:
    """Parse a stored ISO timestamp, or None when it is missing or unusable.

    A naive value is read as UTC rather than rejected. Every writer in this
    codebase stores UTC, and this window is a local dedupe heuristic - refusing to
    coalesce is a worse failure than assuming the timezone every row already has.
    """
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _as_stamp(value) -> str | None:
    """Normalise an API timestamp to the form this database stores.

    The API returns offset-aware ISO strings; the theme table stores the Z form, and the
    window comparison is a STRING comparison in SQLite. Mixing the two would compare
    "2026-07-29T00:00:00+00:00" against "2026-07-29T00:00:00Z" and get the wrong answer
    at exactly the boundary the window exists to mark.
    """
    if not value:
        return None
    text = str(value)
    if text.endswith("+00:00"):
        return text[:-6] + "Z"
    return text


class MatchStore:
    _HERO_COLS = (
        "side",
        "slot",
        "hero_slug",
        "art_ref",
        "status",
        "score",
        "margin",
        "cell_type",
        "cell_name",
        "candidate_scope",
        "pool_miss",
        "runner_up_slug",
        "runner_up_score",
        "crop_path",
        "frame_path",
        "stat_sword",
        "stat_heart",
        "stat_shield",
        "power",
        "identified_by",
    )
    _POOL_COLS = (
        "slot",
        "hero_slug",
        "art_ref",
        "status",
        "banned",
        "score",
        "margin",
        "runner_up_slug",
        "runner_up_score",
        "crop_path",
        "frame_path",
    )
    _MATCH_COLS = (
        "natural_key",
        "event_id",
        "theme_id",
        "theme_resolved_by",
        "source",
        "captured_at",
        "theme",
        "balance_epoch",
        "left_player",
        "left_rating",
        "left_rank",
        "right_player",
        "right_rating",
        "right_rank",
        "outcome",
        "outcome_source",
    )

    # Databases this process has already brought up to schema. Migrating is idempotent
    # and costs a few milliseconds, but doing it per connection would run it thousands
    # of times a session for nothing.
    _schema_ensured: ClassVar[set[Path]] = set()

    def __init__(self, db_path: Path) -> None:
        self._db = Path(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Bring this database up to the current schema, and create the derived views.

        THIS IS NOT OPTIONAL PLUMBING. A shipped build never runs `migrate.py`, and
        `solstice_db_path` returns an existing user database untouched - so without this
        a database keeps whatever schema it was seeded with forever. A contributor who
        installed before `match.predicted_left` existed had EVERY match fail at the
        write with "no such column: predicted_left", and updating the app did not help,
        because nothing ever added the column. 27 lost matches in one log, 2026-07-28.

        Migration is only ATTEMPTED when something is actually missing. `migrate.py`
        rewrites the schema and so needs a write lock, and taking one on every store
        construction deadlocked against connections other callers still had open -
        `with sqlite3.connect(...)` commits but does not close. The check below is a
        handful of PRAGMA reads and needs no lock at all, so the common case (an
        up-to-date database) touches nothing.

        Checked by COLUMN rather than by recorded version: a database seeded from an old
        bundle can carry a current-looking `schema_version` row and still lack the
        columns, which is exactly the case that has to be repaired.

        `migrate.py` is called rather than reimplemented. Two copies of a migration list
        drift, and the one that drifts is the one nobody runs by hand.
        """
        if self._db in MatchStore._schema_ensured:
            return
        MatchStore._schema_ensured.add(self._db)

        script = resource_file(Path("solstice_clash") / "migrate.py")
        if script is None or not script.is_file():
            logging.debug("[SC-93] migrate.py not found; schema left as-is")
            return
        try:
            spec = importlib.util.spec_from_file_location("_solstice_migrate", script)
            if spec is None or spec.loader is None:
                return
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if self._schema_is_current(module):
                return
            summary = module.apply(str(self._db), quiet=True)
        except Exception as exc:  # see the docstring: never block startup
            logging.warning(f"[SC-93] could not migrate {self._db}: {exc}")
            return

        added = summary.get("columns_added") or []
        renamed = summary.get("columns_renamed") or []
        logging.info(
            f"[SC-93] database upgraded to schema v{summary.get('version')}: "
            f"added {added or 'nothing'}, renamed {renamed or 'nothing'}"
        )

    def _schema_is_current(self, migrate_module) -> bool:
        """Read-only: does the database already have every column and view we need?

        Returns False on any doubt - a needless migration is idempotent and cheap, while
        a skipped one loses every match the user records.
        """
        try:
            con = sqlite3.connect(self._db)
        except sqlite3.Error:
            return False
        try:
            tables = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
            if "hero_matchup" not in tables:
                return False
            for table, column, _decl in getattr(migrate_module, "ADD_COLUMNS", []):
                if table not in tables:
                    return False
                present = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
                if column not in present:
                    return False
            return True
        except sqlite3.Error:
            return False
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db)
        # Required for ON DELETE CASCADE. SQLite defaults this OFF per connection, so
        # declaring it in the schema alone is not enough.
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @staticmethod
    def _check(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    def record_match(self, rec: MatchRecord) -> int:
        """Insert, or return the existing id if this natural_key was already seen.

        Re-observing a match must not duplicate it or raise - Mode B will see the same
        match on consecutive polls. An unkeyed observation always inserts, because
        mid-draft there is nothing stable to dedupe on yet.
        """
        self._check(rec.source in _SOURCES, f"invalid source: {rec.source!r}")
        self._check(
            rec.outcome is None or rec.outcome in _OUTCOMES,
            f"invalid outcome: {rec.outcome!r}",
        )
        cols = ",".join(self._MATCH_COLS)
        placeholders = ",".join("?" * len(self._MATCH_COLS))
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO match({cols}) VALUES({placeholders}) "
                f"ON CONFLICT(natural_key) DO NOTHING",
                tuple(getattr(rec, c) for c in self._MATCH_COLS),
            )
            if rec.natural_key is None:
                return int(cur.lastrowid or 0)
            row = con.execute(
                "SELECT id FROM match WHERE natural_key=?", (rec.natural_key,)
            ).fetchone()
        return int(row[0])

    def match_by_natural_key(self, natural_key: str) -> int | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id FROM match WHERE natural_key=?", (natural_key,)
            ).fetchone()
        return int(row[0]) if row else None

    def set_natural_key(self, match_id: int, natural_key: str) -> None:
        """Set once a match has enough stable facts to be keyed."""
        with self._connect() as con:
            con.execute(
                "UPDATE match SET natural_key=? WHERE id=?", (natural_key, match_id)
            )

    def _event_slug_for(self, con: sqlite3.Connection, match_id: int) -> str | None:
        """`match.event_id` first, then `theme_id -> theme.event_id`, else None.

        The fallback is not hypothetical: 120 of 1,200 keyed rows carry a NULL
        `event_id` and every one of them resolves through its theme. A row that
        resolves through neither keeps `comps_key` NULL and stays out of the pool -
        an unattributable match is not identity we can compute.
        """
        row = con.execute(
            "SELECT event_id, theme_id FROM match WHERE id=?", (match_id,)
        ).fetchone()
        if row is None:
            return None
        if row[0] is not None:
            found = con.execute(
                "SELECT slug FROM event WHERE id=?", (row[0],)
            ).fetchone()
            if found is not None:
                return str(found[0])
        if row[1] is not None:
            found = con.execute(
                "SELECT e.slug FROM theme t JOIN event e ON e.id = t.event_id"
                " WHERE t.id=?",
                (row[1],),
            ).fetchone()
            if found is not None:
                return str(found[0])
        return None

    def _bridged_roots(
        self,
        con: sqlite3.Connection,
        key: str,
        at: datetime,
        exclude: int | None = None,
    ) -> list[int]:
        """Active local rows on `key` whose bounds are within the window of `at`.

        Bounds are sufficient for single-linkage in one dimension: the nearest point
        of a cluster to any new capture is always one of its two extremes.

        Only SUPERSESSION ROOTS are candidates. A row already superseded is not a
        cluster of its own, and linking to it would build a chain the analysis
        filters have to walk instead of a single hop.

        Returns:
            Root ids, EARLIEST FIRST. The caller merges into the first.
        """
        rows = con.execute(
            "SELECT id, COALESCE(captures_min_at, captured_at),"
            "       COALESCE(captures_max_at, captured_at)"
            "  FROM match"
            " WHERE comps_key=? AND origin='local' AND superseded_by IS NULL",
            (key,),
        ).fetchall()
        hits: list[tuple[datetime, int]] = []
        for match_id, low_raw, high_raw in rows:
            if exclude is not None and int(match_id) == exclude:
                continue
            low, high = _parse_stamp(low_raw), _parse_stamp(high_raw)
            if low is None or high is None:
                continue
            if (low - at).total_seconds() > WINDOW_SECONDS:
                continue
            if (at - high).total_seconds() > WINDOW_SECONDS:
                continue
            hits.append((low, int(match_id)))
        hits.sort()
        return [match_id for _low, match_id in hits]

    def match_by_comps_key(self, key: str, near: str | None = None) -> int | None:
        """An existing local row already holding this identity, or None.

        `near` applies the same window `finalise_identity` uses, and passing it is
        the correct call in every live path. `comps_key` carries NO time, so two
        matches between the same two trios a day apart share it - that is a GENUINE
        REMATCH, which the server keeps as a separate occurrence. An unbounded
        lookup would make this client refuse to record any rematch, ever.
        """
        at = _parse_stamp(near)
        with self._connect() as con:
            if at is None:
                row = con.execute(
                    "SELECT id FROM match"
                    " WHERE comps_key=? AND origin='local' AND superseded_by IS NULL"
                    " ORDER BY id LIMIT 1",
                    (key,),
                ).fetchone()
                return int(row[0]) if row else None
            roots = self._bridged_roots(con, key, at)
        return roots[0] if roots else None

    def finalise_identity(self, match_id: int) -> None:
        """Compute this row's canonical identity, now that its heroes are known.

        `record_match()` cannot do this. The spectate flow creates the row before
        the heroes exist, so `comps_key` is not computable there; a provisional row
        keeps it NULL and the push gate excludes it.

        Two outcomes. If no existing local occurrence is within the window, the row
        starts one: `comps_key` is stored and the capture bounds are initialised to
        its own capture time. Otherwise the row joins the EARLIEST occurrence it
        bridges - that occurrence's bounds widen to cover everything merged into it,
        every other bridged occurrence is folded in, and this row is marked
        `superseded_by` the survivor.

        Hero rows, the draft frame and the odds sample all STAY on the superseded
        row. They are this install's own evidence, and the side-integrity audit
        reads them.
        """
        with self._connect() as con:
            event_slug = self._event_slug_for(con, match_id)
            if event_slug is None:
                return
            sides: dict[str, list[str]] = {"left": [], "right": []}
            for side, slug in con.execute(
                "SELECT side, hero_slug FROM match_hero"
                " WHERE match_id=? AND hero_slug IS NOT NULL",
                (match_id,),
            ):
                if side in sides:
                    sides[side].append(str(slug))
            if len(sides["left"]) != _SIDE_SIZE or len(sides["right"]) != _SIDE_SIZE:
                return

            key = comps_key(event_slug, sides["left"], sides["right"])
            con.execute(
                "UPDATE match SET comps_key=?,"
                " captures_min_at=COALESCE(captures_min_at, captured_at),"
                " captures_max_at=COALESCE(captures_max_at, captured_at)"
                " WHERE id=?",
                (key, match_id),
            )
            row = con.execute(
                "SELECT captured_at FROM match WHERE id=?", (match_id,)
            ).fetchone()
            at = _parse_stamp(row[0]) if row is not None else None
            if at is None:
                return

            bridged = self._bridged_roots(con, key, at, exclude=match_id)
            if not bridged:
                return
            target, absorbed = bridged[0], bridged[1:]
            self._widen_bounds(con, target, [match_id, *bridged])
            for absorbed_id in absorbed:
                # One level deep: rows already pointing at an occurrence this
                # capture absorbed must follow it, or they hang off a row that is
                # itself no longer active and every filter has to walk a chain.
                con.execute(
                    "UPDATE match SET superseded_by=? WHERE superseded_by=?",
                    (target, absorbed_id),
                )
                con.execute(
                    "UPDATE match SET superseded_by=? WHERE id=?",
                    (target, absorbed_id),
                )
            con.execute(
                "UPDATE match SET superseded_by=? WHERE id=?", (target, match_id)
            )

    def _widen_bounds(
        self, con: sqlite3.Connection, target: int, members: list[int]
    ) -> None:
        """Set `target`'s bounds to the extremes across every merged member.

        Compared as PARSED instants, never as strings. This database holds both the
        `...Z` and the `...+00:00` spelling of the same moment, and `Z` sorts after
        `+` - so SQL MIN()/MAX() picks the wrong row at exactly the tie the bounds
        exist to record. The stored spelling is written back unchanged.
        """
        placeholders = ",".join("?" * len(members))
        rows = con.execute(
            "SELECT COALESCE(captures_min_at, captured_at),"
            "       COALESCE(captures_max_at, captured_at)"
            f"  FROM match WHERE id IN ({placeholders})",
            members,
        ).fetchall()
        lows = [(_parse_stamp(r[0]), r[0]) for r in rows if _parse_stamp(r[0])]
        highs = [(_parse_stamp(r[1]), r[1]) for r in rows if _parse_stamp(r[1])]
        if not lows or not highs:
            return
        con.execute(
            "UPDATE match SET captures_min_at=?, captures_max_at=? WHERE id=?",
            (min(lows)[1], max(highs)[1], target),
        )

    def set_outcome(self, match_id: int, outcome: str, source: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE match SET outcome=?, outcome_source=? WHERE id=?",
                (outcome, source, match_id),
            )

    def record_heroes(self, match_id: int, slots: list[HeroSlot]) -> None:
        for slot in slots:
            self._check(slot.side in _SIDES, f"invalid side: {slot.side!r}")
            self._check(
                slot.status in _HERO_STATUSES, f"invalid status: {slot.status!r}"
            )
            self._check(
                (slot.hero_slug is None) == (slot.status == "unknown"),
                f"slot {slot.side}{slot.slot}: status {slot.status!r} disagrees with "
                f"hero_slug {slot.hero_slug!r}",
            )
        cols = ",".join(self._HERO_COLS)
        placeholders = ",".join("?" * (len(self._HERO_COLS) + 1))
        updates = ",".join(f"{c}=excluded.{c}" for c in self._HERO_COLS[2:])
        with self._connect() as con:
            con.executemany(
                f"INSERT INTO match_hero(match_id,{cols}) VALUES({placeholders}) "
                f"ON CONFLICT(match_id,side,slot) DO UPDATE SET {updates}",
                [(match_id, *(getattr(s, c) for c in self._HERO_COLS)) for s in slots],
            )

    def heroes_for(self, match_id: int) -> list[HeroSlot]:
        cols = ",".join(self._HERO_COLS)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {cols} FROM match_hero WHERE match_id=? ORDER BY side, slot",
                (match_id,),
            ).fetchall()
        return [HeroSlot(*r) for r in rows]

    def record_pool(self, match_id: int, slots: list[PoolSlot]) -> None:
        """Record the 20 offered heroes.

        Never skip banned slots - "available but banned" is a distinct fact from
        "this slot was not read".
        """
        for slot in slots:
            self._check(
                1 <= slot.slot <= POOL_SIZE, f"pool slot out of range: {slot.slot}"
            )
            self._check(
                slot.status in _POOL_STATUSES, f"invalid pool status: {slot.status!r}"
            )
            self._check(
                bool(slot.banned) == (slot.status == "banned"),
                f"pool slot {slot.slot}: banned={slot.banned} disagrees with "
                f"status {slot.status!r}",
            )
        cols = ",".join(self._POOL_COLS)
        placeholders = ",".join("?" * (len(self._POOL_COLS) + 1))
        updates = ",".join(f"{c}=excluded.{c}" for c in self._POOL_COLS[1:])
        with self._connect() as con:
            con.executemany(
                f"INSERT INTO match_pool(match_id,{cols}) VALUES({placeholders}) "
                f"ON CONFLICT(match_id,slot) DO UPDATE SET {updates}",
                [(match_id, *(getattr(s, c) for c in self._POOL_COLS)) for s in slots],
            )

    def pool_is_complete(self, match_id: int) -> bool:
        """Were all 20 grid slots recorded?

        A partial pool is silently wrong: it would under-constrain later identification
        and make "available but not picked" incomplete. Phase 2 should check this before
        using a pool as a candidate constraint.
        """
        with self._connect() as con:
            rows = con.execute(
                "SELECT COUNT(DISTINCT slot) FROM match_pool WHERE match_id=?",
                (match_id,),
            ).fetchone()
        return int(rows[0]) == POOL_SIZE

    def pool_for(self, match_id: int) -> list[PoolSlot]:
        cols = ",".join(self._POOL_COLS)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT {cols} FROM match_pool WHERE match_id=? ORDER BY slot",
                (match_id,),
            ).fetchall()
        return [PoolSlot(*r) for r in rows]

    def record_odds(self, match_id: int, sample: OddsSample) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO match_odds(match_id,sampled_at,left_pool,right_pool,"
                "left_odds,right_odds,spectators) VALUES(?,?,?,?,?,?,?)",
                (
                    match_id,
                    sample.sampled_at,
                    sample.left_pool,
                    sample.right_pool,
                    sample.left_odds,
                    sample.right_odds,
                    sample.spectators,
                ),
            )

    def odds_for(self, match_id: int) -> list[OddsSample]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT sampled_at,left_pool,right_pool,left_odds,right_odds,spectators "
                "FROM match_odds WHERE match_id=? ORDER BY sampled_at",
                (match_id,),
            ).fetchall()
        return [OddsSample(*r) for r in rows]

    # ------------------------------------------------------------------
    # Pooled sync
    # ------------------------------------------------------------------

    def instance_uuid(self) -> str:
        """This install's identity, created on first use.

        Created HERE and not only in `migrate.py`, because a shipped build never
        runs that script. Seeding deletes the bundled `install` row - correctly,
        or every contributor would claim one identity - and nothing recreated it,
        so the client sent an empty `X-Instance-Id` and the server answered 400
        to every request. A fresh contributor's matches never reached the pool.

        `INSERT OR IGNORE` then re-select, rather than insert-and-return: two
        processes on the same database must end up with the SAME uuid, not two.
        """
        with self._connect() as con:
            row = con.execute("SELECT instance_uuid FROM install WHERE id=1").fetchone()
            if row and row[0]:
                return str(row[0])
            con.execute(
                "INSERT OR IGNORE INTO install(id,instance_uuid,created_at)"
                " VALUES(1,?,?)",
                (
                    str(uuid.uuid4()),
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            row = con.execute(
                "SELECT instance_uuid FROM install WHERE id=1"
            ).fetchone()
        return str(row[0])

    def record_prediction(
        self,
        match_id: int,
        p_left: float,
        source: str,
        locked: int,
        predicted_at: str,
    ) -> None:
        """Store the pre-match prediction, so it can be scored against the result.

        Written once, before the fight. It is never recomputed afterwards: the model
        changes as matches arrive, so a prediction reconstructed later would be the
        answer today's model gives, not the one that was acted on.
        """
        with self._connect() as con:
            con.execute(
                "UPDATE match SET predicted_left=?, predicted_source=?,"
                " predicted_locked=?, predicted_at=? WHERE id=?",
                (p_left, source, locked, predicted_at, match_id),
            )

    def scored_predictions(self) -> list[tuple]:
        """(predicted_left, outcome, predicted_source, predicted_locked) for review.

        The rows that answer "where was the logic confidently wrong".

        Superseded rows are excluded. One match observed twice is ONE prediction,
        not two - counting both would inflate every accuracy figure by however many
        duplicates the capture loop happened to produce, and duplicates are not
        distributed evenly across the outcomes.
        """
        with self._connect() as con:
            return con.execute(
                "SELECT predicted_left, outcome, predicted_source, predicted_locked"
                "  FROM match"
                " WHERE predicted_left IS NOT NULL AND outcome IN ('left','right')"
                "   AND superseded_by IS NULL"
            ).fetchall()

    def matches_for_fit(self) -> list[tuple]:
        """Hero rows for every decisive match, for the odds model.

        NOT filtered to full three-a-side here: the query returns one row per identified
        hero, and `load_matches` is what drops a partial comp. Saying otherwise here sent
        a reader looking for a completeness check in the SQL that is not there.

        The completeness filter is not tidiness. Builds before wdb-12.9.24-7 shipped no
        hero art, so on any machine but the developer's every cell read `unknown` and
        matches were stored with an outcome, a theme and NO heroes. Those rows cannot
        sync - they never earn a natural_key - but a naive COUNT(*) would still count
        them toward the display gate, opening it on evidence that does not exist.

        Superseded rows are excluded. Their hero rows are kept - they are this
        install's own evidence and the side-integrity audit reads them - but a match
        seen twice must weigh once in the fit, or every duplicate silently doubles
        one comp's contribution to the model.

        Returns (match_id, outcome, theme_id, event_id, left_player, right_player,
        left_rating, right_rating, side, slug) joined, because a three-a-side check needs
        the heroes anyway. `load_matches` unpacks these positionally, so a new column
        goes on the END or it silently shifts every field after it.
        """
        with self._connect() as con:
            return con.execute(
                "SELECT m.id, m.outcome, m.theme_id, m.event_id,"
                "       m.left_player, m.right_player,"
                "       m.left_rating, m.right_rating,"
                "       h.side, h.hero_slug"
                "  FROM match m JOIN match_hero h ON h.match_id = m.id"
                " WHERE m.outcome IN ('left','right') AND h.hero_slug IS NOT NULL"
                "   AND m.superseded_by IS NULL"
                " ORDER BY m.id, h.side, h.slot"
            ).fetchall()

    def pull_cursor(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT pull_cursor FROM install WHERE id=1").fetchone()
        return int(row[0]) if row and row[0] else 0

    def set_pull_cursor(self, seq: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE install SET pull_cursor=? WHERE id=1", (str(seq),))

    def supersession_cursor(self) -> int:
        """High-water mark for tombstones, SEPARATE from `pull_cursor`.

        Marking a row superseded does not advance its `Match.seq`, so supersessions
        ride their own server sequence. Reusing the match cursor would either ask
        from zero every time - re-reading page one forever - or, once the match
        cursor ran ahead, permanently miss retirements published behind it.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT supersession_cursor FROM install WHERE id=1"
            ).fetchone()
        return int(row[0]) if row and row[0] else 0

    def set_supersession_cursor(self, seq: int) -> None:
        """Record how far tombstones have been read. Never touches `pull_cursor`."""
        with self._connect() as con:
            con.execute(
                "UPDATE install SET supersession_cursor=? WHERE id=1", (str(seq),)
            )

    def pushable_matches(self, limit: int = 500) -> list[dict]:
        """Rows this install may send.

        Three conditions, and every one of them matters:

        - `origin='local'` - never echo back rows we PULLED, or every contributor
          re-uploads everyone else's data forever.
        - `comps_key IS NOT NULL` - a row whose heroes were never read has no
          identity. This replaced `natural_key IS NOT NULL`: the local key is now
          the outcome-free one, and `natural_key` is only ever the SERVER's answer,
          adopted after a successful push. Gating on it would mean nothing was
          pushable until it had already been pushed.
        - `pushed_at IS NULL` - per row, NOT a timestamp watermark. A match is
          inserted before its heroes are read, so it becomes syncable later than
          it was created; a watermark would leave it permanently behind.

        There is deliberately NO supersession term. A locally superseded row is
        STILL PUSHED: the server is the sole deduplicator, and a row this client
        folded into another is exactly the bridging evidence the server needs to
        reach the same conclusion. Withholding it starves the reconciliation.
        """
        with self._connect() as con:
            rows = con.execute(
                # Every match column is qualified: joining match_odds made a bare `id`
                # ambiguous, and SQLite reports that as a failed sync rather than a
                # crash - so it retried and lost every push until someone read the log.
                "SELECT match.id, natural_key, source, captured_at, theme, outcome,"
                " left_player, left_rating, left_rank,"
                " right_player, right_rating, right_rank,"
                # Pushed so calibration can be scored across contributors rather than
                # one machine at a time. The server pairs it with client_version,
                # without which an older build's number means something different.
                " predicted_left, predicted_source, predicted_locked,"
                # The crowd's money, from the newest sample. LEFT JOIN because most
                # matches have none - joined mid-draft, or an older client.
                " o.left_pool, o.right_pool, o.left_odds, o.right_odds, o.spectators"
                " FROM match"
                " LEFT JOIN match_odds o ON o.id = ("
                "   SELECT id FROM match_odds WHERE match_id = match.id"
                "   ORDER BY sampled_at DESC, id DESC LIMIT 1)"
                " WHERE origin='local' AND comps_key IS NOT NULL"
                "   AND pushed_at IS NULL AND push_rejected_reason IS NULL"
                " ORDER BY match.id LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                heroes = con.execute(
                    "SELECT side, slot, hero_slug, stat_sword, stat_heart, stat_shield"
                    " FROM match_hero WHERE match_id=? ORDER BY side, slot",
                    (r[0],),
                ).fetchall()
                out.append(
                    {
                        "local_id": r[0],
                        "source": r[2],
                        "captured_at": r[3],
                        "event_slug": EVENT_SLUG,
                        # The raw screen read, never a slug: identity is the
                        # server's to decide.
                        "theme_ocr": r[4],
                        "outcome": r[5],
                        "left_player": r[6], "left_rating": r[7], "left_rank": r[8],
                        "right_player": r[9], "right_rating": r[10], "right_rank": r[11],
                        # Our pre-fight prediction, scored server-side against the
                        # result and paired with client_version.
                        "predicted_left": r[12],
                        "predicted_source": r[13],
                        "predicted_locked": r[14],
                        # The GAME's betting market, from the newest sample.
                        "left_pool": r[15],
                        "right_pool": r[16],
                        "left_odds": r[17],
                        "right_odds": r[18],
                        "spectators": r[19],
                        "heroes": [
                            {
                                "side": h[0], "slot": h[1], "hero_slug": h[2],
                                "stat_sword": h[3], "stat_heart": h[4],
                                "stat_shield": h[5],
                            }
                            for h in heroes
                        ],
                    }
                )
        return out

    def adopt_canonical(
        self,
        local_id: int,
        natural_key: str,
        theme_slug: str | None,
        theme_resolved_by: str | None,
    ) -> None:
        """Take the server's identity for a row we just pushed, then mark it sent.

        All in ONE transaction. Without adoption the client duplicates its own
        data: it stores a row under its locally-computed key, the server accepts
        it under the canonical one, and the next pull returns that match under a
        key matching nothing locally - so it is inserted a second time.

        The collision branch is ORIGIN-AWARE, and that distinction is the whole
        point of it. A clashing SYNCED row is someone else's copy of a match we
        also observed ourselves, so it is dropped and ours is kept - it carries
        this install's own hero evidence. A clashing LOCAL row is never deleted.
        Under an orientation-invariant `comps_key` two of our own captures of one
        match land on one canonical key, and deleting either would destroy the
        frame-confirmed evidence the side-integrity audit reads.
        """
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as con:
            clash = con.execute(
                "SELECT id, origin FROM match WHERE natural_key=? AND id<>?",
                (natural_key, local_id),
            ).fetchone()
            takes_key = True
            if clash is not None:
                if str(clash[1]) == "synced":
                    con.execute("DELETE FROM match WHERE id=?", (clash[0],))
                else:
                    takes_key = self._resolve_local_clash(con, local_id, int(clash[0]))
            theme_id = None
            if theme_slug:
                row = con.execute(
                    "SELECT id FROM theme WHERE slug=?", (theme_slug,)
                ).fetchone()
                theme_id = row[0] if row else None
            con.execute(
                # COALESCE, not a bare `?`: when this row loses the clash it becomes
                # a member of another occurrence and must stay UNKEYED, while still
                # being marked pushed so the backlog does not re-send it forever.
                "UPDATE match SET natural_key=COALESCE(?, natural_key), pushed_at=?,"
                " theme_id=COALESCE(?, theme_id),"
                " theme_resolved_by=COALESCE(?, theme_resolved_by)"
                " WHERE id=?",
                (
                    natural_key if takes_key else None,
                    now,
                    theme_id,
                    theme_resolved_by,
                    local_id,
                ),
            )

    def _supersession_root(self, con: sqlite3.Connection, match_id: int) -> int:
        """Follow `superseded_by` to the end of the chain.

        Guarded against a cycle rather than trusting there is none. A cycle would
        make both rows inactive forever - every analysis filter drops them and
        retirement skips them, so nothing left in the system could break it.
        """
        seen = {match_id}
        current = match_id
        while True:
            row = con.execute(
                "SELECT superseded_by FROM match WHERE id=?", (current,)
            ).fetchone()
            if row is None or row[0] is None:
                return current
            nxt = int(row[0])
            if nxt in seen:
                return current
            seen.add(nxt)
            current = nxt

    def _resolve_local_clash(
        self, con: sqlite3.Connection, local_id: int, clash_id: int
    ) -> bool:
        """Settle two LOCAL rows competing for one canonical key.

        Exactly one row may hold the key, and it must be the one that is still
        active - `natural_key` is UNIQUE here, and a key parked on a superseded row
        means the surviving occurrence is unrecognisable on the next pull.

        Args:
            con: The open transaction. Both writes belong to it.
            local_id: The row currently adopting the server's key.
            clash_id: The local row already holding it.

        Returns:
            True when `local_id` may take the key, False when it must stay unkeyed
            because another row is the root of its occurrence.
        """
        root = self._supersession_root(con, clash_id)
        if root != local_id:
            # Point at the ROOT, never at the clashing row itself: a link into the
            # middle of a chain is what review round 1 found could close a cycle.
            con.execute("UPDATE match SET superseded_by=? WHERE id=?", (root, local_id))
            return False
        # We ARE the root and a row below us holds the key. Move it up in this same
        # transaction - assigning it while the other row still holds it raises.
        con.execute("UPDATE match SET natural_key=NULL WHERE id=?", (clash_id,))
        return True

    def retire_for_tombstone(self, match_id: int) -> bool:
        """Give up a server identity the pool has retired.

        All three push-gate fields are cleared TOGETHER. Clearing `natural_key`
        alone leaves `pushed_at` set and the row is never re-sent; clearing both but
        leaving `push_rejected_reason` is the same dead end, because
        `pushable_matches` requires all three.

        A row already superseded LOCALLY is skipped. It is not the active member of
        its occurrence - the root is, and the root re-pushes on its behalf - so
        reopening it would restart the push/retire cycle on every pull.

        A SYNCED row is deleted instead of cleared. It is not our evidence; it is a
        copy of a match the server has now folded into another, which arrives under
        the surviving key on this same pull. Keeping it unkeyed would leave a
        permanent duplicate in the fit with nothing left to dedupe it by.

        Returns:
            True when the row was retired, False when it was missing or skipped.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT origin, superseded_by FROM match WHERE id=?", (match_id,)
            ).fetchone()
            if row is None or row[1] is not None:
                return False
            if str(row[0]) == "synced":
                con.execute("DELETE FROM match WHERE id=?", (match_id,))
                return True
            con.execute(
                "UPDATE match SET natural_key=NULL, pushed_at=NULL,"
                " push_rejected_reason=NULL WHERE id=?",
                (match_id,),
            )
        return True

    def mark_push_rejected(self, local_id: int, reason: str) -> None:
        """A row the server refuses is not a failed push - retrying is pointless."""
        with self._connect() as con:
            con.execute(
                "UPDATE match SET push_rejected_reason=? WHERE id=?", (reason, local_id)
            )

    def upsert_synced(self, row: dict) -> int | None:
        """Insert a pulled match, or do nothing if we already have it.

        Remote ids are meaningless here - `match.id` is a per-database
        autoincrement - so hero rows are remapped onto the LOCAL id.
        """
        with self._connect() as con:
            existing = con.execute(
                "SELECT id FROM match WHERE natural_key=?", (row["natural_key"],)
            ).fetchone()
            if existing is not None:
                return None
            theme_id = None
            if row.get("theme_slug"):
                t = con.execute(
                    "SELECT id FROM theme WHERE slug=?", (row["theme_slug"],)
                ).fetchone()
                theme_id = t[0] if t else None
            cur = con.execute(
                "INSERT INTO match(natural_key, source, captured_at, theme,"
                " theme_id, theme_resolved_by, outcome, outcome_source,"
                " left_player, left_rating, left_rank,"
                " right_player, right_rating, right_rank,"
                " origin, contributor_uuid, remote_received_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'synced', ?, ?)",
                (
                    row["natural_key"], row["source"], row["captured_at"],
                    None, theme_id, row.get("theme_resolved_by"),
                    row["outcome"], "synced",
                    row.get("left_player"), row.get("left_rating"), row.get("left_rank"),
                    row.get("right_player"), row.get("right_rating"),
                    row.get("right_rank"),
                    row.get("contributor_uuid"), row.get("remote_received_at"),
                ),
            )
            match_id = int(cur.lastrowid)
            for h in row.get("heroes", []):
                con.execute(
                    "INSERT INTO match_hero(match_id, side, slot, hero_slug, status,"
                    " stat_sword, stat_heart, stat_shield)"
                    # status is NOT NULL locally but the server carries no scores
                    # or geometry - that is THIS machine's evidence. A pulled hero
                    # is identified by definition: the server only accepts
                    # complete matches.
                    " VALUES(?,?,?,?, 'identified', ?,?,?)",
                    (match_id, h["side"], h["slot"], h["hero_slug"],
                     h.get("stat_sword"), h.get("stat_heart"), h.get("stat_shield")),
                )
        return match_id

    def adopt_theme_windows(self, themes: list[dict]) -> int:
        """Fill in theme boundaries this install does not have. Returns how many.

        Only NULL is filled. A boundary already recorded here is never overwritten,
        because that would silently re-file matches already attributed to a theme - and
        this install may have observed a rotation the pool has not been told about yet.

        A theme the pool knows and this install does not is INSERTED, so a new theme
        arriving mid-event does not have to wait for a release.
        """
        filled = 0
        with self._connect() as con:
            for row in themes:
                slug = row.get("slug")
                if not slug:
                    continue
                event = con.execute(
                    "SELECT id FROM event WHERE slug=?",
                    (row.get("event_slug") or EVENT_SLUG,),
                ).fetchone()
                if event is None:
                    continue
                event_id = int(event[0])
                con.execute(
                    "INSERT OR IGNORE INTO theme"
                    "(event_id,slug,name,starts_at,ends_at,is_default)"
                    " VALUES(?,?,?,?,?,?)",
                    (
                        event_id,
                        slug,
                        row.get("name") or slug,
                        _as_stamp(row.get("starts_at")),
                        _as_stamp(row.get("ends_at")),
                        1 if row.get("is_default") else 0,
                    ),
                )
                for column in ("starts_at", "ends_at"):
                    value = _as_stamp(row.get(column))
                    if not value:
                        continue
                    changed = con.execute(
                        f"UPDATE theme SET {column}=?"
                        f" WHERE event_id=? AND slug=? AND {column} IS NULL",
                        (value, event_id, slug),
                    ).rowcount
                    filled += max(changed, 0)
        return filled

    def refile_default_themes(self) -> int:
        """Re-resolve matches that landed on the default theme. Returns how many moved.

        A match resolved BY A WINDOW was attributed against a boundary somebody
        confirmed, and is left alone. A match resolved by default was never attributed at
        all - it is what a client stores when it does not know the window yet, and once
        the window arrives it can be filed properly.

        This is the self-heal half of pooling the windows. Without it, learning a
        boundary fixes only future matches and leaves the gap permanently.
        """
        moved = 0
        with self._connect() as con:
            rows = con.execute(
                "SELECT m.id, m.captured_at FROM match m"
                " JOIN theme t ON t.id = m.theme_id"
                " WHERE m.theme_resolved_by='default' OR t.is_default=1"
            ).fetchall()
        for match_id, captured_at in rows:
            event_id, theme_id, how = self.resolve_theme(captured_at)
            if not theme_id or how != "window":
                continue
            with self._connect() as con:
                changed = con.execute(
                    "UPDATE match SET theme_id=?, event_id=?, theme_resolved_by=?"
                    " WHERE id=? AND theme_id != ?",
                    (theme_id, event_id, how, match_id, theme_id),
                ).rowcount
            moved += max(changed, 0)
        return moved

    def resolve_theme(
        self,
        captured_at: str,
        ocr_name: str | None = None,
        event_slug: str = EVENT_SLUG,
    ) -> tuple[int | None, int | None, str | None]:
        """Return (event_id, theme_id, resolved_by) for a capture, from the WINDOW.

        The dated window is the ONLY thing that decides a theme. A capture no
        window covers falls to the event default and is filed as explicitly
        unknown; it never falls back to the screen read.

        The OCR name is still recorded on the match, as a hint for backfilling a
        window later - it is just not allowed to decide anything. A screen read
        can be wrong in ways a clock cannot, and themes change roster balance, so
        a match filed under the WRONG theme silently corrupts the model while one
        filed under a vague default is visibly unknown and can be promoted later.

        `ocr_name` is accepted and ignored, so every call site keeps compiling
        and none of them can quietly reintroduce the fallback.
        """
        with self._connect() as con:
            row = con.execute(
                "SELECT id FROM event WHERE slug=?", (event_slug,)
            ).fetchone()
            if row is None:
                return None, None, None
            event_id = int(row[0])

            dated = con.execute(
                "SELECT id FROM theme WHERE event_id=?"
                " AND (starts_at IS NULL OR starts_at <= ?)"
                " AND (ends_at   IS NULL OR ends_at   >  ?)"
                " AND (starts_at IS NOT NULL OR ends_at IS NOT NULL)"
                " ORDER BY starts_at IS NULL, starts_at DESC LIMIT 1",
                (event_id, captured_at, captured_at),
            ).fetchone()
            if dated is not None:
                return event_id, int(dated[0]), "window"

            fallback = con.execute(
                "SELECT id FROM theme WHERE event_id=? AND is_default=1", (event_id,)
            ).fetchone()
            return event_id, (int(fallback[0]) if fallback else None), "default"

    def sibling_theme_ids(self, theme_id: int | None) -> tuple[int, ...]:
        """Other theme ids whose modifiers are identical to `theme_id`'s.

        Names are the join, because a name is what a person can check against the
        in-game Themes screen and an id means nothing to anyone. Returns an empty tuple
        for an unknown theme or one in no group, which makes the fit behave exactly as
        it did before groups existed.
        """
        if theme_id is None:
            return ()
        with self._connect() as con:
            row = con.execute(
                "SELECT name FROM theme WHERE id=?", (theme_id,)
            ).fetchone()
            if row is None or row[0] is None:
                return ()
            names = themes_sharing_modifiers(str(row[0]))
            if len(names) <= 1:
                return ()
            placeholders = ",".join("?" for _ in names)
            rows = con.execute(
                f"SELECT id FROM theme WHERE name IN ({placeholders}) AND id != ?",
                (*sorted(names), theme_id),
            ).fetchall()
        return tuple(int(r[0]) for r in rows)

    def _screen_id(self, con: sqlite3.Connection, slug: str) -> int:
        row = con.execute("SELECT id FROM screen WHERE slug=?", (slug,)).fetchone()
        if row is None:
            raise ValueError(f"unknown screen slug: {slug!r}")
        return int(row[0])

    def record_audit(self, row: AuditRow) -> int:
        """Persist one identification comparison and return its id."""
        self._check(row.side in _SIDES, f"invalid side: {row.side!r}")
        # agreed is DERIVED, never taken from the caller: the schema CHECK requires it to
        # be consistent with the slugs, and computing it here keeps that impossible to
        # get wrong at a call site.
        agreed = int(
            row.image_slug is not None
            and row.ocr_slug is not None
            and row.image_slug == row.ocr_slug
        )
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO identification_audit"
                "(match_id,screen_id,side,slot,image_slug,image_art_ref,image_score,"
                " image_margin,ocr_slug,agreed,frame_path,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.match_id, self._screen_id(con, row.screen_slug), row.side,
                    row.slot, row.image_slug, row.image_art_ref, row.image_score,
                    row.image_margin, row.ocr_slug, agreed, row.frame_path,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid or 0)

    def learn_transform(
        self,
        audit_id: int,
        screen_slug: str,
        hero_slug: str,
        art_ref: str,
        scale: float,
        score: float,
        margin: float,
        crop: tuple[int, int, int] | None = None,
    ) -> None:
        """Store tuned parameters, upserting on (screen, hero, art).

        The database triggers reject unconfirmed evidence; this raises ValueError rather
        than sqlite3.IntegrityError so callers get one exception type to handle.
        """
        half_w, top, bottom = crop if crop is not None else (None, None, None)
        try:
            with self._connect() as con:
                con.execute(
                    "INSERT INTO hero_screen_transform"
                    "(screen_id,hero_slug,art_ref,scale,crop_half_w,crop_top,"
                    " crop_bottom,score,margin,confirmed_by,audit_id,verified_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,'longpress_ocr',?,?)"
                    " ON CONFLICT(screen_id,hero_slug,art_ref) DO UPDATE SET"
                    "  scale=excluded.scale, crop_half_w=excluded.crop_half_w,"
                    "  crop_top=excluded.crop_top, crop_bottom=excluded.crop_bottom,"
                    "  score=excluded.score, margin=excluded.margin,"
                    "  audit_id=excluded.audit_id, verified_at=excluded.verified_at",
                    (
                        self._screen_id(con, screen_slug), hero_slug, art_ref, scale,
                        half_w, top, bottom, score, margin, audit_id,
                        datetime.now(UTC).isoformat(timespec="seconds"),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"refusing to learn a transform for {hero_slug!r} on {screen_slug!r}: "
                f"audit {audit_id} does not confirm it ({exc})"
            ) from exc

    def transform_for(self, screen_slug: str, hero_slug: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT t.scale,t.crop_half_w,t.crop_top,t.crop_bottom,t.score,t.margin"
                " FROM hero_screen_transform t JOIN screen s ON s.id=t.screen_id"
                " WHERE s.slug=? AND t.hero_slug=? ORDER BY t.margin DESC LIMIT 1",
                (screen_slug, hero_slug),
            ).fetchone()
        if row is None:
            return None
        keys = ("scale", "crop_half_w", "crop_top", "crop_bottom", "score", "margin")
        return dict(zip(keys, row, strict=True))

    def audit_agreement_rate(self, screen_slug: str) -> tuple[int, int]:
        """(agreed, total) for one screen - the false-positive rate's two halves."""
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(SUM(a.agreed),0), COUNT(*)"
                " FROM identification_audit a JOIN screen s ON s.id=a.screen_id"
                " WHERE s.slug=?",
                (screen_slug,),
            ).fetchone()
        return int(row[0]), int(row[1])
