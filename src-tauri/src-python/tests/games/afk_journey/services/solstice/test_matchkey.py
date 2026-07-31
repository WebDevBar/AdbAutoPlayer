"""The canonical match key.

This must stay byte-identical to the server's app/identity.py. If the two drift,
dedupe stops working silently and the pool double-counts every shared match.
"""

from datetime import UTC, datetime

import pytest

from adb_auto_player.games.afk_journey.services.solstice.matchkey import (
    comps_key,
    is_complete,
    natural_key,
)


def dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def test_key_is_stable_regardless_of_hero_order():
    a = natural_key("left", ["c", "a", "b"], ["f", "e", "d"], dt("2026-07-27T01:43:05Z"))
    b = natural_key("left", ["a", "b", "c"], ["d", "e", "f"], dt("2026-07-27T01:43:05Z"))
    assert a == b


def test_key_distinguishes_sides():
    a = natural_key("left", ["a", "b", "c"], ["d", "e", "f"], dt("2026-07-27T01:00:00Z"))
    b = natural_key("left", ["d", "e", "f"], ["a", "b", "c"], dt("2026-07-27T01:00:00Z"))
    assert a != b


def test_key_buckets_to_ten_minutes():
    """Two spectators of one match capture seconds apart; ten minutes is ample."""
    a = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:10:01Z"))
    b = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:19:59Z"))
    assert a == b


def test_two_matches_ten_minutes_apart_are_different_matches():
    """Why the bucket is no longer an hour: two DIFFERENT matches with the same
    comps and outcome are real signal, and an hourly bucket dropped one."""
    a = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:05:00Z"))
    b = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:15:00Z"))
    assert a != b


def test_key_is_timezone_independent():
    a = natural_key("left", ["a"], ["b"], dt("2026-07-27T03:30:00+02:00"))
    b = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:30:00Z"))
    assert a == b


def test_a_string_timestamp_is_accepted():
    """captured_at is stored as ISO text in SQLite."""
    a = natural_key("left", ["a"], ["b"], "2026-07-27T01:30:00+00:00")
    b = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:30:00Z"))
    assert a == b


def test_naive_captured_at_is_rejected():
    with pytest.raises(ValueError):
        natural_key("left", ["a"], ["b"], datetime(2026, 7, 27, 1, 0, 0))


def test_key_has_no_theme_parameter():
    import inspect

    assert "theme" not in inspect.signature(natural_key).parameters


def test_completeness_requires_three_a_side_and_a_decisive_outcome():
    assert is_complete(["a", "b", "c"], ["d", "e", "f"], "left") is True
    assert is_complete(["a", "b"], ["d", "e", "f"], "left") is False
    assert is_complete(["a", "b", "c"], ["d", "e", "f"], "draw") is False


def test_the_key_matches_the_server_algorithm():
    """A hard-coded digest, so a change on either side fails loudly here rather
    than quietly halving the pool's dedupe."""
    key = natural_key(
        "left",
        ["aliceth", "alna", "alsa"],
        ["antandra", "arden", "atalanta"],
        dt("2026-07-27T01:43:05Z"),
    )
    assert key == (
        "sha256:a441384a8c7747c4c36ade5ac9eeaf3d1e102fc4a133a0fcdfee1f73e80a5bdd"
    )


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
