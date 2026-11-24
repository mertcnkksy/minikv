"""Command definitions, registry and built-in commands."""

from minikv.commands.base import (
    CommandContext,
    CommandHandler,
    CommandSpec,
    PersistenceControl,
    require_int,
)
from minikv.commands.builtin import CORE_COMMANDS
from minikv.commands.expiry import EXPIRY_COMMANDS
from minikv.commands.persistence import PERSISTENCE_COMMANDS
from minikv.commands.registry import CommandRegistry
from minikv.commands.server import SERVER_COMMANDS

BUILTIN_COMMANDS = CORE_COMMANDS + EXPIRY_COMMANDS + SERVER_COMMANDS + PERSISTENCE_COMMANDS
"""Every command shipped with MiniKV."""


def default_registry() -> CommandRegistry:
    """Return a new registry populated with all built-in commands."""
    return CommandRegistry(BUILTIN_COMMANDS)


__all__ = [
    "BUILTIN_COMMANDS",
    "CORE_COMMANDS",
    "EXPIRY_COMMANDS",
    "PERSISTENCE_COMMANDS",
    "SERVER_COMMANDS",
    "CommandContext",
    "CommandHandler",
    "CommandRegistry",
    "CommandSpec",
    "PersistenceControl",
    "default_registry",
    "require_int",
]
