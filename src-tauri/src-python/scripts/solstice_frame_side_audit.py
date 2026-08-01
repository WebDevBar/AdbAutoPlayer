"""Does our stored side agree with what the draft frame shows?

READ ONLY by default. It opens the database with `mode=ro` so a write raises rather
than being merely avoided, and its only outputs are a markdown report and a
machine-readable sidecar.

The draft screen is the only screen carrying true blue/red plates, so it is the only
thing able to adjudicate a summary-screen read. Every verdict here is this machine's
draft read against this machine's summary read; it says nothing about anybody else's
rows.

Run:  uv run python scripts/solstice_frame_side_audit.py --cutoff 2026-07-31T12:00:00Z
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2

from adb_auto_player.games.afk_journey.services.solstice.config import SolsticeConfig
from adb_auto_player.games.afk_journey.services.solstice.frameside import (
    TRIO_SIZE,
    Verdict,
    classify,
    read_frame_sides,
)
from adb_auto_player.games.afk_journey.services.solstice.icons import IconLibrary
from adb_auto_player.games.afk_journey.services.solstice.matchkey import comps_key
from adb_auto_player.games.afk_journey.services.solstice.paths import solstice_icon_dir

DEFAULT_DB = Path(
    os.environ.get(
        "SOLSTICE_DB",
        str(Path.home() / ".local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite"),
    )
)
DEFAULT_FRAMES = Path(
    os.environ.get("SOLSTICE_FRAME_DIR", "/mnt/vault/adbautoplayer/solstice-frames")
)
DEFAULT_EVENT_SLUG = "solstice-clash"
_FRAME_NAME = re.compile(r"^draft-(\d+)\.png$")

# The report's own thresholds, named so no bare literal ends up in a comparison.
_Z_95 = 1.959963985
_PREDICTION_THRESHOLD = 0.5
# The 2x2 against time of day needs a binary split, and midday UTC is the one cut that
# is not chosen after looking at the answer.
_LATE_HOUR_START = 12
# Two rows sharing a comps_key are the same six heroes in the same event: either a
# duplicate capture of one match or a genuine rematch. Either way it is the population
# the duplicate-pair evidence came from, so it gets its own 2x2.
_DUPLICATE_GROUP_MIN = 2


@dataclass(frozen=True)
class Row:
    """One frame's verdict on one match."""

    match_id: int
    verdict: Verdict
    one_sided: Verdict
    captured_at: str
    outcome: str | None
    row_left: tuple[str, ...]
    row_right: tuple[str, ...]
    frame_blue: tuple[str, ...]
    frame_red: tuple[str, ...]
    natural_key: str | None
    predicted_left: float | None
    left_rating: int | None
    right_rating: int | None
    has_duplicate: bool


