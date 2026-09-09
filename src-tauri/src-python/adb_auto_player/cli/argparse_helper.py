"""Anything Argparse related."""

import argparse
from argparse import Namespace

from adb_auto_player.models.commands import Command


# Short names for the commands typed most often. `AFKJCustomRoutine2` is three words
# of camelCase and a digit to reach a thing the UI just calls "Custom Routine 2", and
# it is the command a person runs daily - so it gets an alias, accepted anywhere the
# full name is and shown beside it in the listing.
COMMAND_ALIASES: dict[str, str] = {
    "c1": "AFKJCustomRoutine",
    "c2": "AFKJCustomRoutine2",
    "c3": "AFKJCustomRoutine3",
}

# The reverse direction, for the help output. Built once rather than searched per line.
_ALIAS_OF = {target: alias for alias, target in COMMAND_ALIASES.items()}


class ArgparseHelper:
    """Argparse helper functions."""

    @staticmethod
    def build_argument_parser(
        commands: dict[str, list[Command]], exit_on_error: bool = True
    ) -> argparse.ArgumentParser:
        """Builds argparse.ArgumentParser."""
        parser = argparse.ArgumentParser(
            formatter_class=_build_argparse_formatter(commands),
            exit_on_error=exit_on_error,
        )
        parser.add_argument(
            "command",
            help="Command to run",
            choices=[
                cmd.name
                for category_commands in commands.values()
                for cmd in category_commands
            ]
            # Aliases are real choices, not a pre-parse rewrite: argparse validates
            # against this list, so a rewrite would have to happen before validation
            # and would lose argparse's own error message for a genuine typo.
            + [
                alias
                for alias, target in COMMAND_ALIASES.items()
                if any(
                    cmd.name == target
                    for category_commands in commands.values()
                    for cmd in category_commands
                )
            ],
        )
        parser.add_argument(
            "--output",
            choices=["terminal", "text", "raw"],
            default="terminal",
            help="Output format",
        )
        parser.add_argument(
            "--log-level",
            choices=["DISABLE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            default="INFO",
            help=(
                "How much reaches the CONSOLE. Defaults to INFO: a run's DEBUG lines "
                "are template matches and taps, which bury the handful of lines a "
                "person is actually reading for. They are still kept - the "
                "warning-context file holds the DEBUG lead-up and flushes it when "
                "something goes wrong."
            ),
        )
        parser.add_argument(
            "--app-config-dir",
            default=None,
            help="settings directory",
        )
        parser.add_argument(
            "--resource-dir",
            default=None,
            help="adb_auto_player directory",
        )

        return parser

    @staticmethod
    def resolve_command(name: str) -> str:
        """Map a short alias to the command it stands for.

        Args:
            name: What the user typed.

        Returns:
            The real command name, or `name` unchanged when it is not an alias.
        """
        return COMMAND_ALIASES.get(name, name)

    @staticmethod
    def get_log_level_from_args(args: Namespace) -> int | str:
        """Get log level from command line arguments."""
        log_level = args.log_level
        if log_level == "DISABLE":
            log_level = 99
        return log_level



def _format_command_line(cmd) -> str:
    """One listing row: the command, its alias if it has one, and its tooltip.

    The alias is shown INLINE rather than in a separate legend - a legend is a second
    place to look, and the point of the alias is to be seen while reading the list.

    Args:
        cmd: The command to render.

    Returns:
        The formatted line.
    """
    alias = _ALIAS_OF.get(cmd.name)
    label = f"{cmd.name} ({alias})" if alias else cmd.name
    tooltip = getattr(cmd.menu_item, "tooltip", "")
    return f"    {label:<36} {tooltip}" if tooltip else f"    {label}"


def _build_argparse_formatter(commands_by_category: dict[str, list[Command]]):
    """Builds argparse.HelpFormatter."""

    class CustomArgparseFormatter(argparse.HelpFormatter):
        def _format_usage(self, usage, actions, groups, prefix):
            prog = self._prog

            optional_actions = [
                action
                for action in actions
                if action.option_strings and action.dest != "help"
            ]
            optional_parts = []
            for action in optional_actions:
                opt_str = (
                    action.option_strings[0]
                    if len(action.option_strings) == 1
                    else action.option_strings[-1]
                )
                optional_parts.append(f"[{opt_str}]")
            optional_str = " ".join(optional_parts)
            command_action = next(
                (
                    action
                    for action in actions
                    if not action.option_strings and action.dest == "command"
                ),
                None,
            )

            if command_action:
                # Get command choices
                choices = (
                    sorted(command_action.choices) if command_action.choices else []
                )
                if len(choices) > (max_choices := 3):
                    command_str = "{" + ", ".join(choices[:max_choices]) + ", ...}"
                else:
                    command_str = "{" + ", ".join(choices) + "}"
            else:
                command_str = ""

            # Format according to argparse's style
            usage_str = f"{prog} [-h] {optional_str} {command_str}"

            return f"{usage_str}\n\n"

        def _format_action(self, action):
            if action.dest == "command":
                parts = []

                common_cmds = commands_by_category.get("Commands", [])
                if common_cmds:
                    parts.append("  Common Commands:")
                    for cmd in sorted(common_cmds, key=lambda c: c.name.lower()):
                        parts.append(_format_command_line(cmd))

                other_groups = {
                    k: v for k, v in commands_by_category.items() if k != "Commands"
                }
                if other_groups:
                    parts.append("\n  Game Commands:")
                    for group_name, group_cmds in sorted(
                        other_groups.items(), key=lambda item: item[0].lower()
                    ):
                        parts.append(f"  - {group_name}:")
                        for cmd in sorted(group_cmds, key=lambda c: c.name.lower()):
                            parts.append(_format_command_line(cmd))

                return "\n".join(parts) + "\n"

            return super()._format_action(action)

    return CustomArgparseFormatter
