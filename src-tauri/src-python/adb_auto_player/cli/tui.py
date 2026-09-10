"""Full-screen scrollable pickers for the CLI menu.

Why this exists: 39 tasks and 74 game settings do not fit a 24-row terminal. A
printed list scrolls out of reach the moment it is longer than the window, which in
tmux means the top of the list is simply gone.

**Everything renders to /dev/tty, never to stdout.** `afk-bot.sh` pipes stdout
through `tee` into a saved log; a full-screen UI on that stream would fill the log
with cursor movement and clear-screen sequences. Writing to the terminal device
directly leaves the logged stream untouched - the log keeps the task output that is
worth saving, and none of the interface.

`is_available()` is the gate. No controlling terminal, or a `prompt_toolkit` that
cannot drive it, and the caller falls back to the line-oriented prompts, which is
also what a cron run gets.
"""

from collections.abc import Sequence
from typing import Any

CANCELLED = object()
"""Returned when the user backed out instead of choosing."""

_PAGE = 10
_SCROLL_MARGIN = 5

# One entry, resolved on first use: "unknown" until tried, then the io pair or None.
# A dict rather than two module globals so rebinding does not need `global`.
_TTY: dict[str, Any] = {"io": "unknown"}


def _tty() -> tuple[Any, Any] | None:
    """Open /dev/tty for input and output, once.

    Returns:
        (input, output) for prompt_toolkit, or None if there is no usable terminal.
    """
    if _TTY["io"] != "unknown":
        return _TTY["io"]

    try:
        from prompt_toolkit.input import create_input  # noqa: PLC0415
        from prompt_toolkit.output import create_output  # noqa: PLC0415

        # Two handles, not one "r+": /dev/tty is a character device, and opening
        # it read-write makes Python try to seek it (io.UnsupportedOperation).
        # Both live for the session, so they are deliberately never closed.
        tty_in = open("/dev/tty")
        tty_out = open("/dev/tty", "w")
        _TTY["io"] = (create_input(tty_in), create_output(tty_out))
    except Exception:  # any failure means "use the plain prompts"
        _TTY["io"] = None
    return _TTY["io"]


def is_available() -> bool:
    """Whether a full-screen UI can be shown.

    Returns:
        True when /dev/tty is usable, False when the caller should print instead.
    """
    return _tty() is not None


def _build_keys(count: int, option_count: int, state: dict, digits: list[str]):
    """Key bindings for `select`, kept out of it so `select` stays readable.

    Args:
        count: Total entries including the back row.
        option_count: Entries excluding the back row, for digit jumping.
        state: Holds "cursor" and "result", mutated in place.
        digits: Accumulates a typed number across keystrokes.

    Returns:
        The populated KeyBindings.
    """
    from prompt_toolkit.key_binding import KeyBindings  # noqa: PLC0415

    keys = KeyBindings()

    def move(delta: int) -> None:
        digits.clear()
        state["cursor"] = (state["cursor"] + delta) % count

    @keys.add("up")
    @keys.add("k")
    def _up(event) -> None:
        move(-1)

    @keys.add("down")
    @keys.add("j")
    def _down(event) -> None:
        move(1)

    @keys.add("pageup")
    @keys.add("c-u")
    def _page_up(event) -> None:
        state["cursor"] = max(0, state["cursor"] - _PAGE)

    @keys.add("pagedown")
    @keys.add("c-d")
    def _page_down(event) -> None:
        state["cursor"] = min(count - 1, state["cursor"] + _PAGE)

    @keys.add("home")
    @keys.add("g")
    def _home(event) -> None:
        state["cursor"] = 0

    @keys.add("end")
    @keys.add("G")
    def _end(event) -> None:
        state["cursor"] = count - 1

    @keys.add("enter")
    def _accept(event) -> None:
        state["result"] = -1 if state["cursor"] == count - 1 else state["cursor"]
        event.app.exit()

    @keys.add("escape")
    @keys.add("q")
    @keys.add("c-c")
    def _back(event) -> None:
        state["result"] = -1
        event.app.exit()

    @keys.add("backspace")
    def _undigit(event) -> None:
        if digits:
            digits.pop()

    for digit in "0123456789":

        @keys.add(digit)
        def _digit(event, digit: str = digit) -> None:
            # Typing a number jumps to it, so muscle memory from the printed menu
            # still works. 0 is Back, matching the old numbering exactly.
            digits.append(digit)
            wanted = int("".join(digits))
            if wanted == 0:
                state["cursor"] = count - 1
            elif 1 <= wanted <= option_count:
                state["cursor"] = wanted - 1
            if wanted * 10 > option_count:
                digits.clear()

    return keys


