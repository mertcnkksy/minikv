"""The interactive read-eval-print loop."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from minikv import __version__
from minikv.cli.formatting import format_error, format_response
from minikv.commands import CommandRegistry
from minikv.engine import Database
from minikv.errors import MiniKVError
from minikv.protocol import parse_command

EXIT_COMMANDS = frozenset({"QUIT", "EXIT"})
"""Client-side commands that end the session."""


def render_help(registry: CommandRegistry, topic: str | None = None) -> str:
    """Build help text for all commands or for a single command.

    The overview lists command names with a one-line summary; full usage
    synopses are shown for a single ``topic`` to keep the overview narrow.

    Args:
        registry: Registry to describe.
        topic: Optional command name to describe in detail.

    Returns:
        The help text.
    """
    if topic is not None:
        spec = registry.get(topic)
        if spec is None:
            return f"(error) ERR no help for unknown command '{topic}'"
        return f"{spec.usage}\n  {spec.summary}"

    entries = [(spec.name, spec.summary) for spec in registry]
    entries.append(("HELP", "Show this help, or usage with HELP <command>."))
    entries.append(("QUIT", "Leave the shell (alias: EXIT)."))
    width = max(len(name) for name, _ in entries)
    lines = ["Available commands (type HELP <command> for usage):"]
    lines.extend(f"  {name:<{width}}  {summary}" for name, summary in entries)
    return "\n".join(lines)


class Repl:
    """An interactive shell that executes commands against a :class:`Database`.

    Input and output are injectable so the loop can be tested without a
    terminal.

    Args:
        database: The database to operate on.
        input_func: Function that displays a prompt and returns one line,
            raising :class:`EOFError` when input is exhausted.
        output: Stream that receives all output.
        prompt: Prompt text.
        show_banner: Whether to print a welcome banner on start.
    """

    def __init__(
        self,
        database: Database,
        *,
        input_func: Callable[[str], str] = input,
        output: TextIO | None = None,
        prompt: str = "minikv> ",
        show_banner: bool = True,
    ) -> None:
        self._db = database
        self._input = input_func
        self._output = output if output is not None else sys.stdout
        self._prompt = prompt
        self._show_banner = show_banner

    def _write(self, text: str) -> None:
        self._output.write(text + "\n")
        self._output.flush()

    def run(self) -> int:
        """Run the loop until ``QUIT``/``EXIT`` or end of input.

        Returns:
            The process exit code (always ``0``).
        """
        if self._show_banner:
            self._write(f"MiniKV {__version__} - type HELP for commands, QUIT to exit.")
        while True:
            try:
                line = self._input(self._prompt)
            except EOFError:
                self._write("")
                break
            except KeyboardInterrupt:
                self._write("")
                continue
            if not self.handle_line(line):
                break
        return 0

    def handle_line(self, line: str) -> bool:
        """Process a single input line.

        Args:
            line: Raw user input.

        Returns:
            ``False`` if the session should end, ``True`` otherwise.
        """
        try:
            tokens = parse_command(line)
        except MiniKVError as exc:
            self._write(format_error(exc))
            return True
        if not tokens:
            return True

        name = tokens[0].upper()
        if name in EXIT_COMMANDS:
            self._write("Bye!")
            return False
        if name == "HELP":
            topic = tokens[1] if len(tokens) > 1 else None
            self._write(render_help(self._db.commands, topic))
            return True

        try:
            response = self._db.execute(*tokens)
        except MiniKVError as exc:
            self._write(format_error(exc))
        except Exception as exc:  # noqa: BLE001 - keep the shell alive on bugs
            self._write(f"(error) ERR internal error: {exc}")
        else:
            self._write(format_response(response))
        return True
