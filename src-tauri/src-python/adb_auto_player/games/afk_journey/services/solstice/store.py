"""Match recording.

These tables are LOCALLY EARNED - `build_hero_db.py` never touches them. A wiki refresh
rebuilds only `hero_skill` and `solstice_roster`.

Two rules that come from real failures:

- **Every slot is stored, including unknown ones.** Dropping an unidentified slot would
  make a 3v3 look like a 2v3 and silently corrupt the training data.
- **The pool is stored, not just the picks.** Which heroes were *available and not
  picked* is a real signal, and a pick absent from the pool is a detected error.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MatchRecord:
    source: str  # 'compete' | 'spectate'
    captured_at: str
    natural_key: str | None = None  # NULL until the match has enough stable facts
    theme: str | None = None
    balance_epoch: str | None = None
    blue_player: str | None = None
    blue_rating: int | None = None
    blue_rank: int | None = None
    red_player: str | None = None
    red_rating: int | None = None
    red_rank: int | None = None
    outcome: str | None = None  # 'blue' | 'red' | 'draw' | None
    outcome_source: str | None = None


@dataclass(frozen=True)
class HeroSlot:
    side: str  # 'blue' | 'red'
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
    blue_pool: int | None = None
    red_pool: int | None = None
    blue_odds: float | None = None
    red_odds: float | None = None
    spectators: int | None = None


# Valid values. The schema documents these in comments only, so the store enforces them:
# without this a Phase 2 caller could persist source='comptee' or side='blu' silently.
_SOURCES = frozenset({"compete", "spectate"})
_SIDES = frozenset({"blue", "red"})
_HERO_STATUSES = frozenset({"identified", "unknown"})
_POOL_STATUSES = frozenset({"identified", "unknown", "banned"})
_OUTCOMES = frozenset({"blue", "red", "draw"})
POOL_SIZE = 20  # the draft grid is always 5x4


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
        "source",
        "captured_at",
        "theme",
        "balance_epoch",
        "blue_player",
        "blue_rating",
        "blue_rank",
        "red_player",
        "red_rating",
        "red_rank",
        "outcome",
        "outcome_source",
    )

    def __init__(self, db_path: Path) -> None:
        self._db = Path(db_path)

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
                "INSERT INTO match_odds(match_id,sampled_at,blue_pool,red_pool,"
                "blue_odds,red_odds,spectators) VALUES(?,?,?,?,?,?,?)",
                (
                    match_id,
                    sample.sampled_at,
                    sample.blue_pool,
                    sample.red_pool,
                    sample.blue_odds,
                    sample.red_odds,
                    sample.spectators,
                ),
            )

    def odds_for(self, match_id: int) -> list[OddsSample]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT sampled_at,blue_pool,red_pool,blue_odds,red_odds,spectators "
                "FROM match_odds WHERE match_id=? ORDER BY sampled_at",
                (match_id,),
            ).fetchall()
        return [OddsSample(*r) for r in rows]
