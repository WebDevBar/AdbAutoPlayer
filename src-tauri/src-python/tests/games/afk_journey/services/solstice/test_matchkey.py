"""The canonical match key.

This must stay byte-identical to the server's app/identity.py. If the two drift,
dedupe stops working silently and the pool double-counts every shared match.
"""

from datetime import UTC, datetime

import pytest

from adb_auto_player.games.afk_journey.services.solstice.matchkey import (
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


def test_key_buckets_to_the_utc_hour():
    a = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:00:01Z"))
    b = natural_key("left", ["a"], ["b"], dt("2026-07-27T01:59:59Z"))
    c = natural_key("left", ["a"], ["b"], dt("2026-07-27T02:00:01Z"))
    assert a == b
    assert a != c


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
        "sha256:ad7d3be46bba8685ee06c8585b7bd910ed5cf099c12738d515de0b9a2c5d7b95"
    )
