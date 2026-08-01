"""Canonical trio ordering, and the single validated write boundary.

WHICH trio is "1" must be a pure function of the heroes themselves, never of where
they appeared on screen, or two contributors watching the same match with the sides
swapped store it under different numbers and every pointer stops meaning what it says.

`data/solstice_clash/canon_rows.py` is a deliberate sibling copy of the three pure
functions here, because the standalone migration cannot import this package. The
equivalence test in `test_canon.py` is what keeps them honest.
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


def assert_canonical(rows: list[dict]) -> None:
    """Reject any hero set that could not have come off a real screen.

    Every write of `match_hero` goes through this. A constraint nobody has tried to
    breach is a comment, so each rule here has a test that breaches it.

    Canonical ORDERING is enforced here rather than in the schema because SQLite
    cannot express a cross-row comparison as a CHECK - and without it a writer can
    store the lexicographically larger trio as trio 1 while every other constraint
    passes, leaving `winning_trio`, `blue_trio` and every rating pointing at the
    other composition.

    Args:
        rows: The full hero set for one match, each with trio, slot, hero_slug.

    Raises:
        ValueError: On any violation, naming the rule.
    """
    identified = [r for r in rows if r.get("hero_slug")]
    for row in rows:
        if row.get("trio") not in (1, 2):
            raise ValueError(f"trio must be 1 or 2, got {row.get('trio')!r}")
        if row.get("slot") not in (1, 2, 3):
            raise ValueError(f"slot must be 1, 2 or 3, got {row.get('slot')!r}")

    seen_slots: set[tuple[int, int]] = set()
    for row in rows:
        key = (row["trio"], row["slot"])
        if key in seen_slots:
            raise ValueError(f"duplicate trio/slot {key}")
        seen_slots.add(key)

    slugs = [r["hero_slug"] for r in identified]
    if len(slugs) != len(set(slugs)):
        raise ValueError("a hero appears in both trios")

    grouped = {
        1: sorted(r["hero_slug"] for r in identified if r["trio"] == 1),
        2: sorted(r["hero_slug"] for r in identified if r["trio"] == 2),
    }
    # Only a COMPLETE match is checked for ordering. A partial read is a match still
    # in progress, not a contradiction.
    if len(grouped[1]) == TRIO_SIZE and len(grouped[2]) == TRIO_SIZE:
        if grouped[1] > grouped[2]:
            raise ValueError(
                f"trios are not canonically ordered: {grouped[1]} should not follow "
                f"{grouped[2]} - trio 1 is the lexicographically smaller composition"
            )
