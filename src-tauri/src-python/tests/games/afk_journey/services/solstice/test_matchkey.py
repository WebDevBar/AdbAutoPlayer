"""The canonical match key.

This must stay byte-identical to the server's app/identity.py. If the two drift,
dedupe stops working silently and the pool double-counts every shared match.
"""

from adb_auto_player.games.afk_journey.services.solstice.matchkey import (
    comps_key,
    is_complete,
)


def test_completeness_requires_three_a_side_and_a_decisive_outcome():
    assert is_complete(["a", "b", "c"], ["d", "e", "f"], "left") is True
    assert is_complete(["a", "b"], ["d", "e", "f"], "left") is False
    assert is_complete(["a", "b", "c"], ["d", "e", "f"], "draw") is False


def test_natural_key_is_gone():
    """The orientation-sensitive key is removed, not merely unused.

    Leaving it importable is how a call site quietly comes back: it keyed one
    fight twice when the two spectators saw opposite sides, which is the whole
    reason `comps_key` exists.
    """
    from adb_auto_player.games.afk_journey.services.solstice import matchkey

    assert not hasattr(matchkey, "natural_key")


# --- the comps key ---------------------------------------------------------

_A = ["lorsan", "sonja", "valka"]
_B = ["hepler", "silven", "thador"]

# Pinned. The server test asserts the SAME literal; if they ever diverge, both fail.
_EXPECTED_PIN = "solstice-clash|hepler,silven,thador|lorsan,sonja,valka"


def test_orientation_does_not_change_the_key():
    assert comps_key("solstice-clash", _A, _B) == comps_key("solstice-clash", _B, _A)


def test_shuffling_within_a_side_does_not_change_the_key():
    assert comps_key("solstice-clash", _A, _B) == comps_key(
        "solstice-clash", list(reversed(_A)), list(reversed(_B))
    )


def test_a_different_event_gives_a_different_key():
    assert comps_key("solstice-clash", _A, _B) != comps_key("other-event", _A, _B)


def test_payload_is_the_pinned_shape():
    import hashlib

    expected = "sha256:" + hashlib.sha256(_EXPECTED_PIN.encode()).hexdigest()
    assert comps_key("solstice-clash", _A, _B) == expected
