"""Edit a settings TOML from the terminal, driven by the Pydantic models.

Fields come from the model's schema, so adding a setting in Python makes it
editable here with no change to this file - the same property the GUI form has.

What is edited and written is the raw dict, never `model_dump()`. A key the model
does not declare is therefore preserved but NOT shown; `App.toml` has two such keys
today, and the Python and Rust models have drifted besides. "The editor shows every
setting" is not a claim this makes.
"""

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from adb_auto_player.file_loader import settings_file

from . import prompt

_SCALAR_TYPES = ("string", "number", "integer", "boolean")


class _Field:
    """One editable leaf of a settings model."""

    def __init__(self, dotted_key: str, title: str, schema: dict[str, Any]) -> None:
        self.dotted_key = dotted_key
        self.title = title
        self.schema = schema

    @property
    def kind(self) -> str:
        """The JSON-schema type name, or "enum-list" for a multi-choice array."""
        if self.choices is not None and self.schema.get("type") == "array":
            return "enum-list"
        return str(self.schema.get("type", "string"))

    @property
    def choices(self) -> list[str] | None:
        """Allowed values, for an enum or an array of enums."""
        for holder in (self.schema, self.schema.get("items", {})):
            if isinstance(holder, dict) and "enum" in holder:
                return [str(v) for v in holder["enum"]]
        return None


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a $ref one level, which is how Pydantic emits nested models."""
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = ref.rsplit("/", 1)[-1]
    return root.get("$defs", {}).get(name, {})


def collect_fields(model: type[BaseModel]) -> list[_Field]:
    """Flatten a settings model into its editable leaves.

    Nested models become dotted keys, which is exactly the shape the TOML has.

    Args:
        model: The settings model to describe.

    Returns:
        Editable fields, in declaration order.
    """
    root = model.model_json_schema(by_alias=False)
    fields: list[_Field] = []

    def walk(schema: dict[str, Any], prefix: str, label: str) -> None:
        schema = _resolve(schema, root)
        properties = schema.get("properties")
        if not properties:
            return
        for name, raw in properties.items():
            resolved = _resolve(raw, root)
            title = raw.get("title") or resolved.get("title") or name
            dotted = f"{prefix}{name}"
            if resolved.get("properties"):
                walk(resolved, f"{dotted}.", f"{label}{title} / ")
                continue
            field = _Field(dotted, f"{label}{title}", resolved)
            if field.kind in _SCALAR_TYPES or field.kind == "enum-list":
                fields.append(field)

    walk(root, "", "")
    return fields


def _coerce(field: _Field, raw: str) -> Any:
    """Turn typed text into the value the model expects.

    Raises:
        ValueError: If the text is not valid for the field's type.
    """
    kind = field.kind
    if kind == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("true", "yes", "y", "1"):
            return True
        if lowered in ("false", "no", "n", "0"):
            return False
        raise ValueError(f"expected true or false, got {raw!r}")
    if kind == "integer":
        return int(raw)
    if kind == "number":
        return float(raw)
    return raw


def _edit_enum_list(field: _Field, current: Any) -> Any:
    """Toggle members of a multi-choice list."""
    choices = field.choices or []
    selected = [str(v) for v in current] if isinstance(current, list) else []

    while True:
        labels = [f"[{'x' if c in selected else ' '}] {c}" for c in choices]
        index = prompt.choose(f"{field.title} - toggle, then Done", labels, back="Done")
        if index is prompt.CANCELLED:
            return current
        if index == -1:
            return selected
        choice = choices[index]
        if choice in selected:
            selected.remove(choice)
        else:
            selected.append(choice)


def _prompt_scalar(field: _Field, data: dict[str, Any], current: Any) -> bool:
    """Ask for a plain scalar value. Returns True if it changed."""
    answer = prompt.ask_line(f"{field.title} (now: {current})", str(current))
    if answer is prompt.CANCELLED:
        return False
    try:
        value = _coerce(field, str(answer))
    except ValueError as exc:
        print(f"  not saved: {exc}")
        return False
    if value == current:
        return False
    settings_file.set_in(data, field.dotted_key, value)
    return True


def _edit_field(field: _Field, data: dict[str, Any]) -> bool:
    """Prompt for one field's new value. Returns True if it changed."""
    current = settings_file.get_in(data, field.dotted_key)

    if field.kind == "enum-list":
        new_value = _edit_enum_list(field, current)
        if new_value == current:
            return False
        settings_file.set_in(data, field.dotted_key, new_value)
        return True

    if field.choices:
        index = prompt.choose(f"{field.title} (now: {current})", field.choices)
        if index is prompt.CANCELLED or index == -1:
            return False
        settings_file.set_in(data, field.dotted_key, field.choices[index])
        return True

    return _prompt_scalar(field, data, current)


def edit(path: Path, model: type[BaseModel], on_saved=None) -> None:
    """Run the edit loop for one settings file.

    Args:
        path: The TOML to edit. It need not exist yet.
        model: The model describing it, used for field metadata and validation.
        on_saved: Called after a successful write, to invalidate caches.
    """
    try:
        data = settings_file.read(path)
    except Exception as exc:
        print(f"  cannot read {path}: {exc}")
        return

    fields = collect_fields(model)
    if not fields:
        print(f"  no editable fields in {model.__name__}")
        return

    dirty = False
    while True:
        labels = [
            f"{f.title} = {settings_file.get_in(data, f.dotted_key)}" for f in fields
        ]
        title = f"{path.name}{' *' if dirty else ''}"
        index = prompt.choose(
            title, labels, back="Save and go back" if dirty else "Back"
        )

        if index is prompt.CANCELLED:
            index = -1

        if index == -1:
            if not dirty:
                return
            try:
                settings_file.validate(data, model)
            except ValidationError as exc:
                print(f"\n  NOT saved - {exc.error_count()} invalid value(s):")
                for error in exc.errors():
                    print(
                        f"    {'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
                    )
                if prompt.confirm("Keep editing?", default=True) is True:
                    continue
                return
            try:
                settings_file.write(path, data)
            except Exception as exc:
                print(f"  NOT saved - {exc}")
                return
            print(f"  saved {path}")
            logging.debug(f"CLI wrote {path}")
            if on_saved is not None:
                on_saved()
            return

        dirty = _edit_field(fields[index], data) or dirty
