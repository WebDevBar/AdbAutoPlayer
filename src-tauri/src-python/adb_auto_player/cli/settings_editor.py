"""Edit a settings TOML from the terminal, driven by the Pydantic models.

Fields come from the model's schema, so adding a setting in Python makes it
editable here with no change to this file - the same property the GUI form has.

Rows show the value the bot will actually use: the raw TOML value when the key is
present (`[set]`), otherwise the model's default (`[default]`). Showing the raw
`get_in` result instead read `None` for every absent key, although the bot uses the
default. Help text comes from the schema `description`, falling back to the
CLI-side `field_help` map, and the default and allowed values are appended to it.

What is edited and written is the raw dict, never `model_dump()`. Defaults are only
ever displayed or used as a prefill - viewing a section does not add keys to the
file. A key the model does not declare is therefore preserved but NOT shown;
`App.toml` has two such keys today, and the Python and Rust models have drifted
besides. "The editor shows every setting" is not a claim this makes.
"""

import logging
import shutil
import textwrap
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from adb_auto_player.file_loader import settings_file

from . import field_help, prompt

_SCALAR_TYPES = ("string", "number", "integer", "boolean")

_UNSET: Any = object()
"""Marks "no default known", because None is a legitimate default."""

_MAX_REF_DEPTH = 8
_VALUE_WIDTH = 30
_BOOLEAN_LABELS = ("On", "Off")
_EN_DASH = chr(0x2013)  # not a literal: RUF001 flags it
_SET = "[set]"
_DEFAULT = "[default]"
_LEGEND = f"{_SET} = in your file   {_DEFAULT} = built-in value, not written"


class _Field:
    """One editable leaf of a settings model."""

    def __init__(
        self,
        dotted_key: str,
        title: str,
        schema: dict[str, Any],
        root: dict[str, Any] | None = None,
        default: Any = _UNSET,
    ) -> None:
        self.dotted_key = dotted_key
        self.title = title
        self.schema = schema
        self.root = root or {}
        self.default = default

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
    def leaf_key(self) -> str:
        """The field's own TOML key (its alias), without any table prefix."""
        return self.dotted_key.rsplit(".", 1)[-1]

    @property
    def leaf_title(self) -> str:
        """The field's own label, without the section prefix the walk added."""
        return self.title.split(" / ")[-1]

    @property
    def choices(self) -> list[str] | None:
        """Allowed values, for an enum or an array of enums."""
        items = self.schema.get("items", {})
        if isinstance(items, dict):
            # Pydantic emits list[SomeEnum] as items: {"$ref": ...}, so the enum
            # is not in `items` itself until it is resolved.
            items = _resolve(items, self.root)
        for holder in (self.schema, items):
            if isinstance(holder, dict) and "enum" in holder:
                return [str(v) for v in holder["enum"]]
        return None

    @property
    def minimum(self) -> float | None:
        """Lower bound from the schema, if any."""
        return self.schema.get("minimum")

    @property
    def maximum(self) -> float | None:
        """Upper bound from the schema, if any."""
        return self.schema.get("maximum")

    @property
    def has_default(self) -> bool:
        """Whether a default is known for this field."""
        return self.default is not _UNSET


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Follow a $ref, which is how Pydantic emits nested models and enums.

    Metadata written beside the $ref (default, description, title) belongs to the
    field, not to the shared definition, so it is merged over the target. Returning
    the target alone dropped the field's own default and description.
    """
    for _ in range(_MAX_REF_DEPTH):
        ref = schema.get("$ref")
        if not ref:
            break
        name = ref.rsplit("/", 1)[-1]
        target = root.get("$defs", {}).get(name, {})
        siblings = {k: v for k, v in schema.items() if k != "$ref"}
        schema = {**target, **siblings}
    return schema


def _model_defaults(model: type[BaseModel]) -> dict[str, Any]:
    """The model's own defaults, shaped like the TOML (keyed by alias).

    Built by validating an empty dict rather than reading the JSON schema, because
    the schema omits the default of any `default_factory` field - which is every
    nested section and every list.

    Returns:
        The defaults as plain data, or an empty dict if the model has required
        fields and so cannot be built from nothing.
    """
    try:
        return model.model_validate({}).model_dump(mode="json", by_alias=True)
    except ValidationError:
        return {}


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
    defaults = _model_defaults(model)
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
            default = settings_file.get_in(defaults, dotted, _UNSET)
            if default is _UNSET:
                default = resolved.get("default", _UNSET)
            field = _Field(dotted, f"{label}{title}", resolved, root, default)
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
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"expected a whole number, got {raw!r}") from None
    if kind == "number":
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"expected a number, got {raw!r}") from None
    return raw


def _number(value: float) -> str:
    """Render a bound without a pointless ".0" on whole numbers."""
    return str(int(value)) if float(value).is_integer() else str(value)


def range_text(field: _Field) -> str:
    """Numeric bounds as text, e.g. 1-999 with an en dash, or "" if unbounded."""
    low, high = field.minimum, field.maximum
    if low is not None and high is not None:
        return f"{_number(low)}{_EN_DASH}{_number(high)}"
    if low is not None:
        return f"≥ {_number(low)}"
    if high is not None:
        return f"≤ {_number(high)}"
    return ""


def _check_range(field: _Field, value: Any) -> str | None:
    """Return an error message if a number is outside the schema's bounds."""
    if field.kind not in ("integer", "number"):
        return None
    low, high = field.minimum, field.maximum
    if (low is not None and value < low) or (high is not None and value > high):
        return f"{field.leaf_title} must be {range_text(field)}; got {_number(value)}"
    return None


