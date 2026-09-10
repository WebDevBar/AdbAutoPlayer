"""Read, validate and write a settings TOML without losing what it holds.

Three traps make the obvious implementation destructive, and all three are real in
this repo:

1. **Writing through a Pydantic model deletes keys.** `TomlSettings.from_toml`
   validates with `extra="ignore"`, and `App.toml` carries `notifications_enabled`
   and `action_log_limit`, which no model declares in any language. Round-tripping
   through `model_dump()` would silently drop them. So the raw dict `tomllib`
   produced is what gets written; the model only ever validates.
2. **"It loaded" is not validation.** `from_toml` catches every exception and falls
   back to defaults, so a save path that trusted load would happily write garbage.
   `validate` calls `model_validate` directly and lets the error propagate.
3. **A partial write is worse than a failed one.** The Rust side writes these files
   non-atomically, and a truncated read is swallowed by `from_toml` into defaults,
   which a later save would then persist over real data. Every write here goes to a
   temp file in the same directory and is moved into place with `os.replace`.

Formatting is not preserved: `tomli-w` does not keep comments or layout. The
guarantee is semantic - every key that was present is still present with the same
value, and no key the caller did not edit changes value.
"""

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel


def read(path: Path) -> dict[str, Any]:
    """Read a TOML file into a plain dict.

    Args:
        path: The file to read.

    Returns:
        The parsed table, or an empty dict if the file does not exist.

    Raises:
        tomllib.TOMLDecodeError: If the file exists but is not valid TOML. Unlike
            `from_toml`, this is NOT swallowed - a caller about to write needs to
            know it is working from a real parse rather than an empty fallback.
    """
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def validate(data: dict[str, Any], model: type[BaseModel]) -> BaseModel:
    """Validate a raw dict against a settings model, raising on failure.

    Args:
        data: The raw table, as returned by `read`.
        model: The Pydantic model describing it.

    Returns:
        The validated model instance, used for field metadata and defaults.

    Raises:
        pydantic.ValidationError: If the data does not satisfy the model.
    """
    return model.model_validate(data)


def write(path: Path, data: dict[str, Any]) -> None:
    """Write a dict to a TOML file atomically.

    The temp file is created in the destination directory so that `os.replace` is a
    rename within one filesystem, which is atomic. A temp file elsewhere would make
    it a copy, reintroducing the partial-write window this exists to close.

    Args:
        path: The file to write.
        data: The complete table to write. Whatever is not in here is gone, so
            callers pass the dict they read, modified at the keys they edited.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def set_in(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set one dotted key in a nested dict, creating intermediate tables.

    Args:
        data: The table to modify in place.
        dotted_key: A path such as `"advanced.template_timeout"`.
        value: The value to store.
    """
    *parents, leaf = dotted_key.split(".")
    table = data
    for part in parents:
        existing = table.get(part)
        if not isinstance(existing, dict):
            existing = {}
            table[part] = existing
        table = existing
    table[leaf] = value


def get_in(data: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """Read one dotted key from a nested dict.

    Args:
        data: The table to read.
        dotted_key: A path such as `"advanced.template_timeout"`.
        default: Returned when any segment is missing.

    Returns:
        The stored value, or `default`.
    """
    table: Any = data
    for part in dotted_key.split("."):
        if not isinstance(table, dict) or part not in table:
            return default
        table = table[part]
    return table
