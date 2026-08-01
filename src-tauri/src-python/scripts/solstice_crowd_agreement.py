"""Does agreeing with the crowd improve our calls, and does spectator count matter?

The grid is model-confidence x spectator-floor x {all, agree, disagree}. That is 90
cells, which is exactly the shape that manufactures a spurious threshold - so the
selection audit is not an optional extra here, it is the point. Two are run:

  1. A permutation null in which the CROWD's favoured side is shuffled across
     matches. This kills the crowd's information while leaving our own predictions,
     the outcomes, and the spectator distribution untouched. If the best observed
     "agreeing helps" cell is routinely matched by shuffled crowds, agreement is
     worth nothing however good the raw cell looks.
  2. Choose the best cell on one theme, apply that exact cell to the other.

Run:  python3 scripts/solstice_crowd_agreement.py
"""

import math
import os
import random
import sqlite3
import sys

DB = os.environ.get(
    "SOLSTICE_DB",
    os.path.expanduser("~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite"),
)

THRESHOLDS = (0.52, 0.54, 0.56, 0.58, 0.60, 0.62)
SPECTATOR_FLOORS = (0, 50, 100, 150, 200)
PERMUTATIONS = 5000
SEED = 20260731
MIN_CELL = 20  # a cell smaller than this is not evidence of anything


