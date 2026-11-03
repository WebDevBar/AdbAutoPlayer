"""Help text for settings fields that carry no `description` in their model.

This lives CLI-side on purpose. `games/afk_journey/settings.py` is tracked from
upstream and merged regularly; a description added there would conflict on every
merge. A model that does carry a `description` always wins over anything here, so
the day upstream documents a field, this entry is simply ignored.

Keys are TOML aliases - what the file and the schema (built with `by_alias=True`)
call the field - not Python attribute names. `SECTION_HELP` overrides `FIELD_HELP`
where the same alias means something different in one section.
"""

FIELD_HELP: dict[str, str] = {
    "Attempts": (
        "Max battles with EACH formation before moving to the next one (not a total)."
    ),
    "Formations": (
        "How many suggested teams from the floor's Records to try; skipped "
        "entries count. Ignored when Suggested Formations is Off."
    ),
    "Suggested Formations": (
        "Copy teams from the floor's Records. Off = only use your current team."
    ),
    "Start with current Formation": (
        "Try your current team first, before any suggested team."
    ),
    "Skip Manual Formations": (
        "Skip suggested teams marked 'manual clear' (they usually fail on auto)."
    ),
    "Run Manual Formations Last": (
        "Try auto-clear suggestions first, manual-clear ones last. No effect if "
        "Skip Manual Formations is On."
    ),
    "Spend Gold": "Allow paying gold for extra attempts when free ones run out.",
}

SECTION_HELP: dict[tuple[str, str], str] = {
    # Ravaged Realm has no Formations setting and fights one battle per attempt
    # (mixins/ravaged_realm.py `_run_battle`), so the generic wording is wrong.
    ("Ravaged Realm", "Attempts"): "Total battles to fight.",
    ("Ravaged Realm", "Suggested Formations"): (
        "Copy the first team from the floor's Records. Off = only use your "
        "current team."
    ),
}


def lookup(section: str, leaf: str) -> str | None:
    """Find help text for a field by its TOML aliases.

    Args:
        section: The field's top-level table alias, or "" for a bare key.
        leaf: The field's own alias.

    Returns:
        The section-specific text if there is one, else the generic text, else None.
    """
    return SECTION_HELP.get((section, leaf)) or FIELD_HELP.get(leaf)
