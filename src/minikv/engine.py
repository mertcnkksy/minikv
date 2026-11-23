"""The MiniKV database engine.

:class:`Database` is the single entry point for executing commands. It wires
a storage backend, a clock and a command registry together and dispatches
tokenised commands to their handlers. It knows nothing about terminals or
sockets, which lets the CLI and a future TCP server share exactly the same
execution path.
"""

from __future__ import annotations

from minikv.clock import Clock, SystemClock
from minikv.commands import CommandContext, CommandRegistry, default_registry
from minikv.errors import CommandError, UnknownCommandError
from minikv.protocol import parse_command
from minikv.responses import Response
from minikv.storage import InMemoryStorage, StorageBackend


class Database:
    """An in-memory key-value database with key expiry.

    Example:
        >>> db = Database()
        >>> db.execute("SET", "greeting", "hello", "EX", "60")
        SimpleString(value='OK')
        >>> db.execute("GET", "greeting")
        'hello'

    Args:
        storage: Storage backend to use. Defaults to a new
            :class:`~minikv.storage.InMemoryStorage` driven by ``clock``.
        registry: Command registry to use. Defaults to the built-in commands.
        clock: Time source for a default storage backend. When ``storage`` is
            given, its own clock is used and ``clock`` must be omitted or be
            that same object.

    Raises:
        ValueError: If ``storage`` and ``clock`` disagree about the clock.
    """

    def __init__(
        self,
        storage: StorageBackend | None = None,
        registry: CommandRegistry | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        if storage is None:
            storage = InMemoryStorage(clock if clock is not None else SystemClock())
        elif clock is not None and clock is not storage.clock:
            raise ValueError("clock must match the clock of the provided storage backend")
        self._storage: StorageBackend = storage
        self._registry: CommandRegistry = registry if registry is not None else default_registry()
        self._context = CommandContext(storage=storage, clock=storage.clock)

    @property
    def storage(self) -> StorageBackend:
        """The storage backend holding the data."""
        return self._storage

    @property
    def clock(self) -> Clock:
        """The clock used for expiry decisions."""
        return self._storage.clock

    @property
    def commands(self) -> CommandRegistry:
        """The registry of commands this database understands."""
        return self._registry

    def execute(self, *command: str) -> Response:
        """Execute a single tokenised command.

        Before dispatching, keys whose expiry has passed are purged
        (active expiration), so memory is reclaimed even for keys that are
        never read again.

        Args:
            *command: The command name followed by its arguments,
                e.g. ``execute("SET", "key", "value")``.

        Returns:
            The command's response value (see :mod:`minikv.responses`).

        Raises:
            TypeError: If any token is not a string.
            CommandError: If the command is empty, unknown, called with the
                wrong number of arguments, or fails during execution.
        """
        if not command:
            raise CommandError("empty command")
        for token in command:
            if not isinstance(token, str):
                raise TypeError(f"command tokens must be str, got {type(token).__name__}")

        name, *args = command
        spec = self._registry.get(name)
        if spec is None:
            raise UnknownCommandError(name, args)
        spec.validate_arity(len(command))
        self._storage.remove_expired()
        return spec.handler(self._context, args)

    def execute_line(self, line: str) -> Response:
        """Parse and execute a raw command line such as ``SET key "a value" EX 10``.

        Args:
            line: The raw command text.

        Returns:
            The command's response value.

        Raises:
            ParseError: If the line cannot be tokenised.
            CommandError: See :meth:`execute`.
        """
        return self.execute(*parse_command(line))
