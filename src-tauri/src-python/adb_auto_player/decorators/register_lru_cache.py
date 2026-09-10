"""Re-export of `register_cache`.

The implementation lives in `adb_auto_player.registries` so that `file_loader`
can import it without closing the cycle
`decorators -> util -> file_loader -> decorators`.
"""

from adb_auto_player.registries import register_cache

__all__ = ["register_cache"]