def _format_list(field: _Field, value: list[Any]) -> str:
    """Members of a list, or a count when they would not fit a row."""
    if not value:
        return "(none)"
    joined = ", ".join(str(v) for v in value)
    if len(joined) <= _VALUE_WIDTH:
        return joined
    return f"{len(value)} of {len(field.choices or value)} selected"


def format_value(field: _Field, value: Any) -> str:
    """Render a value the way the operator should read it.

    Booleans are On/Off, lists are their members, and a missing value is a dash.
    """
    if value is None or value is _UNSET:
        return "-"
    if isinstance(value, bool):
        return _BOOLEAN_LABELS[0 if value else 1]
    if isinstance(value, list):
        return _format_list(field, value)
    text = str(value) or "(empty)"
    if len(text) > _VALUE_WIDTH:
        return f"{text[: _VALUE_WIDTH - 1]}…"
    return text


def effective(field: _Field, data: dict[str, Any]) -> tuple[Any, bool]:
    """The value the bot will use for a field, and whether the file sets it.

    Args:
        field: The field to look up.
        data: The raw TOML dict. It is read, never modified.

    Returns:
        (value, is_set). `value` is the file's value when `is_set`, else the
        model default (or None when no default is known).
    """
    stored = settings_file.get_in(data, field.dotted_key, _UNSET)
    if stored is not _UNSET:
        return stored, True
    return (field.default if field.has_default else None), False


def help_text(field: _Field) -> str:
    """Explanation of a field: the schema description, else the CLI-side map.

    Returns:
        The text, or "" when nothing documents the field.
    """
    description = field.schema.get("description")
    if description:
        return str(description)
    return field_help.lookup(field.section, field.leaf_key) or ""


def _allowed_text(field: _Field) -> str:
    """What may be entered, or "" when any text is fine."""
    if field.kind == "enum-list":
        return f"any of {', '.join(field.choices or [])}"
    if field.choices:
        return ", ".join(field.choices)
    return range_text(field)


def _facts(field: _Field) -> str:
    """The "Default: 5. Allowed: 1-999." line, omitting what is not known."""
    parts = []
    if field.has_default:
        parts.append(f"Default: {format_value(field, field.default)}.")
    allowed = _allowed_text(field)
    if allowed:
        parts.append(f"Allowed: {allowed}.")
    return " ".join(parts)


def help_block(field: _Field, width: int | None = None) -> str:
    """Help text plus default and allowed values, wrapped for a terminal.

    Args:
        field: The field being edited.
        width: Columns to wrap to. Defaults to the terminal width less a margin.

    Returns:
        Possibly several lines, or "" when there is nothing to say.
    """
    if width is None:
        width = max(shutil.get_terminal_size((80, 24)).columns - 4, 20)
    lines: list[str] = []
    for text in (help_text(field), _facts(field)):
        if text:
            lines.extend(textwrap.wrap(text, width))
    return "\n".join(lines)


def _row_cells(field: _Field, data: dict[str, Any]) -> tuple[str, str, str, str]:
    """(label, value, marker, range) for one row of a section list."""
    value, is_set = effective(field, data)
    return (
        field.leaf_title,
        format_value(field, value),
        _SET if is_set else _DEFAULT,
        range_text(field),
    )


