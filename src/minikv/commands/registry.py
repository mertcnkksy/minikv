"""A case-insensitive registry of command specifications."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from minikv.commands.base import CommandSpec


class CommandRegistry:
    """Map command names to :class:`CommandSpec` objects.

    Lookups are case-insensitive, mirroring Redis.

    Args:
        specs: Optional initial specifications to register.
    """

    def __init__(self, specs: Iterable[CommandSpec] = ()) -> None:
        self._commands: dict[str, CommandSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: CommandSpec) -> None:
        """Add a command.

        Raises:
            ValueError: If a command with the same name is already registered.
        """
        if spec.name in self._commands:
            raise ValueError(f"command '{spec.name}' is already registered")
        self._commands[spec.name] = spec

    def get(self, name: str) -> CommandSpec | None:
        """Return the specification for ``name``, or ``None`` if unknown."""
        return self._commands.get(name.upper())

    def names(self) -> list[str]:
        """Return all registered command names in alphabetical order."""
        return sorted(self._commands)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.upper() in self._commands

    def __iter__(self) -> Iterator[CommandSpec]:
        return (self._commands[name] for name in self.names())

    def __len__(self) -> int:
        return len(self._commands)
