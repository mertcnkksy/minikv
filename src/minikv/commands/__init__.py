"""Command definitions, registry and built-in commands."""

from minikv.commands.base import CommandContext, CommandHandler, CommandSpec, require_int
from minikv.commands.builtin import CORE_COMMANDS
from minikv.commands.expiry import EXPIRY_COMMANDS
from minikv.commands.registry import CommandRegistry

BUILTIN_COMMANDS = CORE_COMMANDS + EXPIRY_COMMANDS
"""Every command shipped with MiniKV."""


def default_registry() -> CommandRegistry:
    """Return a new registry populated with all built-in commands."""
    return CommandRegistry(BUILTIN_COMMANDS)


__all__ = [
    "BUILTIN_COMMANDS",
    "CORE_COMMANDS",
    "EXPIRY_COMMANDS",
    "CommandContext",
    "CommandHandler",
    "CommandRegistry",
    "CommandSpec",
    "default_registry",
    "require_int",
]
