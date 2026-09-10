"""ADB Auto Player Settings Loader Module."""

import contextvars
import logging
import tomllib
from pathlib import Path

from adb_auto_player.models.decorators import CacheGroup
from adb_auto_player.registries import register_cache
from adb_auto_player.models.pydantic import (
    AdbSettings,
)
from adb_auto_player.tauri_context import profile_aware_cache

_profile_app_config_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "profile_app_config_dir", default=None
)
_profile_resource_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "profile_resource_dir", default=None
)


class SettingsLoader:
    """Utility class for resolving and caching important settings paths."""

    @staticmethod
    def get_app_config_dir() -> Path:
        """Get App Config Dir."""
        value = _profile_app_config_dir.get()
        if value is None:
            raise RuntimeError("App Config Dir undefined")
        return value

    @staticmethod
    def resolve_layout() -> tuple[Path, Path]:
        """Resolve the config ROOT and the PROFILE directory from one input path.

        Three layouts have to work from a single `--app-config-dir` value:

        - The config ROOT (`App.toml` here, profiles in numbered subdirectories).
          This is what the CLI is given. The profile comes from
          `profiles.active_profile` in App.toml, exactly as the GUI applies it in
          `src/lib/utils/settings.ts`. Hardcoding profile 0 would silently diverge
          the moment a second GUI profile existed, and `commands.rs` renumbers
          surviving directories on delete, so a fixed index can even change meaning.
        - A PROFILE directory (`App.toml` one level up). This is what the GUI passes
          per task; `app_config_dir` there means the profile dir.
        - A legacy FLAT directory holding all three TOMLs side by side, with no
          numbered subdirectory. `<repo>/src-tauri/settings` is one.

        Neither file present means flat: the CLI never creates the two-level layout
        implicitly, only an explicit migration does.

        Returns:
            (root, profile_dir). In the flat layout the two are the same directory.
            Neither is guaranteed to exist; callers already handle missing files.
        """
        config_dir = SettingsLoader.get_app_config_dir()

        if (config_dir / "App.toml").is_file():
            profile_dir = config_dir / str(SettingsLoader._active_profile(config_dir))
            if not profile_dir.is_dir():
                profile_dir = config_dir  # legacy flat: no numbered subdirectory
            return config_dir, profile_dir

        if (config_dir.parent / "App.toml").is_file():
            return config_dir.parent, config_dir

        return config_dir, config_dir

    @staticmethod
    def _active_profile(root: Path) -> int:
        """Read profiles.active_profile from App.toml, defaulting to 0.

        Deliberately reads the raw TOML rather than going through AppSettings: this
        runs while resolving where settings live, so it cannot depend on a loader
        that needs that answer first.

        Args:
            root: The config root holding App.toml.

        Returns:
            The active profile index, or 0 if absent or unreadable.
        """
        try:
            with open(root / "App.toml", "rb") as f:
                value = tomllib.load(f).get("profiles", {}).get("active_profile", 0)
            return int(value)
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
            return 0

    @staticmethod
    def app_settings_path() -> Path:
        """Where App.toml lives, for whichever layout is in use.

        Returns:
            The path App.toml should be read from. May not exist, which callers
            already handle - the point is that it is the RIGHT path to check.
        """
        return SettingsLoader.resolve_layout()[0] / "App.toml"

    @staticmethod
    def set_app_config_dir(value: Path) -> None:
        """Set App Config Dir."""
        _profile_app_config_dir.set(value)

    @staticmethod
    def get_resource_dir() -> Path:
        """Get resource dir."""
        value = _profile_resource_dir.get()
        if value is None:
            raise RuntimeError("Resource Dir undefined")
        return value

    @staticmethod
    def set_resource_dir(value: Path) -> None:
        """Set resource dir.

        Expects contents to have the structure as the adb_auto_player project dir.
        ./games/afk_journey/templates/...
        ./binaries/...
        """
        _profile_resource_dir.set(value)

    @staticmethod
    def games_dir() -> Path:
        """Determine and return the games directory."""
        return SettingsLoader.get_resource_dir() / "games"

    @staticmethod
    def binaries_dir() -> Path:
        """Return the binaries directory."""
        return SettingsLoader.get_resource_dir() / "binaries"

    @staticmethod
    def settings_dir() -> Path:
        """Return the directory holding ADB.toml and the game TOMLs.

        This is the PROFILE directory, which is the config dir itself in the flat
        layout and a numbered subdirectory in the two-level one.
        """
        return SettingsLoader.resolve_layout()[1]

    @staticmethod
    @register_cache(CacheGroup.ADB_SETTINGS)
    @profile_aware_cache(maxsize=1)
    def adb_settings() -> AdbSettings:
        """Locate and load the ADB.toml settings file."""
        settings_file_path = SettingsLoader.settings_dir() / "ADB.toml"
        logging.debug(f"Python ADB.toml path: {settings_file_path}")
        return AdbSettings.from_toml(settings_file_path)

    @staticmethod
    @register_cache(CacheGroup.APP_SETTINGS)
    @profile_aware_cache(maxsize=1)
    def app_settings():
        """Locate and load the general application settings AdbAutoPlayer.toml file."""
        from adb_auto_player.models.pydantic.app_settings import (  # noqa: PLC0415
            AppSettings,
        )

        settings_file_path = SettingsLoader.settings_dir() / "AdbAutoPlayer.toml"
        logging.debug(f"Python AdbAutoPlayer.toml path: {settings_file_path}")
        return AppSettings.from_toml(settings_file_path)
