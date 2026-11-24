"""Core abstractions for defining commands."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from minikv.clock import Clock
from minikv.errors import InvalidIntegerError, WrongArityError
from minikv.responses import Response
from minikv.storage import StorageBackend
from minikv.utils import parse_int64


class PersistenceControl(Protocol):
    """Operations that persistence commands (``SAVE``, ``BGSAVE``...) need."""

    def save(self) -> None:
        """Write a snapshot synchronously."""
        ...

    def background_save(self) -> None:
        """Start writing a snapshot in the background."""
        ...

    def background_rewrite_aof(self) -> None:
        """Start compacting the append-only file in the background."""
        ...

    def last_save_time(self) -> int:
        """Return the Unix time (seconds) of the last successful snapshot."""
        ...


@dataclass(slots=True)
class CommandContext:
    """Runtime resources available to command handlers.

    Grouping dependencies in one object keeps handler signatures stable as
    new subsystems are added (for example per-client connection state).

    Attributes:
        storage: The storage backend holding the data.
        clock: The time source; handlers must never call :mod:`time` directly.
        persistence: Persistence controls, or ``None`` for a pure in-memory database.
    """

    storage: StorageBackend
    clock: Clock
    persistence: PersistenceControl | None = None
    _propagate: list[list[str]] | None = field(default=None, repr=False)

    def now_ms(self) -> int:
        """Return the current time in milliseconds since the Unix epoch."""
        return self.clock.time_ms()

    def propagate_as(self, *commands: Sequence[str]) -> None:
        """Record what should be written to the append-only log for this command.

        By default a command that modified the dataset is logged verbatim.
        Handlers whose effect depends on the current time (``EXPIRE``,
        ``SET ... EX``) must call this with an equivalent, time-independent
        form, so that replaying the log later reproduces the same state.
        """
        self._propagate = [list(command) for command in commands]

    def reset_propagation(self) -> None:
        """Forget any recorded propagation (called by the engine per command)."""
        self._propagate = None

    def propagation(self, default: Sequence[str]) -> list[list[str]]:
        """Return the commands to log, falling back to ``default``."""
        return self._propagate if self._propagate is not None else [list(default)]


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
