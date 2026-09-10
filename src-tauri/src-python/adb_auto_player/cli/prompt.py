"""Line-oriented prompts for the CLI menu.

Everything here reads a whole line and prints whole lines. No cursor movement, no
redraw, no alternate screen: `afk-bot.sh` pipes stdout through `tee` into a saved
log, and any escape sequence beyond colour would garble it.

`KeyboardInterrupt` and EOF are normal ways to answer a prompt, not errors. Both
come back as a cancellation the caller can act on rather than an exception that
unwinds the whole menu.
"""

from collections.abc import Sequence
from typing import Any

from . import tui

CANCELLED = tui.CANCELLED
"""Returned when the user cancelled instead of answering.

Shared with `tui` so a caller can compare against one sentinel whichever front end
answered.
"""


def _input(prompt: str) -> str | Any:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        print()
        return CANCELLED


def ask_line(prompt: str, default: str | None = None) -> str | Any:
    """Ask for a single line of text.

    Uses the full-screen front end when a terminal is available, and falls back to
    a printed prompt otherwise - a cron run, a pipe, or a terminal prompt_toolkit
    cannot drive.

    Args:
        prompt: Shown before the cursor.
        default: Returned when the user just presses enter.

    Returns:
        The entered text, the default, or CANCELLED.
    """
    if tui.is_available():
        return tui.ask_line(prompt, default)

    suffix = f" [{default}]" if default is not None else ""
    answer = _input(f"{prompt}{suffix}: ")
    if answer is CANCELLED:
        return CANCELLED
    answer = answer.strip()
    if not answer and default is not None:
        return default
    return answer


def choose(
    title: str,
    options: Sequence[str],
    back: str = "Back",
    subtitle: str = "",
) -> int | Any:
    """Show a list and return the chosen index.

    Args:
        title: Shown above the list.
        options: The choices, in display order.
        back: Label for the always-present zero option.
        subtitle: Optional second header line.

    Returns:
        The zero-based index into `options`, -1 for the back option, or CANCELLED.
    """
    if tui.is_available():
        return tui.select(title, options, back=back, subtitle=subtitle)

    while True:
        print(f"\n{title}")
        if subtitle:
            print(f"  {subtitle}")
        for i, option in enumerate(options, start=1):
            print(f"  {i:>3}. {option}")
        print(f"  {0:>3}. {back}")

        answer = ask_line("Choice")
        if answer is CANCELLED:
            return CANCELLED
        if not answer:
            continue
        try:
            index = int(answer)
        except ValueError:
            print(f"  not a number: {answer!r}")
            continue
        if index == 0:
            return -1
        if 1 <= index <= len(options):
            return index - 1
        print(f"  out of range: {index}")


def confirm(prompt: str, default: bool = False) -> bool | Any:
    """Ask a yes/no question.

    Args:
        prompt: The question.
        default: Used when the user just presses enter.

    Returns:
        True, False, or CANCELLED.
    """
    if tui.is_available():
        return tui.confirm(prompt, default)

    answer = ask_line(f"{prompt} (y/n)", "y" if default else "n")
    if answer is CANCELLED:
        return CANCELLED
    return str(answer).strip().lower().startswith("y")