def select(
    title: str,
    options: Sequence[str],
    back: str = "Back",
    subtitle: str = "",
    start: int = 0,
) -> int | Any:
    """Show a scrollable list and return the chosen index.

    Args:
        title: Shown in the header.
        options: Choices, in display order.
        back: Label for the escape option, always last.
        subtitle: Optional second header line, e.g. a resolved path.
        start: Index to place the cursor on initially.

    Returns:
        The zero-based index into `options`, -1 for the back option, or CANCELLED.
    """
    io = _tty()
    if io is None:
        return CANCELLED

    from prompt_toolkit.application import Application  # noqa: PLC0415
    from prompt_toolkit.layout import (  # noqa: PLC0415
        HSplit,
        Layout,
        ScrollOffsets,
        Window,
    )
    from prompt_toolkit.layout.controls import FormattedTextControl  # noqa: PLC0415
    from prompt_toolkit.styles import Style  # noqa: PLC0415

    entries = [*options, back]
    state: dict[str, Any] = {
        "cursor": min(max(start, 0), len(entries) - 1),
        "result": CANCELLED,
    }
    digits: list[str] = []

    def header() -> list[tuple[str, str]]:
        lines = [("class:title", f" {title}\n")]
        if subtitle:
            lines.append(("class:subtitle", f" {subtitle}\n"))
        return lines

    def body() -> list[tuple[str, str]]:
        rendered = []
        for i, entry in enumerate(entries):
            number = "  0" if i == len(entries) - 1 else f"{i + 1:>3}"
            selected = i == state["cursor"]
            style = "class:selected" if selected else "class:entry"
            if selected:
                # This fragment is what prompt_toolkit scrolls to. Without it the
                # control reports its cursor on line 0 and the view never moves.
                rendered.append(("[SetCursorPosition]", ""))
            rendered.append((style, f" {'>' if selected else ' '} {number}. {entry}\n"))
        return rendered

    def footer() -> list[tuple[str, str]]:
        typed = f"   typing: {''.join(digits)}" if digits else ""
        position = f"{state['cursor'] + 1}/{len(entries)}"
        return [
            (
                "class:footer",
                f" {position}   up/down or j/k   enter select"
                f"   esc/q back   digits jump{typed}\n",
            )
        ]

    layout = Layout(
        HSplit(
            [
                Window(FormattedTextControl(header), height=2 if subtitle else 1),
                Window(
                    FormattedTextControl(body, focusable=True),
                    # Scrolling is prompt_toolkit's own, driven by the
                    # [SetCursorPosition] fragment body() marks the selected row
                    # with. A get_vertical_scroll hint CANNOT do this: it is
                    # applied first (containers.py:2503), then the built-in
                    # scroll pass reads the control's cursor position - 0 without
                    # that fragment - and drags the view straight back to the top,
                    # so any list longer than the window never scrolled at all.
                    scroll_offsets=ScrollOffsets(
                        top=_SCROLL_MARGIN, bottom=_SCROLL_MARGIN
                    ),
                ),
                Window(FormattedTextControl(footer), height=1),
            ]
        )
    )

    style = Style.from_dict(
        {
            "title": "bold",
            "subtitle": "#888888",
            "entry": "",
            "selected": "reverse bold",
            "footer": "#888888",
        }
    )

    Application(
        layout=layout,
        key_bindings=_build_keys(len(entries), len(options), state, digits),
        style=style,
        full_screen=True,
        input=io[0],
        output=io[1],
    ).run()

    return state["result"]


def ask_line(prompt_text: str, default: str | None = None) -> str | Any:
    """Ask for one line of text on the terminal device.

    Args:
        prompt_text: Shown before the cursor.
        default: Pre-filled, and returned on an empty answer.

    Returns:
        The entered text, the default, or CANCELLED.
    """
    io = _tty()
    if io is None:
        return CANCELLED

    from prompt_toolkit import PromptSession  # noqa: PLC0415

    try:
        session: Any = PromptSession(input=io[0], output=io[1])
        answer = session.prompt(f"{prompt_text}: ", default=default or "")
    except (KeyboardInterrupt, EOFError):
        return CANCELLED
    answer = answer.strip()
    if not answer and default is not None:
        return default
    return answer


def confirm(prompt_text: str, default: bool = False) -> bool | Any:
    """Ask a yes/no question as a two-item picker.

    Args:
        prompt_text: The question.
        default: Which option starts selected.

    Returns:
        True, False, or CANCELLED.
    """
    index = select(prompt_text, ["Yes", "No"], back="Cancel", start=0 if default else 1)
    if index is CANCELLED or index == -1:
        return CANCELLED
    return index == 0


def message(text: str) -> None:
    """Show a message and wait for acknowledgement.

    Args:
        text: What to show. Each line becomes a row, so long text scrolls.
    """
    select("", text.splitlines() or [text], back="OK")