def one_sided_verdict(
    frame_blue: frozenset[str],
    frame_red: frozenset[str],
    row_left: frozenset[str],
    row_right: frozenset[str],
) -> Verdict:
    """The verdict when only the BLUE trio came back complete.

    Measured, not assumed: on 914 of 916 saved frames the reader returns 3 blue and
    2 red. The missing cell is always plate 6, the LAST pick of the snake draft, which
    has not locked yet at the moment the frame is saved. `classify` therefore calls
    every one of them `unreadable`, and the audit adjudicates nothing at all.

    This rule is the weaker one that the available evidence does support: a COMPLETE
    blue trio matching one stored trio exactly, with the partial red read contained in
    the other, fixes the orientation on its own. The missing hero cannot change which
    way round the two trios sit.

    It is a DIAGNOSTIC. `classify` remains the rule of record, `Row.verdict` is what
    the repair acts on, and nothing here is authority to flip a row.

    Args:
        frame_blue: Blue trio read from the draft frame.
        frame_red: Red trio read from the draft frame, usually short by one.
        row_left: The row's `side='left'` slugs.
        row_right: The row's `side='right'` slugs.

    Returns:
        The one-sided verdict for this row.
    """
    if len(row_left) != TRIO_SIZE or len(row_right) != TRIO_SIZE:
        return Verdict.INCOMPLETE
    if len(frame_blue) != TRIO_SIZE:
        return Verdict.UNREADABLE
    if frame_blue == row_left and frame_red <= row_right:
        return Verdict.AGREE
    if frame_blue == row_right and frame_red <= row_left:
        return Verdict.MIRRORED
    return Verdict.PARTIAL


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the database so that writes are impossible.

    Args:
        db_path: Path to heroes.sqlite.

    Returns:
        A read-only connection.
    """
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def summarise(verdicts: list[Verdict]) -> Counter:
    """Count each verdict, including the ones that did not occur.

    Args:
        verdicts: One verdict per audited frame.

    Returns:
        A counter carrying every member of `Verdict`, zero included.
    """
    counts: Counter = Counter({member: 0 for member in Verdict})
    counts.update(verdicts)
    return counts


def wilson(hits: int, total: int) -> tuple[float, float]:
    """95% Wilson interval, in percent, which behaves at small n.

    Args:
        hits: Successes.
        total: Trials.

    Returns:
        `(low, high)` as percentages.
    """
    if total == 0:
        return (0.0, 0.0)
    proportion = hits / total
    denom = 1 + _Z_95 * _Z_95 / total
    centre = (proportion + _Z_95 * _Z_95 / (2 * total)) / denom
    half = (
        _Z_95
        * math.sqrt(
            proportion * (1 - proportion) / total + _Z_95 * _Z_95 / (4 * total * total)
        )
        / denom
    )
    return (100 * (centre - half), 100 * (centre + half))


def two_prop_z(hits_a: int, n_a: int, hits_b: int, n_b: int) -> tuple[float, float]:
    """Pooled two-proportion z test.

    Args:
        hits_a: Successes in group A.
        n_a: Trials in group A.
        hits_b: Successes in group B.
        n_b: Trials in group B.

    Returns:
        `(z, two-sided p)`.
    """
    if n_a == 0 or n_b == 0:
        return (0.0, 1.0)
    pooled = (hits_a + hits_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se == 0:
        return (0.0, 1.0)
    z = (hits_a / n_a - hits_b / n_b) / se
    return (z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))))


def parse_timestamp(value: str) -> datetime:
    """Parse a stored ISO-8601 timestamp, tolerating the `Z` suffix.

    Both forms are present in the live corpus - `...11Z` on the earliest rows and
    `...51+00:00` on the newest - so a string comparison against a cutoff would sort
    them against each other incorrectly.

    Args:
        value: The stored timestamp.

    Returns:
        A timezone-aware datetime in UTC.
    """
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def frame_index(frame_dir: Path) -> dict[int, Path]:
    """Every saved draft frame that claimed a match id.

    `draft-pending-*.png` files are deliberately excluded: a frame that kept the
    pending name belongs to a match that was never recorded.

    Args:
        frame_dir: Directory of saved frames.

    Returns:
        Match id to frame path.
    """
    found: dict[int, Path] = {}
    for path in sorted(frame_dir.glob("draft-*.png")):
        match = _FRAME_NAME.match(path.name)
        if match is not None:
            found[int(match.group(1))] = path
    return found


def load_matches(
    con: sqlite3.Connection, cutoff: datetime
) -> dict[int, dict[str, Any]]:
    """Every match at or before the cutoff, with its two hero trios.

    Args:
        con: A read-only connection.
        cutoff: Rows captured after this are ignored, because both databases keep
            growing while the audit runs.

    Returns:
        Match id to a dict carrying the row's fields plus `left`/`right` slug tuples.
    """
    matches: dict[int, dict[str, Any]] = {}
    for row in con.execute(
        "SELECT m.id, m.natural_key, m.captured_at, m.outcome, m.origin, m.pushed_at,"
        " m.predicted_left, m.left_rating, m.right_rating,"
        " COALESCE(e.slug, ?) AS event_slug"
        " FROM match m LEFT JOIN event e ON e.id = m.event_id",
        (DEFAULT_EVENT_SLUG,),
    ):
        if parse_timestamp(row["captured_at"]) > cutoff:
            continue
        matches[row["id"]] = {
            "natural_key": row["natural_key"],
            "captured_at": row["captured_at"],
            "outcome": row["outcome"],
            "origin": row["origin"],
            "pushed_at": row["pushed_at"],
            "predicted_left": row["predicted_left"],
            "left_rating": row["left_rating"],
            "right_rating": row["right_rating"],
            "event_slug": row["event_slug"],
            "left": [],
            "right": [],
        }

    for hero in con.execute(
        "SELECT match_id, side, hero_slug FROM match_hero"
        " WHERE hero_slug IS NOT NULL ORDER BY match_id, side, slot"
    ):
        entry = matches.get(hero["match_id"])
        if entry is not None and hero["side"] in ("left", "right"):
            entry[hero["side"]].append(hero["hero_slug"])
    return matches


def duplicate_keys(matches: dict[int, dict[str, Any]]) -> set[str]:
    """Comps keys held by more than one match in the corpus.

    Args:
        matches: Every match under the cutoff.

    Returns:
        The set of comps keys that appear at least twice.
    """
    counts: Counter = Counter()
    for entry in matches.values():
        counts[_comps(entry)] += 1
    return {key for key, count in counts.items() if count >= _DUPLICATE_GROUP_MIN}


def _comps(entry: dict[str, Any]) -> str:
    """The orientation-free comps key for one stored row."""
    return comps_key(entry["event_slug"], entry["left"], entry["right"])


_WORKER: dict[str, Any] = {}


def _worker_init(db_path: str, icon_dir: str) -> None:
    """Build the geometry and icon library once per worker process."""
    cfg = SolsticeConfig.load(Path(db_path))
    _WORKER["cfg"] = cfg
    _WORKER["library"] = IconLibrary.build(cfg, Path(icon_dir))


def _read_frame(item: tuple[int, str]) -> tuple[int, list[str], list[str]]:
    """Read one frame's two trios. Returns empty sides when the file will not decode."""
    match_id, path = item
    frame = cv2.imread(path)
    if frame is None:
        return match_id, [], []
    blue, red = read_frame_sides(frame, _WORKER["cfg"], _WORKER["library"])
    return match_id, sorted(blue), sorted(red)


