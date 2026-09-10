"""See __init__.py."""

from collections.abc import Callable

from adb_auto_player.models.commands import Command
from adb_auto_player.models.decorators import CacheGroup
from adb_auto_player.models.registries import CustomRoutineEntry, GameMetadata

# Nested dictionary: { module_name (e.g., 'AFKJourney'): { name: Command } }
COMMAND_REGISTRY: dict[str, dict[str, Command]] = {}

# { module_name (e.g., 'AFKJourney'): { label: CustomRoutineEntry} } }
CUSTOM_ROUTINE_REGISTRY: dict[str, dict[str, CustomRoutineEntry]] = {}

GAME_REGISTRY: dict[str, GameMetadata] = {}

CACHE_REGISTRY: dict[CacheGroup, list[tuple[Callable, bool]]] = {}


def register_cache(*groups: CacheGroup, profile_aware: bool = True):
    """Decorator to register a function's cache under one or more groups.

    Lives here rather than in `decorators` so that `file_loader` can import it:
    `decorators/__init__` pulls in `util`, which imports `file_loader`, so a
    module-level import from there would be a cycle. `decorators` re-exports it.

    If profile_aware=True, cache_clear will call func.cache_clear(profile_index)
    Otherwise, it will call func.cache_clear()

    Args:
        *groups: Cache groups to register the function's cache under.
        profile_aware: Whether the cache is keyed by profile index.

    Returns:
        A decorator that registers and returns the function unchanged.
    """

    def decorator(func: Callable) -> Callable:
        for group in groups:
            CACHE_REGISTRY.setdefault(group, []).append((func, profile_aware))
        return func

    return decorator


def cache_clear(group: CacheGroup, profile_index: int | None = None) -> None:
    """Clear every registered cache in a group.

    Args:
        group: The cache group to clear.
        profile_index: Profile to clear for profile-aware caches; None clears all.
    """
    for func, profile_aware in CACHE_REGISTRY.get(group, []):
        if cache_clear_func := getattr(func, "cache_clear", None):
            if profile_aware and profile_index is not None:
                cache_clear_func(profile_index)
            else:
                cache_clear_func()
