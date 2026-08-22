"""Tests for `profile_aware_cache`.

Regression test for: `cache_clear(profile_index)` used `if not profile_index`,
which treats `profile_index=0` the same as `profile_index=None` (falsy-zero
bug). Saving settings for profile 0 was wiping every other profile's cache
instead of only profile 0's.
"""

from adb_auto_player.tauri_context.cache import profile_aware_cache
from adb_auto_player.tauri_context.context import TauriContext


def _set_profile_and_call(func, profile_index: int | None):
    TauriContext.set_profile_index(profile_index)
    return func()


class TestProfileAwareCacheClear:
    """Tests for `profile_aware_cache(...).cache_clear`."""

    def test_clearing_profile_zero_does_not_clear_other_profiles(self):
        calls: list[int | None] = []

        @profile_aware_cache(maxsize=1)
        def func():
            calls.append(1)
            return len(calls)

        _set_profile_and_call(func, 0)
        _set_profile_and_call(func, 1)
        assert calls == [1, 1]

        func.cache_clear(0)

        # Profile 0 was cleared: calling it again recomputes.
        assert _set_profile_and_call(func, 0) == 3
        # Profile 1's cached value must be untouched.
        assert _set_profile_and_call(func, 1) == 2

    def test_clearing_with_none_clears_all_profiles(self):
        calls: list[int | None] = []

        @profile_aware_cache(maxsize=1)
        def func():
            calls.append(1)
            return len(calls)

        _set_profile_and_call(func, 0)
        _set_profile_and_call(func, 1)

        func.cache_clear(None)

        assert _set_profile_and_call(func, 0) == 3
        assert _set_profile_and_call(func, 1) == 4