def section_rows(fields: list[_Field], data: dict[str, Any]) -> list[str]:
    """Aligned one-line descriptions of a section's fields.

    Args:
        fields: The section's fields.
        data: The raw TOML dict. It is read, never modified.

    Returns:
        One row per field, e.g. `Attempts   40  [set]      1-999`.
    """
    cells = [_row_cells(f, data) for f in fields]
    label_width = max((len(c[0]) for c in cells), default=0)
    value_width = max((len(c[1]) for c in cells), default=0)
    return [
        f"{label:<{label_width}}  {value:>{value_width}}  {marker:<{len(_DEFAULT)}}"
        f"  {span}".rstrip()
        for label, value, marker, span in cells
    ]


def _mark_current(labels: list[str], current: Any) -> list[str]:
    """Tag the option that matches the current value."""
    return [
        f"{label}   (current)" if label == str(current) else label for label in labels
    ]


def _edit_enum_list(field: _Field, current: Any, help_block_text: str = "") -> Any:
    """Toggle members of a multi-choice list, starting from the effective value."""
    choices = field.choices or []
    selected = [str(v) for v in current] if isinstance(current, list) else []

    while True:
        labels = [f"[{'x' if c in selected else ' '}] {c}" for c in choices]
        index = prompt.choose(
            f"{field.title} - toggle, then Done",
            labels,
            back="Done",
            subtitle=help_block_text,
        )
        if index is prompt.CANCELLED:
            return current
        if index == -1:
            break
        choice = choices[index]
        if choice in selected:
            selected.remove(choice)
        else:
            selected.append(choice)

    # Same members as before is not a change, whatever order they were toggled in.
    before = {str(v) for v in current} if isinstance(current, list) else set()
    if set(selected) == before:
        return current
    return [c for c in choices if c in selected]


def _prompt_scalar(field: _Field, data: dict[str, Any], current: Any) -> bool:
    """Ask for a plain scalar value. Returns True if it changed."""
    span = range_text(field)
    prefill = "" if current is None else str(current)
    answer = prompt.ask_line(
        f"{field.title}{f' ({span})' if span else ''}",
        prefill,
        help_text=help_block(field),
    )
    if answer is prompt.CANCELLED:
        return False
    try:
        value = _coerce(field, str(answer))
    except ValueError as exc:
        prompt.message(f"{field.leaf_title}: {exc}\nNot changed.")
        return False
    problem = _check_range(field, value)
    if problem:
        prompt.message(f"{problem}\nNot changed.")
        return False
    if value == current:
        return False
    settings_file.set_in(data, field.dotted_key, value)
    return True


def _choose_value(field: _Field, options: list[str], current: Any) -> int | None:
    """Pick one of `options`; returns its index, or None for back/cancel."""
    marked = _mark_current(options, current)
    start = options.index(str(current)) if str(current) in options else 0
    index = prompt.choose(field.title, marked, subtitle=help_block(field), start=start)
    if index is prompt.CANCELLED or index == -1:
        return None
    return index


def _pick_value(field: _Field, current: Any) -> Any:
    """Ask for a new value of a field edited from a list of choices.

    Returns:
        The chosen value, or `_UNSET` if the operator backed out.
    """
    if field.kind == "enum-list":
        return _edit_enum_list(field, current, help_block(field))
    if field.kind == "boolean":
        index = _choose_value(
            field, list(_BOOLEAN_LABELS), format_value(field, current)
        )
        return _UNSET if index is None else index == 0
    choices = field.choices or []
    index = _choose_value(field, choices, current)
    return _UNSET if index is None else choices[index]


def _edit_field(field: _Field, data: dict[str, Any]) -> bool:
    """Prompt for one field's new value. Returns True if it changed."""
    current, _ = effective(field, data)

    if field.kind not in ("enum-list", "boolean") and not field.choices:
        return _prompt_scalar(field, data, current)

    new_value = _pick_value(field, current)
    if new_value is _UNSET or new_value == current:
        return False
    settings_file.set_in(data, field.dotted_key, new_value)
    return True


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
        index = prompt.choose(
            title, section_rows(fields, data), back="Done", subtitle=_LEGEND
        )
        if index is prompt.CANCELLED or index == -1:
            return changed
        changed = _edit_field(fields[index], data) or changed


def _save(path: Path, data: dict[str, Any], model: type[BaseModel], on_saved) -> bool:
    """Validate then write. Returns True only when the file was written.

    False keeps the operator in the editor, so a failed write never discards the
    edits made so far.
    """
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
        return False
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
