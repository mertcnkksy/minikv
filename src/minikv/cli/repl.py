"""The interactive read-eval-print loop.

The loop talks to a :class:`CommandExecutor`: either an embedded
:class:`~minikv.Database` or a :class:`~minikv.client.MiniKVClient`
connected to a server. Both expose the same ``execute(*command)`` method
and return the same response values, so the shell is identical in both modes.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol, TextIO

from minikv import __version__
from minikv.cli.formatting import format_error, format_response
from minikv.commands import CommandRegistry, default_registry
from minikv.errors import MiniKVError
from minikv.protocol import parse_command
from minikv.responses import Response

EXIT_COMMANDS = frozenset({"QUIT", "EXIT"})
"""Client-side commands that end the session."""


class CommandExecutor(Protocol):
    """Anything that can execute a tokenised command."""

    def execute(self, *command: str) -> Response:
        """Execute a command and return its response."""
        ...


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
    """An interactive shell that executes commands through an executor.

    Input and output are injectable so the loop can be tested without a
    terminal.

    Args:
        executor: Where commands run: a :class:`~minikv.Database` or a
            :class:`~minikv.client.MiniKVClient`.
        help_registry: Registry used by ``HELP``. Defaults to the executor's
            own ``commands`` attribute if it has one, else the built-ins.
        input_func: Function that displays a prompt and returns one line,
            raising :class:`EOFError` when input is exhausted.
        output: Stream that receives all output.
        prompt: Prompt text.
        show_banner: Whether to print a welcome banner on start.
        banner: Custom banner text.
    """

    def __init__(
        self,
        executor: CommandExecutor,
        *,
        help_registry: CommandRegistry | None = None,
        input_func: Callable[[str], str] = input,
        output: TextIO | None = None,
        prompt: str = "minikv> ",
        show_banner: bool = True,
        banner: str | None = None,
    ) -> None:
        self._executor = executor
        own_registry = getattr(executor, "commands", None)
        if help_registry is not None:
            self._help_registry = help_registry
        elif isinstance(own_registry, CommandRegistry):
            self._help_registry = own_registry
        else:
            self._help_registry = default_registry()
        self._input = input_func
        self._output = output if output is not None else sys.stdout
        self._prompt = prompt
        self._show_banner = show_banner
        self._banner = banner or f"MiniKV {__version__} - type HELP for commands, QUIT to exit."

    def _write(self, text: str) -> None:
        self._output.write(text + "\n")
        self._output.flush()

    def run(self) -> int:
        """Run the loop until ``QUIT``/``EXIT`` or end of input.

        Returns:
            The process exit code (always ``0``).
        """
        if self._show_banner:
            self._write(self._banner)
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
            self._write(render_help(self._help_registry, topic))
            return True

        try:
            response = self._executor.execute(*tokens)
        except MiniKVError as exc:
            self._write(format_error(exc))
        except ConnectionError as exc:
            self._write(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the shell alive on bugs
            self._write(f"(error) ERR internal error: {exc}")
        else:
            self._write(format_response(response))
        return True