def audit(
    db_path: Path,
    frame_dir: Path,
    cutoff: datetime,
    workers: int = 1,
) -> list[Row]:
    """Classify every saved draft frame against its stored row.

    Args:
        db_path: heroes.sqlite, opened read-only.
        frame_dir: Where `draft-<match_id>.png` files live.
        cutoff: Ignore matches captured after this.
        workers: Worker processes for the icon matching, which dominates the runtime.

    Returns:
        One `Row` per audited frame, ordered by match id.
    """
    con = open_readonly(db_path)
    try:
        matches = load_matches(con, cutoff)
    finally:
        con.close()

    duplicates = duplicate_keys(matches)
    icon_dir = solstice_icon_dir()
    if icon_dir is None:
        raise RuntimeError("no bundled icon library found - cannot read a frame")

    work = [
        (match_id, str(path))
        for match_id, path in sorted(frame_index(frame_dir).items())
        if match_id in matches
    ]
    reads = _run_reads(work, db_path, icon_dir, workers)

    rows: list[Row] = []
    for match_id, blue, red in reads:
        entry = matches[match_id]
        row_left = tuple(sorted(entry["left"]))
        row_right = tuple(sorted(entry["right"]))
        sides = (
            frozenset(blue),
            frozenset(red),
            frozenset(row_left),
            frozenset(row_right),
        )
        verdict = classify(*sides)
        rows.append(
            Row(
                match_id=match_id,
                verdict=verdict,
                one_sided=one_sided_verdict(*sides),
                captured_at=entry["captured_at"],
                outcome=entry["outcome"],
                row_left=row_left,
                row_right=row_right,
                frame_blue=tuple(blue),
                frame_red=tuple(red),
                natural_key=(entry["natural_key"] if entry["pushed_at"] else None),
                predicted_left=entry["predicted_left"],
                left_rating=entry["left_rating"],
                right_rating=entry["right_rating"],
                has_duplicate=_comps(entry) in duplicates,
            )
        )
    return rows


def _run_reads(
    work: list[tuple[int, str]],
    db_path: Path,
    icon_dir: Path,
    workers: int,
) -> list[tuple[int, list[str], list[str]]]:
    """Read every frame, in this process or a pool of them."""
    if workers <= 1:
        _worker_init(str(db_path), str(icon_dir))
        return [_read_frame(item) for item in work]
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
        initargs=(str(db_path), str(icon_dir)),
    ) as pool:
        return list(pool.map(_read_frame, work, chunksize=4))