def wilson(hits: int, n: int) -> tuple[float, float]:
    """95% Wilson interval."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963985
    p = hits / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


class Row:
    """One match: our call, the crowd's call, the crowd's size, the result."""

    __slots__ = ("conf", "crowd_left", "left_won", "our_left", "spectators")

    def __init__(self, p: float, crowd_left: bool, spectators: int, left_won: bool):
        self.conf = max(p, 1 - p)
        self.our_left = p >= 0.5
        self.crowd_left = crowd_left
        self.spectators = spectators
        self.left_won = left_won

    @property
    def hit(self) -> bool:
        return self.our_left == self.left_won

    def agrees(self, crowd_left: bool | None = None) -> bool:
        side = self.crowd_left if crowd_left is None else crowd_left
        return self.our_left == side


def load(con: sqlite3.Connection, theme_id: int) -> list[Row]:
    """Locked predictions for one theme, joined to the crowd sample.

    Ties in the pool are dropped: the crowd has expressed no side there.
    """
    sql = """
        -- Everything is stated relative to trio 1 now: the prediction, the outcome
        -- and the pools all share that frame, so "agreement" means the same thing it
        -- always did without needing a side.
        SELECT m.predicted_trio_1 p,
               CASE WHEN m.winning_trio = 1 THEN 'left' ELSE 'right' END o,
               d.trio_1_pool lp, d.trio_2_pool rp, d.spectators s
        FROM match m JOIN match_odds d ON d.match_id = m.id
        WHERE m.theme_id = ? AND m.predicted_trio_1 IS NOT NULL
          AND m.winning_trio IS NOT NULL
          AND m.superseded_by IS NULL AND m.canonical_state = 'canonical'
          AND d.trio_1_pool IS NOT NULL AND d.trio_2_pool IS NOT NULL
          AND d.trio_1_pool <> d.trio_2_pool AND d.spectators IS NOT NULL
    """
    return [
        Row(r["p"], r["lp"] > r["rp"], r["s"], r["o"] == "left")
        for r in con.execute(sql, (theme_id,))
    ]


def cell(
    rows: list[Row], thr: float, floor: int, mode: str, crowd: list[bool] | None = None
) -> tuple[int, int]:
    """Hits and n for one grid cell. `crowd` overrides the crowd side (permutation)."""
    hits = n = 0
    for i, r in enumerate(rows):
        if r.conf < thr or r.spectators < floor:
            continue
        if mode != "all":
            agrees = r.agrees(None if crowd is None else crowd[i])
            if (mode == "agree") != agrees:
                continue
        n += 1
        hits += r.hit
    return hits, n


def grid(rows: list[Row], label: str) -> None:
    """Print the full grid."""
    print(f"\n=== {label} (n={len(rows)}) ===")
    print(f"{'conf':>5} {'spec':>5} | {'all':>17} | {'agree':>17} | {'disagree':>17}")
    for thr in THRESHOLDS:
        for floor in SPECTATOR_FLOORS:
            parts = []
            for mode in ("all", "agree", "disagree"):
                h, n = cell(rows, thr, floor, mode)
                parts.append(
                    f"{n:>4} {100 * h / n:5.1f}%" + ("*" if n < MIN_CELL else " ")
                    if n
                    else f"{'-':>11}"
                )
            print(
                f"{thr:>5.2f} {floor:>5} | {parts[0]:>17} | "
                f"{parts[1]:>17} | {parts[2]:>17}"
            )
    print(f"  (* cell smaller than {MIN_CELL} - not evidence)")


def best_agreement_gain(
    rows: list[Row], crowd: list[bool] | None = None
) -> tuple[float, str]:
    """Largest accuracy gain from adding the agree filter, over the whole grid.

    The comparison is like-for-like: the same threshold and spectator floor, with
    and without the filter. Cells below MIN_CELL are ignored so the maximum is not
    just the noisiest corner.
    """
    best, where = -1.0, "none"
    for thr in THRESHOLDS:
        for floor in SPECTATOR_FLOORS:
            ah, an = cell(rows, thr, floor, "agree", crowd)
            bh, bn = cell(rows, thr, floor, "all", crowd)
            if an < MIN_CELL or bn < MIN_CELL:
                continue
            gain = ah / an - bh / bn
            if gain > best:
                best, where = gain, f"conf>={thr:.2f} spec>={floor} (n={an})"
    return best, where


def permutation_audit(rows: list[Row]) -> None:
    """Is the best agreement gain reachable by a crowd that knows nothing?"""
    observed, where = best_agreement_gain(rows)
    print(f"\nbest observed agreement gain: {observed:+.4f} at {where}")
    rng = random.Random(SEED)
    sides = [r.crowd_left for r in rows]
    atleast = 0
    for _ in range(PERMUTATIONS):
        rng.shuffle(sides)
        gain, _ = best_agreement_gain(rows, sides)
        atleast += gain >= observed
    p = (atleast + 1) / (PERMUTATIONS + 1)
    print(f"permutations = {PERMUTATIONS}, null >= observed = {atleast}, p = {p:.4f}")
    print("  p above ~0.05 means a crowd with no information reproduces this gain")
    print("  just as often - the cell is selection, not signal.")


def choose_test(a: list[Row], a_name: str, b: list[Row], b_name: str) -> None:
    """Pick the best agree-cell on one theme; apply that exact cell to the other.

    Selection is on the agreement GAIN, not on raw accuracy, and the test theme
    reports the gain too. An earlier version did neither, and it made a cell that
    reverses out of theme look like it survived: picking the highest raw accuracy
    just picks the highest-confidence cell, which is a property of the model rather
    than of the crowd. Caught by the second reviewer, 2026-07-31.
    """
    best, chosen = -1.0, None
    for thr in THRESHOLDS:
        for floor in SPECTATOR_FLOORS:
            ah, an = cell(a, thr, floor, "agree")
            bh, bn = cell(a, thr, floor, "all")
            if an < MIN_CELL or bn < MIN_CELL:
                continue
            gain = ah / an - bh / bn
            if gain > best:
                best, chosen = gain, (thr, floor, ah, an, bh, bn)
    if chosen is None:
        print(f"\nCHOOSE {a_name} -> TEST {b_name}: no cell reached {MIN_CELL}")
        return
    thr, floor, ah, an, bh, bn = chosen
    print(f"\nCHOOSE on {a_name}: agree, conf>={thr:.2f}, spec>={floor}")
    print(
        f"  agree {ah}/{an} = {100 * ah / an:.1f}%   "
        f"all {bh}/{bn} = {100 * bh / bn:.1f}%   gain {100 * best:+.1f} pts"
    )
    tah, tan = cell(b, thr, floor, "agree")
    tbh, tbn = cell(b, thr, floor, "all")
    if tan == 0 or tbn == 0:
        print(f"  TEST on {b_name}: no matching rows")
        return
    tgain = tah / tan - tbh / tbn
    lo, hi = wilson(tah, tan)
    print(
        f"  TEST on {b_name}: agree {tah}/{tan} = {100 * tah / tan:.1f}% "
        f"[{lo:.1f}-{hi:.1f}]   all {tbh}/{tbn} = {100 * tbh / tbn:.1f}%   "
        f"gain {100 * tgain:+.1f} pts"
    )


def main() -> int:
    if not os.path.exists(DB):
        print(f"no database at {DB}", file=sys.stderr)
        return 1
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    fw = load(con, 4)
    cp = load(con, 3)

    print("Crowd baseline: how often does the crowd alone pick the winner?")
    for rows, name in ((fw, "Flourishing Wilds"), (cp, "Converging Paths")):
        if not rows:
            continue
        h = sum(r.crowd_left == r.left_won for r in rows)
        lo, hi = wilson(h, len(rows))
        print(
            f"  {name:<20} {h}/{len(rows)} = {100 * h / len(rows):.1f}%  "
            f"[{lo:.1f}-{hi:.1f}]"
        )
        for floor in SPECTATOR_FLOORS[1:]:
            sub = [r for r in rows if r.spectators >= floor]
            if len(sub) < MIN_CELL:
                continue
            h = sum(r.crowd_left == r.left_won for r in sub)
            print(f"    spec>={floor:<4} {h}/{len(sub)} = {100 * h / len(sub):.1f}%")

    grid(fw, "Flourishing Wilds")
    grid(cp, "Converging Paths")

    print("\n=== SELECTION AUDIT: Flourishing Wilds ===")
    permutation_audit(fw)
    choose_test(fw, "Flourishing Wilds", cp, "Converging Paths")
    choose_test(cp, "Converging Paths", fw, "Flourishing Wilds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
