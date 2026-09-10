"""Commands, Games and CustomRoutines.

The modules here should never have dependencies except models.
"""

from .registries import (
    CACHE_REGISTRY,
    COMMAND_REGISTRY,
    CUSTOM_ROUTINE_REGISTRY,
    GAME_REGISTRY,
    cache_clear,
    register_cache,
)

__all__ = [
    "CACHE_REGISTRY",
    "COMMAND_REGISTRY",
    "CUSTOM_ROUTINE_REGISTRY",
    "GAME_REGISTRY",
    "cache_clear",
    "register_cache",
]
