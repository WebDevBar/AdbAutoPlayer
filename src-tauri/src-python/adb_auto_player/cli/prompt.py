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

CANCELLED = object()
"""Returned when the user pressed Ctrl+C or Ctrl+D instead of answering."""


def _input(prompt: str) -> str | Any:
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        print()
        return CANCELLED


def ask_line(prompt: str, default: str | None = None) -> str | Any:
    """Ask for a single line of text.

    Args:
        prompt: Shown before the cursor.
        default: Returned when the user just presses enter.

    Returns:
        The entered text, the default, or CANCELLED.
    """
    suffix = f" [{default}]" if default is not None else ""
    answer = _input(f"{prompt}{suffix}: ")
    if answer is CANCELLED:
        return CANCELLED
    answer = answer.strip()
    if not answer and default is not None:
        return default
    return answer


def choose(title: str, options: Sequence[str], back: str = "Back") -> int | Any:
    """Show a numbered list and return the chosen index.

    Args:
        title: Printed above the list.
        options: The choices, in display order.
        back: Label for the always-present zero option.

    Returns:
        The zero-based index into `options`, -1 for the back option, or CANCELLED.
    """
    while True:
        print(f"\n{title}")
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
    answer = ask_line(f"{prompt} (y/n)", "y" if default else "n")
    if answer is CANCELLED:
        return CANCELLED
    return str(answer).strip().lower().startswith("y")
