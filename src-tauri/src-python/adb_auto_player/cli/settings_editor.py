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
    def section(self) -> str:
        """Top-level table this field belongs to, or "" for a bare key."""
        return self.dotted_key.split(".")[0] if "." in self.dotted_key else ""

    @property
    def leaf_title(self) -> str:
        """The field's own label, without the section prefix the walk added."""
        return self.title.split(" / ")[-1]

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
    # by_alias=True is REQUIRED, not cosmetic. AFKJourney.toml is keyed by alias
    # (["AFK Stages"], Attempts) while the field names are afk_stages.attempts.
    # Building dotted keys from field names made every value read as None, and a
    # save would have written a parallel table the model never reads - leaving the
    # real settings untouched and the file full of junk. App.toml hid this because
    # its models declare no aliases.
    root = model.model_json_schema(by_alias=True)
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


def _section_label(fields: list[_Field], section: str) -> str:
    """Human label for a section, from the first field that carries one."""
    for field in fields:
        if field.section == section and " / " in field.title:
            return field.title.split(" / ")[0]
    return section or "General"


def _edit_section(fields: list[_Field], data: dict[str, Any], title: str) -> bool:
    """Edit the fields of one section. Returns True if anything changed."""
    changed = False
    while True:
        labels = [
            f"{f.leaf_title} = {settings_file.get_in(data, f.dotted_key)}"
            for f in fields
        ]
        index = prompt.choose(title, labels, back="Done")
        if index is prompt.CANCELLED or index == -1:
            return changed
        changed = _edit_field(fields[index], data) or changed


def _save(path: Path, data: dict[str, Any], model: type[BaseModel], on_saved) -> bool:
    """Validate then write. Returns True when the caller should stop editing."""
    try:
        settings_file.validate(data, model)
    except ValidationError as exc:
        lines = [f"{exc.error_count()} invalid value(s) - NOT saved:"]
        lines += [
            f"  {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        prompt.message("\n".join(lines))
        return False
    try:
        settings_file.write(path, data)
    except Exception as exc:  # a bad write must not kill the menu
        prompt.message(f"NOT saved - {exc}")
        return True
    logging.debug(f"CLI wrote {path}")
    if on_saved is not None:
        on_saved()
    return True


def edit(path: Path, model: type[BaseModel], on_saved=None) -> None:
    """Run the edit loop for one settings file, grouped by section.

    A flat list of every field is unusable: the game settings have 50+ of them and a
    terminal has 24 rows. Sections mirror the TOML's own tables, so no single list
    is longer than a screen.

    Args:
        path: The TOML to edit. It need not exist yet.
        model: The model describing it, used for field metadata and validation.
        on_saved: Called after a successful write, to invalidate caches.
    """
    try:
        data = settings_file.read(path)
    except Exception as exc:  # report and return, never crash the menu
        prompt.message(f"cannot read {path}: {exc}")
        return

    fields = collect_fields(model)
    if not fields:
        prompt.message(f"no editable fields in {model.__name__}")
        return

    sections: list[str] = []
    for field in fields:
        if field.section not in sections:
            sections.append(field.section)

    dirty = False
    while True:
        labels = []
        for section in sections:
            members = [f for f in fields if f.section == section]
            labels.append(f"{_section_label(fields, section)}  ({len(members)})")

        index = prompt.choose(
            f"{path.name}{'   * unsaved' if dirty else ''}",
            labels,
            back="Save and go back" if dirty else "Back",
            subtitle=str(path),
        )
        if index is prompt.CANCELLED:
            index = -1

        if index == -1:
            if not dirty or _save(path, data, model, on_saved):
                return
            continue

        section = sections[index]
        members = [f for f in fields if f.section == section]
        dirty = _edit_section(members, data, _section_label(fields, section)) or dirty
