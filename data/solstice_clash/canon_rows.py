"""Canonical trio ordering, for the STANDALONE migration.

A byte-for-byte sibling of
`adb_auto_player/games/afk_journey/services/solstice/canon.py`, and deliberately so.
`migrate.py` runs as a script from the repo root and is loaded by path outside the
package in the shipped build, so it cannot import from `adb_auto_player` - and
`sys.path` surgery inside a migration that runs unattended on someone else's machine
is worse than a copy. `test_canon.py` pins the two together with an equivalence test.
"""

from __future__ import annotations

TRIO_SIZE = 3


def canonical_trios(by_side: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Return (trio_1, trio_2), each sorted, with trio_1 the lexicographically smaller.

    Args:
        by_side: Heroes grouped by 'left' and 'right'.

    Returns:
        The two trios in canonical order.

    Raises:
        ValueError: A side is not exactly three heroes, or a hero appears in both.
    """
    left = sorted(by_side.get("left") or [])
    right = sorted(by_side.get("right") or [])
    for name, trio in (("left", left), ("right", right)):
        if len(trio) != TRIO_SIZE:
            raise ValueError(f"{name} is not exactly three heroes: {trio}")
    overlap = set(left) & set(right)
    if overlap:
        raise ValueError(f"hero appears in both trios: {sorted(overlap)}")
    return (left, right) if left < right else (right, left)


def trio_index_for(side: str, trio_1: list[str], by_side: dict[str, list[str]]) -> int:
    """Which canonical trio number the heroes on `side` form.

    Args:
        side: 'left' or 'right'.
        trio_1: The canonical first trio.
        by_side: Heroes grouped by side.

    Returns:
        1 or 2.
    """
    return 1 if sorted(by_side[side]) == trio_1 else 2


def map_side_pair(left_value, right_value, left_is_trio: int) -> tuple:
    """Reorder a (left, right) measurement pair onto canonical trio order.

    Args:
        left_value: The value measured on the left.
        right_value: The value measured on the right.
        left_is_trio: Which canonical trio the left side is.

    Returns:
        (trio_1_value, trio_2_value).
    """
    if left_is_trio == 1:
        return (left_value, right_value)
    return (right_value, left_value)