def adjudicable(rows: list[Row]) -> list[Row]:
    """The rows a frame actually decided: AGREE or MIRRORED, nothing else.

    Args:
        rows: Every audited row.

    Returns:
        Only the rows where both reads were complete and one orientation matched.
    """
    return [r for r in rows if r.verdict in (Verdict.AGREE, Verdict.MIRRORED)]


def adjudicable_one_sided(rows: list[Row]) -> list[Row]:
    """The rows the weaker blue-trio rule decided.

    Args:
        rows: Every audited row.

    Returns:
        Rows whose one-sided verdict is AGREE or MIRRORED.
    """
    return [r for r in rows if r.one_sided in (Verdict.AGREE, Verdict.MIRRORED)]


def _split_table(rows: list[Row], name: str, is_true, label: str) -> list[str]:
    """One 2x2 block: mirrored rate on each side of a binary split, with a z test."""
    group_a = [r for r in rows if is_true(r)]
    group_b = [r for r in rows if not is_true(r)]
    hits_a = sum(r.one_sided is Verdict.MIRRORED for r in group_a)
    hits_b = sum(r.one_sided is Verdict.MIRRORED for r in group_b)
    z, p = two_prop_z(hits_a, len(group_a), hits_b, len(group_b))
    return [
        f"**{name}**",
        "",
        "| group | n | mirrored | rate | 95% Wilson |",
        "|---|---:|---:|---:|---|",
        _split_row(label, hits_a, len(group_a)),
        _split_row(f"not {label}", hits_b, len(group_b)),
        "",
        f"Two-proportion z = {z:.3f}, p = {p:.4f}.",
        "",
    ]


def _split_row(label: str, hits: int, total: int) -> str:
    """One line of a 2x2 table."""
    if total == 0:
        return f"| {label} | 0 | 0 | - | - |"
    low, high = wilson(hits, total)
    return (
        f"| {label} | {total} | {hits} | {100 * hits / total:.1f}% |"
        f" {low:.1f}-{high:.1f}% |"
    )


def _accuracy(rows: list[Row], *, corrected: bool) -> tuple[int, int]:
    """Scored prediction accuracy, optionally flipping the outcome on mirrored rows.

    `predicted_left` is draft-relative and therefore already refers to the correct
    side; on a mirrored row it is the stored OUTCOME that is on the wrong side. So a
    mirrored row whose prediction looks wrong was in fact right.
    """
    hits = 0
    total = 0
    for row in rows:
        if row.predicted_left is None or row.outcome not in ("left", "right"):
            continue
        outcome = row.outcome
        if corrected and row.one_sided is Verdict.MIRRORED:
            outcome = "right" if outcome == "left" else "left"
        total += 1
        called_left = row.predicted_left >= _PREDICTION_THRESHOLD
        hits += called_left == (outcome == "left")
    return hits, total


