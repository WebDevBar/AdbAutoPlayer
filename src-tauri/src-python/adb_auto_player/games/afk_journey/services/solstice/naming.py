"""Resolve a hero slug from OCR text.

This is the ground-truth channel for the whole mode: the long-press popup names the hero
outright, and that name is what confirms or refutes image matching. A wrong answer here is
worse than no answer, because it would be recorded as truth - so anything ambiguous
resolves to None.
"""

from __future__ import annotations

from adb_auto_player.models import ConfidenceValue
from adb_auto_player.util import StringHelper

from .config import SolsticeConfig

# Below this similarity a block is not considered a name at all.
DEFAULT_THRESHOLD = 0.80
# A name must be at least this long to be matched, so that a short slug cannot match
# inside an unrelated word.
MIN_NAME_LENGTH = 4
# Longest known hero name is "Regulus Abundius" (16 chars); add slack for OCR noise.
# fuzzy_substring_match slides a pattern-length window across the WHOLE text, so a long
# stat-description block (e.g. skill text) has enough windows that one of them can land
# within similarity_threshold of a short name purely by chance - a real false positive
# from the "longpress_ally1" fixture, where a skill-description sentence fuzzy-matched
# "Leymar". Hero names are short UI labels, not sentences, so anything past this length
# is popup body copy, not a name, and is skipped before it ever reaches the matcher.
MAX_NAME_LENGTH = 24


def resolve_hero_name(
    texts: list[str],
    cfg: SolsticeConfig,
    threshold: float = DEFAULT_THRESHOLD,
) -> str | None:
    """Return the hero slug named in `texts`, or None if that is not unambiguous.

    Args:
        texts: OCR block strings from the popup region ONLY. fuzzy_substring_match is a
            SUBSTRING match, so passing whole-frame OCR would let a short hero name match
            inside unrelated text.
        cfg: loaded config, providing the hero name -> slug map.
        threshold: minimum similarity, as a ratio.

    Returns:
        The hero slug, or None when nothing matched or two heroes matched equally well.
    """
    heroes = cfg.heroes()
    confidence = ConfidenceValue(f"{int(round(threshold * 100))}%")

    hits: set[str] = set()
    for text in texts:
        cleaned = text.strip()
        if len(cleaned) < MIN_NAME_LENGTH or len(cleaned) > MAX_NAME_LENGTH:
            continue
        for slug, row in heroes.items():
            if len(row.name) < MIN_NAME_LENGTH:
                continue
            if StringHelper.fuzzy_substring_match(cleaned, row.name, confidence):
                hits.add(slug)

    # fuzzy_substring_match returns a bool, so it cannot rank. Two candidates means the
    # text was too degraded to be ground truth, and ground truth is the entire point.
    if len(hits) != 1:
        return None
    return hits.pop()
