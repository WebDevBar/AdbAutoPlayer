"""Audit the LEFT-side (first-pick) bias and what it does to the betting rule.

Reads only the locked live predictions (`match.predicted_left`), so every number
here is out-of-sample by construction: the value stored was the call the model
made before the outcome existed.

Run:  uv run python scripts/solstice_side_audit.py
"""

import math
import os
import sqlite3
import sys

DB = os.environ.get(
    "SOLSTICE_DB",
    os.path.expanduser("~/.local/share/AdbAutoPlayer/solstice_clash/heroes.sqlite"),
)


def wilson(hits: int, n: int) -> tuple[float, float]:
    """95% Wilson interval, which behaves at small n where the normal one does not."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963985
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (100 * (centre - half), 100 * (centre + half))


def two_prop_z(h1: int, n1: int, h2: int, n2: int) -> tuple[float, float]:
    """Pooled two-proportion z test. Returns (z, two-sided p)."""
    if n1 == 0 or n2 == 0:
        return (0.0, 1.0)
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return (0.0, 1.0)
    z = (h1 / n1 - h2 / n2) / se
    return (z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))))


def line(label: str, hits: int, n: int, extra: str = "") -> None:
    if n == 0:
        print(f"{label:<44} n=0")
        return
    lo, hi = wilson(hits, n)
    print(
        f"{label:<44} n={n:<5} {100 * hits / n:5.1f}%  "
        f"[{lo:4.1f}-{hi:4.1f}]  {extra}"
    )


def main() -> int:
    if not os.path.exists(DB):
        print(f"no database at {DB}", file=sys.stderr)
        return 1
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute(
        """
        SELECT m.predicted_left AS p, m.outcome AS o, m.captured_at AS t,
               COALESCE(t2.name, m.theme, '?') AS theme
        FROM match m LEFT JOIN theme t2 ON t2.id = m.theme_id
        WHERE m.predicted_left IS NOT NULL AND m.outcome IN ('left', 'right')
        """
    ).fetchall()

    print(f"=== locked live predictions with an outcome: {len(rows)} ===\n")

    themes = sorted({r["theme"] for r in rows})
    for theme in themes:
        sub = [r for r in rows if r["theme"] == theme]
        hits = sum(_hit(r) for r in sub)
        leftw = sum(r["o"] == "left" for r in sub)
        line(f"{theme}: model", hits, len(sub))
        line(f"{theme}: always-LEFT on the same rows", leftw, len(sub))
        z, pv = two_prop_z(hits, len(sub), leftw, len(sub))
        print(f"{'':44} model minus always-left: "
              f"{100 * (hits - leftw) / len(sub):+.1f} pts\n")

    print("=== by which side the model favours (all confidences) ===")
    for theme in themes:
        for side, keep in (("LEFT", True), ("RIGHT", False)):
            sub = [r for r in rows
                   if r["theme"] == theme and (r["p"] >= 0.5) == keep]
            if not sub:
                continue
            line(f"{theme} favours {side}", sum(_hit(r) for r in sub), len(sub))
    print()

    print("=== candidate rules, Flourishing Wilds only ===")
    fw = [r for r in rows if r["theme"] == "Flourishing Wilds"]
    _rules(fw)
    print("\n=== candidate rules, Converging Paths only (does it hold up?) ===")
    cp = [r for r in rows if r["theme"] == "Converging Paths"]
    _rules(cp)

    # GROUP BY t.name, never the output alias: SQLite resolves a GROUP BY name
    # against the INPUT columns first, and `match` has its own `theme` column that is
    # empty on every synced row. Our own uuid is excluded because the pool echoes our
    # pushed rows back to us as `synced`. Both caught by review, 2026-07-31.
    print("\n=== the confound check: side bias per EXTERNAL contributor, same theme ===")
    for r in con.execute(
        """
        SELECT COALESCE(t.name, '?') theme, m.origin,
               COALESCE(m.contributor_uuid, 'local') who,
               COUNT(*) n, SUM(m.outcome = 'left') L
        FROM match m LEFT JOIN theme t ON t.id = m.theme_id
        WHERE m.outcome IN ('left', 'right')
          AND (m.contributor_uuid IS NULL
               OR m.contributor_uuid NOT IN (SELECT instance_uuid FROM install))
        GROUP BY t.name, m.origin, who HAVING n >= 20 ORDER BY t.name, n DESC
        """
    ):
        line(f"{r['theme']} / {r['origin']} / {r['who'][:8]}", r["L"], r["n"])

    loc = con.execute(
        "SELECT COUNT(*) n, SUM(outcome='left') L FROM match "
        "WHERE theme_id=4 AND origin='local' AND outcome IN ('left','right')"
    ).fetchone()
    rem = con.execute(
        "SELECT COUNT(*) n, SUM(outcome='left') L FROM match "
        "WHERE theme_id=4 AND origin='synced' AND outcome IN ('left','right') "
        "AND contributor_uuid NOT IN (SELECT instance_uuid FROM install)"
    ).fetchone()
    z, pv = two_prop_z(loc["L"], loc["n"], rem["L"], rem["n"])
    print(f"\nlocal vs synced left-rate on Flourishing Wilds: z={z:.2f} p={pv:.4f}")
    print("  If p is small the left bias is a property of OUR capture path, not the")
    print("  theme. It stays exploitable for us either way, but it will not")
    print("  generalise and it may not survive the rotation.")
    return 0


def _hit(r) -> bool:
    return (r["p"] >= 0.5) == (r["o"] == "left")


def _rules(rows: list) -> None:
    if not rows:
        print("  (no rows)")
        return
    for thr in (0.50, 0.56, 0.58, 0.60, 0.62):
        both = [r for r in rows if max(r["p"], 1 - r["p"]) >= thr]
        leftonly = [r for r in both if r["p"] >= 0.5]
        line(f"  bet either side  >= {thr:.2f}", sum(_hit(r) for r in both), len(both))
        line(f"  bet LEFT only    >= {thr:.2f}",
             sum(_hit(r) for r in leftonly), len(leftonly))


if __name__ == "__main__":
    raise SystemExit(main())
