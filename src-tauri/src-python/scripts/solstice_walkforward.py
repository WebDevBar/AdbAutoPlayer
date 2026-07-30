"""Walk-forward harness for the pre-registered Solstice Clash tests.

Replays every match against a model fitted ONLY on matches that preceded it, in the
same theme. Calls predict() the way the mixin does, band evidence included - omitting
it is the documented trap that produced a 78%-vs-61% discrepancy.
"""

import math
import os
import sqlite3
import sys
from collections import defaultdict

sys.path.insert(
    0,
    os.path.expanduser("~/Dev/webdevbar/adbautoplayer/src-tauri/src-python"),
)

from adb_auto_player.games.afk_journey.services.solstice import odds

DB = os.path.expanduser("~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite")

# A comp is three a side. A match missing a hero is dropped rather than padded, the same
# rule `load_matches` applies - a 2v3 would teach the model that two heroes beat three.
TEAM_SIZE = 3
EVEN = 0.5


def load():
    """Matches in capture order, per theme, with the columns matches_for_fit uses."""
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True)
    rows = con.execute(
        "SELECT m.id, m.outcome, m.theme_id, m.event_id,"
        "       m.left_player, m.right_player,"
        "       m.left_rating, m.right_rating,"
        "       h.side, h.hero_slug"
        "  FROM match m JOIN match_hero h ON h.match_id = m.id"
        " WHERE m.outcome IN ('left','right') AND h.hero_slug IS NOT NULL"
        " ORDER BY m.id, h.side, h.slot"
    ).fetchall()
    order = {
        mid: (ts, name)
        for mid, ts, name in con.execute(
            "SELECT m.id, m.captured_at, t.name FROM match m"
            " JOIN theme t ON t.id = m.theme_id"
        ).fetchall()
    }
    # Rebuild with match ids attached: load_matches drops the id, the walk needs it.
    grouped = defaultdict(lambda: {"left": [], "right": []})
    meta = {}
    for mid, outcome, theme_id, event_id, lp, rp, lr, rr, side, slug in rows:
        meta[mid] = (outcome, theme_id, event_id, lp, rp, lr, rr)
        if side in ("left", "right"):
            grouped[mid][side].append(slug)
    out = []
    for mid, sides in grouped.items():
        if len(sides["left"]) != TEAM_SIZE or len(sides["right"]) != TEAM_SIZE:
            continue
        outcome, theme_id, event_id, lp, rp, lr, rr = meta[mid]
        m = odds.Match(
            left=tuple(sides["left"]),
            right=tuple(sides["right"]),
            left_won=outcome == "left",
            theme_id=theme_id,
            left_player=lp,
            right_player=rp,
            left_rating=lr,
            right_rating=rr,
            event_id=event_id,
        )
        ts, theme_name = order[mid]
        out.append((ts, theme_name, mid, m))
    out.sort(key=lambda x: (x[1], x[0], x[2]))
    return out


def walk(rows, use_evidence=True, nudge=None, min_train=None):
    """Predict each match from the ones before it in the same theme.

    Returns a list of (theme, match_id, p_left, left_won, gap).
    """
    if nudge is not None:
        original = odds.RATING_NUDGE
        odds.RATING_NUDGE = nudge
    if min_train is None:
        min_train = odds.MIN_MATCHES_FOR_ODDS

    results = []
    by_theme = defaultdict(list)
    for ts, theme, mid, m in rows:
        by_theme[theme].append((mid, m))

    try:
        for theme, items in by_theme.items():
            history = []
            fitted = None
            for i, (mid, m) in enumerate(items):
                if len(history) >= min_train:
                    # Refit every match: this is the honest walk-forward cost.
                    fitted = odds.fit(history, theme_id=m.theme_id)
                    ev = (
                        odds.band_evidence(history, event_id=m.event_id)
                        if use_evidence
                        else None
                    )
                    p = odds.predict(
                        fitted,
                        list(m.left),
                        list(m.right),
                        left_rating=m.left_rating,
                        right_rating=m.right_rating,
                        evidence=ev,
                    )
                    gap = (
                        None
                        if m.left_rating is None or m.right_rating is None
                        else m.left_rating - m.right_rating
                    )
                    results.append((theme, mid, p.p_mid, m.left_won, gap))
                history.append(m)
    finally:
        if nudge is not None:
            odds.RATING_NUDGE = original
    return results


def score(results, floor=0.0, theme=None, gap_min=None):
    """Hits, total, and hit rate for the calls clearing `floor`.

    Args:
        results: rows from `walk`.
        floor: minimum confidence, as max(p, 1-p).
        theme: restrict to one theme name.
        gap_min: restrict to matches whose absolute rating gap is at least this.

    Returns:
        (hits, n, fraction).
    """
    sel = results
    if theme:
        sel = [r for r in sel if r[0] == theme]
    if gap_min is not None:
        sel = [r for r in sel if r[4] is not None and abs(r[4]) >= gap_min]
    sel = [r for r in sel if max(r[2], 1 - r[2]) >= floor]
    if not sel:
        return 0, 0, 0.0
    hit = sum(1 for r in sel if (r[2] > EVEN) == r[3])
    return hit, len(sel), hit / len(sel)


def logloss(results):
    """Mean negative log likelihood - the scoring rule a threshold table cannot game."""
    if not results:
        return float("nan")
    total = 0.0
    for _, _, raw, won, _ in results:
        p = min(max(raw, 1e-9), 1 - 1e-9)
        total += -(math.log(p) if won else math.log(1 - p))
    return total / len(results)


if __name__ == "__main__":
    rows = load()
    print(f"loaded {len(rows)} complete matches")
    for theme in sorted({r[1] for r in rows}):
        print(f"  {theme}: {sum(1 for r in rows if r[1] == theme)}")
