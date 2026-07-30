"""Round 4, corrected after peer review. Every defect the reviewers found, fixed.

Three fixes:
  1. Rating evidence now pools across themes within the event, as production does.
  2. "No rating term" means evidence OFF and a zero table - not a zero prior that
     band evidence immediately refills.
  3. Curve comparisons hold the evidence handling FIXED, so a change to the nudge
     table cannot also re-partition the estimator that calibrates it.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from solstice_walkforward import load, logloss, score, walk

rows = load()
print(f"{len(rows)} complete matches\n")

HEADER = (
    f"{'variant':34} {'logloss':>8} {'all':>13} "
    f"{'>=0.56':>13} {'>=0.62':>13}   paired vs shipped"
)

GRADED = ((150, 0.124), (100, 0.0622), (0, 0.0))
FITTED = ((150, 0.21), (100, 0.11), (50, 0.08), (0, 0.02))
ZERO = ((0, 0.0),)


def per(res):
    """Per-match negative log likelihood, keyed by match id."""
    out = {}
    for _, mid, raw, won, _ in res:
        p = min(max(raw, 1e-9), 1 - 1e-9)
        out[mid] = -(math.log(p) if won else math.log(1 - p))
    return out


def paired(a, b):
    """Positive t means b is better than a."""
    ids = sorted(set(a) & set(b))
    d = [a[i] - b[i] for i in ids]
    m = sum(d) / len(d)
    v = sum((x - m) ** 2 for x in d) / (len(d) - 1)
    se = math.sqrt(v / len(d))
    return m, se, (m / se if se else 0.0), len(ids)


def row(label, res, ref=None):
    """One printed line: logloss, hit rates, and a paired t against `ref`."""
    line = f"{label:34} {logloss(res):8.4f}"
    for floor in (0.0, 0.56, 0.62):
        hit, n, pct = score(res, floor=floor)
        line += f" {str(hit) + '/' + str(n):>8}{pct * 100:4.0f}%"
    if ref is not None:
        _, _, t, _ = paired(ref, per(res))
        line += f"   t={t:+.2f}"
    print(line)


print("=" * 92)
print("A. DOES THE RATING TERM EARN ITS PLACE?  (evidence OFF - prior vs prior)")
print("=" * 92)
print(HEADER)
ship_off = walk(rows, use_evidence=False)
ref = per(ship_off)
row("shipped step (flat above 100)", ship_off)
for label, nudge in [
    ("rating term DELETED", ZERO),
    ("challenger (0.0622 any gap)", ((0, 0.0622),)),
    ("graded (pre-registered)", GRADED),
    ("fitted to round-4 bands", FITTED),
]:
    row(label, walk(rows, use_evidence=False, nudge=nudge), ref)

print()
print("=" * 92)
print("B. SAME, WITH BAND EVIDENCE ON (production path, cross-theme pooling fixed)")
print("=" * 92)
print(HEADER)
ship_on = walk(rows, use_evidence=True)
ref_on = per(ship_on)
row("shipped (band evidence)", ship_on)
for label, nudge in [
    ("graded (pre-registered)", GRADED),
    ("fitted to round-4 bands", FITTED),
]:
    row(label, walk(rows, use_evidence=True, nudge=nudge), ref_on)
row("no damping (prior only)", walk(rows, use_evidence=False), ref_on)

print()
print("=" * 92)
print("C. DOES BAND EVIDENCE HELP AT ALL, now that it pools across themes?")
print("=" * 92)
m, se, t, n = paired(per(walk(rows, use_evidence=False)), ref_on)
print(f"  prior-only vs band evidence: mean {m:+.5f} SE {se:.5f} t = {t:+.2f} (n={n})")
print("  positive t means BAND EVIDENCE is the better of the two")