def write_report(rows: list[Row], path: Path, cutoff: datetime) -> None:
    """Write the markdown report.

    Args:
        rows: Every audited row.
        path: Destination markdown file.
        cutoff: The cutoff the audit ran under.
    """
    counts = summarise([r.verdict for r in rows])
    decided = adjudicable(rows)
    mirrored = sum(r.verdict is Verdict.MIRRORED for r in decided)
    loose = adjudicable_one_sided(rows)
    loose_mirrored = sum(r.one_sided is Verdict.MIRRORED for r in loose)

    lines: list[str] = [
        f"# Side audit - {cutoff.date().isoformat()}",
        "",
        f"Cutoff `{cutoff.isoformat()}`. Frames audited: **{len(rows)}**.",
        "",
        "This compares **our summary-screen read** against **our draft-screen read**,"
        " on **one machine**. It cannot judge another contributor's rows, and a frame"
        " that could not be read is evidence about the frame reader, not about the"
        " summary reader - which is why `unreadable` is kept separate from `partial`.",
        "",
        "## Verdicts",
        "",
        "| verdict | n | share of audited |",
        "|---|---:|---:|",
    ]
    for member in Verdict:
        share = 100 * counts[member] / len(rows) if rows else 0.0
        lines.append(f"| {member.value} | {counts[member]} | {share:.1f}% |")

    low, high = wilson(mirrored, len(decided))
    rate = 100 * mirrored / len(decided) if decided else 0.0
    lines += [
        "",
        f"**Mirrored rate on the {len(decided)} adjudicated rows"
        f" (agree + mirrored): {rate:.2f}% [{low:.2f}-{high:.2f}]**.",
        "",
        "A rate near 50% would mean the frame reader is broken - not that half the"
        " corpus is mirrored - because a reader that assigns sides at random produces"
        " exactly that.",
        "",
    ]
    lines += _completeness(rows)

    loose_low, loose_high = wilson(loose_mirrored, len(loose))
    loose_rate = 100 * loose_mirrored / len(loose) if loose else 0.0
    lines += [
        "## The one-sided check (diagnostic, NOT a verdict)",
        "",
        "The blue trio comes back complete even when plate 6 does not, and a complete"
        " blue trio matching one stored trio exactly - with the short red read"
        " contained in the other - already fixes the orientation. The missing hero"
        " cannot change which way round the two trios sit.",
        "",
        "This is the weaker rule, kept separate on purpose. `classify` remains the rule"
        " of record and the repair acts on IT, so nothing below has flipped, or can"
        " flip, a row.",
        "",
        "| one-sided verdict | n |",
        "|---|---:|",
    ]
    loose_counts = summarise([r.one_sided for r in rows])
    for member in Verdict:
        lines.append(f"| {member.value} | {loose_counts[member]} |")
    lines += [
        "",
        f"**One-sided mirrored rate on {len(loose)} rows:"
        f" {loose_rate:.2f}% [{loose_low:.2f}-{loose_high:.2f}]**.",
        "",
        "## Is mirroring associated with anything?",
        "",
        "Every 2x2 below is computed on the ONE-SIDED adjudicated rows, because the"
        " rule of record adjudicated none.",
        "",
    ]
    lines += _split_table(
        loose, "Stored outcome", lambda r: r.outcome == "left", "outcome = left"
    )
    lines += _split_table(
        loose,
        "Hour of day (UTC)",
        lambda r: parse_timestamp(r.captured_at).hour >= _LATE_HOUR_START,
        f"captured at or after {_LATE_HOUR_START}:00",
    )
    lines += _split_table(
        loose,
        "Rating order",
        lambda r: (
            r.left_rating is not None
            and r.right_rating is not None
            and r.left_rating > r.right_rating
        ),
        "left_rating > right_rating",
    )
    lines += _split_table(
        loose, "Has a same-comps twin", lambda r: r.has_duplicate, "has a twin"
    )

    hits_now, total_now = _accuracy(rows, corrected=False)
    hits_fixed, total_fixed = _accuracy(rows, corrected=True)
    lines += [
        "## P1a: the accuracy correction",
        "",
        "`predicted_left` is draft-relative, so on a mirrored row the prediction is"
        " already on the correct side and it is the stored `outcome` that is flipped."
        " Scored accuracy is therefore **understated** while mirrored rows exist."
        " Computed on the one-sided classification, for the same reason as above.",
        "",
        "| scoring | n | hits | accuracy |",
        "|---|---:|---:|---:|",
        _accuracy_row("as stored", hits_now, total_now),
        _accuracy_row("with mirrored outcomes flipped", hits_fixed, total_fixed),
        "",
    ]

    lines += _detail_table(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _accuracy_row(label: str, hits: int, total: int) -> str:
    """One line of the accuracy table."""
    if total == 0:
        return f"| {label} | 0 | 0 | - |"
    return f"| {label} | {total} | {hits} | {100 * hits / total:.2f}% |"


def _completeness(rows: list[Row]) -> list[str]:
    """How many heroes each frame gave up, which is what explains the verdicts."""
    sizes: Counter = Counter((len(r.frame_blue), len(r.frame_red)) for r in rows)
    lines = [
        "## What the frame reader actually returned",
        "",
        "`classify` needs THREE on each side. This table is the reason the verdict"
        " table looks the way it does.",
        "",
        "| blue read | red read | frames |",
        "|---:|---:|---:|",
    ]
    for (blue, red), count in sorted(sizes.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {blue} | {red} | {count} |")
    lines += [
        "",
        "The missing cell is plate 6 - the LAST pick of the snake draft - which has"
        " not locked at the moment the frame is saved. It is a capture-timing defect,"
        " not a geometry or threshold one: the five cells that DO read come back at"
        " 0.94-0.98 while plate 6 sits at ~0.68 with a margin of ~0.002, which is what"
        " an empty plate looks like.",
        "",
    ]
    return lines


def _detail_table(rows: list[Row]) -> list[str]:
    """Every non-AGREE row, in full, so each verdict can be checked by hand.

    Keyed on the ONE-SIDED verdict: under the rule of record every row is
    `unreadable`, so a strict table would simply be the whole corpus reprinted.
    """
    interesting = [r for r in rows if r.one_sided is not Verdict.AGREE]
    lines = [
        "## Every non-agreeing row (one-sided)",
        "",
        f"{len(interesting)} rows.",
        "",
        "| match | verdict | captured | outcome | row left | row right |"
        " frame blue | frame red |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in sorted(interesting, key=lambda r: (r.one_sided.value, r.match_id)):
        lines.append(
            f"| {row.match_id} | {row.one_sided.value} | {row.captured_at} |"
            f" {row.outcome or '-'} | {', '.join(row.row_left) or '-'} |"
            f" {', '.join(row.row_right) or '-'} |"
            f" {', '.join(row.frame_blue) or '-'} |"
            f" {', '.join(row.frame_red) or '-'} |"
        )
    lines.append("")
    return lines


def write_sidecar(rows: list[Row], path: Path) -> None:
    """Write the machine-readable record, keyed by natural key.

    Keyed by `natural_key` rather than `match_id`: the server has no idea what our
    local ids mean, and this file is the only channel by which the server-side
    migration learns which orientation a frame confirmed. Rows never pushed carry
    `null`, so the file stays a complete record of what was audited and the migration
    ignores those.

    Args:
        rows: Every audited row.
        path: Destination JSON file.
    """
    payload = [
        {
            "natural_key": row.natural_key,
            "match_id": row.match_id,
            "verdict": row.verdict.value,
            "one_sided": row.one_sided.value,
            "frame_blue": list(row.frame_blue),
            "frame_red": list(row.frame_red),
        }
        for row in rows
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


_SENTINEL_SIDE = "__swap__"


def swap_sides(con: sqlite3.Connection, match_id: int) -> None:
    """Flip one match's hero sides and its outcome. Nothing else.

    Two phases because `match_hero` carries UNIQUE(match_id, side, slot) and SQLite
    checks constraints per ROW, not at statement end: a single CASE update collides with
    the row it has not moved yet. A sentinel side empties one value first.

    Deliberately untouched: `predicted_left` (draft-relative, so it already refers
    to the correct side and the repair is what brings `outcome` onto the same
    frame), player names (summary HEADER, read by x-position, side-correct), and
    ratings and ranks (draft-derived). Swapping any of them would move correct data
    onto the wrong side.

    Args:
        con: An open, writable connection.
        match_id: The match to flip.
    """
    con.execute(
        "UPDATE match_hero SET side=? WHERE match_id=? AND side='left'",
        (_SENTINEL_SIDE, match_id),
    )
    con.execute(
        "UPDATE match_hero SET side='left' WHERE match_id=? AND side='right'",
        (match_id,),
    )
    con.execute(
        "UPDATE match_hero SET side='right' WHERE match_id=? AND side=?",
        (match_id, _SENTINEL_SIDE),
    )
    con.execute(
        "UPDATE match SET outcome = CASE outcome WHEN 'left' THEN 'right'"
        " WHEN 'right' THEN 'left' ELSE outcome END WHERE id=?",
        (match_id,),
    )
    con.commit()


def snapshot(db_path: Path) -> Path:
    """Copy the database aside, and prove the copy is usable before returning.

    `sqlite3.Connection.backup` rather than a file copy: it is consistent against a
    database another process may be writing, which a `cp` is not. The copy is then
    opened and its schema compared against the original, because a snapshot nobody
    checked is not a snapshot - it is a file.

    Args:
        db_path: The database about to be modified.

    Returns:
        The snapshot path.

    Raises:
        RuntimeError: If the copy does not open or its schema differs.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
        original = _schema(source)
    finally:
        source.close()

    try:
        copy = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise RuntimeError(f"snapshot at {target} will not open: {exc}") from exc
    try:
        if _schema(copy) != original:
            raise RuntimeError(f"snapshot at {target} has a different schema")
    finally:
        copy.close()
    return target


def _schema(con: sqlite3.Connection) -> list[tuple]:
    """Every object in the schema, for comparing a snapshot against its source."""
    return sorted(
        tuple(row) for row in con.execute("SELECT type, name, sql FROM sqlite_master")
    )


def repair(db_path: Path, rows: list[Row], log_path: Path) -> int:
    """Snapshot, then flip every MIRRORED row. Nothing else is touched.

    `PARTIAL`, `UNREADABLE` and `INCOMPLETE` are deliberately left alone: none of them
    is evidence that the stored orientation is wrong, and repairing on a frame we could
    not read would put the reader's failures into the data.

    Args:
        db_path: The live database.
        rows: The audit's rows.
        log_path: Where to write the before/after log.

    Returns:
        The number of matches repaired.
    """
    targets = [r for r in rows if r.verdict is Verdict.MIRRORED]
    if not targets:
        return 0
    backup = snapshot(db_path)
    log = [f"snapshot: {backup}", f"repairing {len(targets)} matches", ""]
    con = sqlite3.connect(db_path)
    try:
        for row in targets:
            log.append(
                f"match {row.match_id}: outcome {row.outcome} ->"
                f" {'right' if row.outcome == 'left' else 'left'};"
                f" left [{', '.join(row.row_left)}] <-> right"
                f" [{', '.join(row.row_right)}]"
            )
            swap_sides(con, row.match_id)
    finally:
        con.close()
    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
    return len(targets)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cutoff",
        required=True,
        help="ISO-8601 UTC. Matches captured after this are ignored, because the"
        " database keeps growing while the audit runs.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--frames", type=Path, default=DEFAULT_FRAMES)
    parser.add_argument(
        "--out-dir", type=Path, default=Path("docs/solstice-clash"), dest="out_dir"
    )
    parser.add_argument(
        "--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2)
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Repair the MIRRORED rows. Off by default; the default run writes"
        " nothing and exits non-zero when there is anything to repair.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the audit, write the report and the sidecar, and report what it found.

    Args:
        argv: Command line arguments, or None for `sys.argv`.

    Returns:
        Process exit code. Non-zero on a dry run that found repairable rows, so the
        default invocation cannot be mistaken for a clean bill of health.
    """
    args = _parse_args(argv)
    if not args.db.is_file():
        print(f"no database at {args.db}", file=sys.stderr)
        return 2
    if not args.frames.is_dir():
        print(f"no frame directory at {args.frames}", file=sys.stderr)
        return 2

    cutoff = parse_timestamp(args.cutoff)
    rows = audit(args.db, args.frames, cutoff, workers=args.workers)
    if not rows:
        print("no frames matched a stored row under this cutoff", file=sys.stderr)
        return 2

    stem = f"side-audit-{cutoff.date().isoformat()}"
    report = args.out_dir / f"{stem}.md"
    write_report(rows, report, cutoff)
    write_sidecar(rows, args.out_dir / f"{stem}.json")

    counts = summarise([r.verdict for r in rows])
    decided = adjudicable(rows)
    mirrored = counts[Verdict.MIRRORED]
    rate = 100 * mirrored / len(decided) if decided else 0.0
    loose = adjudicable_one_sided(rows)
    loose_mirrored = sum(r.one_sided is Verdict.MIRRORED for r in loose)
    loose_counts = summarise([r.one_sided for r in rows])
    loose_rate = 100 * loose_mirrored / len(loose) if loose else 0.0
    print(f"{'verdict':<12} {'strict':>7} {'one-sided':>10}")
    for member in Verdict:
        print(f"{member.value:<12} {counts[member]:>7} {loose_counts[member]:>10}")
    print(f"\nadjudicated {len(decided)}, mirrored rate {rate:.2f}%")
    print(f"one-sided:  adjudicated {len(loose)}, mirrored rate {loose_rate:.2f}%")
    print(f"report:   {report}")
    print(f"sidecar:  {args.out_dir / f'{stem}.json'}")

    if not mirrored:
        return 0
    if not args.apply:
        print(
            f"\nDRY RUN: {mirrored} rows would be repaired. Nothing was written to the"
            " database. Re-run with --apply to repair them.",
            file=sys.stderr,
        )
        return 1
    repaired = repair(args.db, rows, args.db.with_name(f"{stem}-repair.log"))
    print(f"repaired {repaired} matches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
