"""Core abstractions for defining commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from minikv.clock import Clock
from minikv.errors import InvalidIntegerError, WrongArityError
from minikv.responses import Response
from minikv.storage import StorageBackend
from minikv.utils import parse_int64


@dataclass(slots=True)
class CommandContext:
    """Runtime resources available to command handlers.

    Grouping dependencies in one object keeps handler signatures stable as
    new subsystems are added (for example a persistence log or per-client
    connection state).

    Attributes:
        storage: The storage backend holding the data.
        clock: The time source; handlers must never call :mod:`time` directly.
    """

    storage: StorageBackend
    clock: Clock

    def now_ms(self) -> int:
        """Return the current time in milliseconds since the Unix epoch."""
        return self.clock.time_ms()


type CommandHandler = Callable[[CommandContext, Sequence[str]], Response]
"""A handler receives the context and the arguments (without the command name)."""


def require_int(text: str) -> int:
    """Parse a command argument as a signed 64-bit integer.

    Raises:
        InvalidIntegerError: If ``text`` is not a valid integer.
    """
    value = parse_int64(text)
    if value is None:
        raise InvalidIntegerError()
    return value


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Declarative description of a command.

    Attributes:
        name: Command name; normalised to upper case.
        handler: Function that executes the command.
        arity: Redis-style arity counting the command name itself. A positive
            value means "exactly N tokens"; a negative value means "at least
            ``abs(N)`` tokens". For example ``GET key`` has arity ``2`` and
            ``DEL key [key ...]`` has arity ``-2``.
        summary: One-line description shown by ``HELP``.
        usage: Usage synopsis, e.g. ``"GET key"``.
    """

    name: str
    handler: CommandHandler
    arity: int
    summary: str
    usage: str

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("command name must be a non-empty string")
        if self.arity == 0:
            raise ValueError("arity must be non-zero")
        object.__setattr__(self, "name", self.name.strip().upper())

    def accepts(self, token_count: int) -> bool:
        """Return ``True`` if ``token_count`` tokens satisfy this command's arity."""
        if self.arity > 0:
            return token_count == self.arity
        return token_count >= -self.arity

    def validate_arity(self, token_count: int) -> None:
        """Raise :class:`WrongArityError` if ``token_count`` is not accepted."""
        if not self.accepts(token_count):
            raise WrongArityError(self.name)
