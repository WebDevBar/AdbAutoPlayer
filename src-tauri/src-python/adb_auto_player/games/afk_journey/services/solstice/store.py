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

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

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

    # Databases whose views this process has already ensured. Ensuring is idempotent and
    # costs about a millisecond, but doing it on every connection would run it thousands
    # of times a session for no benefit.
    _views_ensured: ClassVar[set[Path]] = set()

    def __init__(self, db_path: Path) -> None:
        self._db = Path(db_path)
        self._ensure_views()

    def _ensure_views(self) -> None:
        """Create the derived views if this database does not have them yet.

        Needed because a shipped build never runs `migrate.py`, and `solstice_db_path`
        returns an EXISTING user database untouched. A view added to the schema today
        would otherwise reach only the machine that ran the migration and no one else.
        `views.sql` is DROP-then-CREATE throughout, which makes this free to repeat and
        lets a changed definition reach an install that already has the old one.

        Failure is swallowed on purpose. A view is a convenience over data that is
        already there, and nothing that records a match depends on one. A mode refusing
        to start because a read-only helper could not be created would be a far worse
        bug than the missing helper.
        """
        if self._db in MatchStore._views_ensured:
            return
        MatchStore._views_ensured.add(self._db)
        sql = resource_file(Path("solstice_clash") / "views.sql")
        if sql is None or not sql.is_file():
            return
        try:
            with self._connect() as con:
                con.executescript(sql.read_text())
        except (sqlite3.Error, OSError) as exc:
            logging.debug(f"[SC-93] derived views not created: {exc}")

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
        """
        with self._connect() as con:
            return con.execute(
                "SELECT predicted_left, outcome, predicted_source, predicted_locked"
                "  FROM match"
                " WHERE predicted_left IS NOT NULL AND outcome IN ('left','right')"
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
                " ORDER BY m.id, h.side, h.slot"
            ).fetchall()

    def pull_cursor(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT pull_cursor FROM install WHERE id=1").fetchone()
        return int(row[0]) if row and row[0] else 0

    def set_pull_cursor(self, seq: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE install SET pull_cursor=? WHERE id=1", (str(seq),))

    def pushable_matches(self, limit: int = 500) -> list[dict]:
        """Rows this install may send.

        Three conditions, and every one of them matters:

        - `origin='local'` - never echo back rows we PULLED, or every contributor
          re-uploads everyone else's data forever.
        - `natural_key IS NOT NULL` - incomplete matches have no identity.
        - `pushed_at IS NULL` - per row, NOT a timestamp watermark. A match is
          inserted before its heroes are read, so it becomes syncable later than
          it was created; a watermark would leave it permanently behind.
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
                " WHERE origin='local' AND natural_key IS NOT NULL"
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
                        "event_slug": "solstice-clash",
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

        The collision branch is not an edge case. If another contributor already
        pushed this match and we already pulled it, a synced row is sitting on
        the canonical key and rewriting ours would violate UNIQUE. Keep OUR row -
        it is this install's own observation, with its own hero evidence - and
        drop the synced copy.
        """
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as con:
            clash = con.execute(
                "SELECT id FROM match WHERE natural_key=? AND id<>?",
                (natural_key, local_id),
            ).fetchone()
            if clash is not None:
                con.execute("DELETE FROM match WHERE id=?", (clash[0],))
            theme_id = None
            if theme_slug:
                row = con.execute(
                    "SELECT id FROM theme WHERE slug=?", (theme_slug,)
                ).fetchone()
                theme_id = row[0] if row else None
            con.execute(
                "UPDATE match SET natural_key=?, pushed_at=?,"
                " theme_id=COALESCE(?, theme_id),"
                " theme_resolved_by=COALESCE(?, theme_resolved_by)"
                " WHERE id=?",
                (natural_key, now, theme_id, theme_resolved_by, local_id),
            )

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
                    (row.get("event_slug") or "solstice-clash",),
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
        event_slug: str = "solstice-clash",
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
