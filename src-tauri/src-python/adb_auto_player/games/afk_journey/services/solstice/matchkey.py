"""The canonical match identity, computed the same way as the server.

Two independent spectators watching one match must produce the same key, and so
must this client and the server. If the two algorithms drift, dedupe silently
stops working and the pool double-counts.

`natural_key` used to live here: sha256 over the outcome, both sorted sides and a
ten-minute UTC bucket. It was removed once every call site moved to `comps_key`,
and it had two defects that no amount of tuning fixed. It was
ORIENTATION-SENSITIVE, so one fight seen from the two sides produced two keys and
the pool stored it twice. And its time bucket SPLIT AT ITS BOUNDARIES - ids
1042/1044 are one match nine seconds apart across a ten-minute wall. `match.natural_key`
the COLUMN survives; it now only ever holds the identity the SERVER assigns.

Keep this in step with app/identity.py in the gameretro-adb-api repo.
"""

from __future__ import annotations

import hashlib


def is_complete(left_slugs: list[str], right_slugs: list[str], outcome: str) -> bool:
    """Only a complete match gets a key.

    Keying a half-read match would let it claim identity over the good version:
    the first submission wins under ON CONFLICT DO NOTHING, so a partial row
    could permanently shadow the complete one.
    """
    if outcome not in ("left", "right"):
        return False
    return len(left_slugs) == 3 and len(right_slugs) == 3


def comps_key(event_slug: str, side_a_slugs: list[str], side_b_slugs: list[str]) -> str:
    """Identity for a match: the event and its two hero trios, nothing else.

    NOT in the key, each for a measured reason:

    - The OUTCOME. Winner-first ordering survives a disagreement about which
      SIDE a trio sat on, but not a disagreement about which trio WON - and a
      misread panel tint is a failure mode on record. Sorting the trios against
      each other removes the outcome from identity entirely.
    - The TIME. A bucket splits at its boundaries: ids 1042/1044 are one match
      nine seconds apart on opposite sides of a ten-minute wall. Proximity is
      handled by a server-side lookup instead.
    - Player NAMES, ranks and ratings. Ranks are NULL on every row. Names are
      OCR-fragile in a SIDE-DEPENDENT way - profile art reads as `GAME` on one
      side and `GAMERETRO` on the other, and rows 1133/1136 read one player as
      `m` and `mn`. A field that reads differently per side is the worst
      possible component of a key whose whole purpose is to make both sides
      agree.
    - The THEME. It is resolved server-side from the capture window and can be
      backfilled later, which would change the key retroactively (see
      `identity.py`).

    Args:
        event_slug: The event this match belongs to.
        side_a_slugs: One side's hero slugs, any order.
        side_b_slugs: The other side's hero slugs, any order.

    Returns:
        `sha256:<hex>`.
    """
    a, b = sorted([",".join(sorted(side_a_slugs)), ",".join(sorted(side_b_slugs))])
    payload = f"{event_slug}|{a}|{b}"
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
