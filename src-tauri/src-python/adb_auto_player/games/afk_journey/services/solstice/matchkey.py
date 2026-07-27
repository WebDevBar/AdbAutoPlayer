"""The canonical match identity, computed the same way as the server.

Two independent spectators watching one match must produce the same key, and so
must this client and the server - the client computes a key locally for its own
ON CONFLICT dedupe, and the server computes the authoritative one. If the two
algorithms drift, dedupe silently stops working and the pool double-counts.

Keep this in step with app/identity.py in the gameretro-adb-api repo.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def is_complete(left_slugs: list[str], right_slugs: list[str], outcome: str) -> bool:
    """Only a complete match gets a key.

    Keying a half-read match would let it claim identity over the good version:
    the first submission wins under ON CONFLICT DO NOTHING, so a partial row
    could permanently shadow the complete one.
    """
    if outcome not in ("left", "right"):
        return False
    return len(left_slugs) == 3 and len(right_slugs) == 3


def natural_key(
    outcome: str,
    left_slugs: list[str],
    right_slugs: list[str],
    captured_at: datetime | str,
) -> str:
    """sha256 over outcome, both sorted hero sides, and a 10-minute UTC bucket.

    Theme is deliberately NOT included. It is derived from the capture time,
    which the time bucket already covers, and including it made the key change
    whenever a theme window was backfilled - breaking identity for every client
    that had already adopted the old key.

    Player names are excluded too: reads of `GAME` and a truncated `[kru` are on
    record, and a name that differs between two spectators would defeat the key.
    """
    if isinstance(captured_at, str):
        captured_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        # astimezone() on a naive value assumes SERVER LOCAL time, so identity
        # would depend on the machine's timezone.
        raise ValueError("captured_at must be timezone-aware")

    # TEN-MINUTE bucket. Was an hour until 2026-07-27; an hour is coarse enough
    # that two genuinely DIFFERENT matches with the same comps and outcome
    # collide and one is silently dropped, which loses real signal because the
    # same six heroes get placed differently on the field by different players.
    #
    # MUST match app/identity.py in gameretro-adb-api exactly. The server key is
    # the authoritative one; this local key exists only for local dedupe, and if
    # the two drift, the local backstop looks up a key that was never stored.
    # A pinned digest in both test suites makes that drift fail loudly.
    moment = captured_at.astimezone(UTC)
    bucket = f"{moment:%Y-%m-%dT%H}:{moment.minute // 10}"
    payload = "|".join(
        [
            outcome,
            ",".join(sorted(left_slugs)),
            ",".join(sorted(right_slugs)),
            bucket,
        ]
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
