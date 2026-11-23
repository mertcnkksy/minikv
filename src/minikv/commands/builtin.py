"""Built-in MiniKV commands for v0.1: PING, SET, GET, DEL, EXISTS, KEYS."""

from __future__ import annotations

from collections.abc import Sequence

from minikv.commands.base import CommandContext, CommandSpec
from minikv.commands.registry import CommandRegistry
from minikv.errors import WrongArityError
from minikv.responses import OK, PONG, Response
from minikv.utils import compile_glob


def cmd_ping(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``PING [message]`` - return ``PONG`` or echo ``message``."""
    if not args:
        return PONG
    if len(args) == 1:
        return args[0]
    raise WrongArityError("PING")


def cmd_set(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``SET key value`` - store a string value, overwriting any existing one."""
    key, value = args
    ctx.storage.set(key, value)
    return OK


def cmd_get(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``GET key`` - return the value at ``key`` or ``None`` if missing."""
    return ctx.storage.get(args[0])


def cmd_del(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``DEL key [key ...]`` - delete keys and return how many were removed."""
    return sum(1 for key in args if ctx.storage.delete(key))


def cmd_exists(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``EXISTS key [key ...]`` - count how many of the given keys exist.

    As in Redis, a key mentioned several times is counted several times.
    """
    return sum(1 for key in args if ctx.storage.exists(key))


def cmd_keys(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``KEYS pattern`` - return keys matching a glob pattern, sorted.

    Unlike Redis, results are sorted to make output deterministic.
    """
    matcher = compile_glob(args[0])
    return sorted(key for key in ctx.storage.keys() if matcher.fullmatch(key))


BUILTIN_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("PING", cmd_ping, -1, "Test the connection or echo a message.", "PING [message]"),
    CommandSpec("SET", cmd_set, 3, "Set the string value of a key.", "SET key value"),
    CommandSpec("GET", cmd_get, 2, "Get the value of a key.", "GET key"),
    CommandSpec("DEL", cmd_del, -2, "Delete one or more keys.", "DEL key [key ...]"),
    CommandSpec("EXISTS", cmd_exists, -2, "Count how many of the keys exist.", "EXISTS key [key ...]"),
    CommandSpec("KEYS", cmd_keys, 2, "Find all keys matching a glob pattern.", "KEYS pattern"),
)


def default_registry() -> CommandRegistry:
    """Return a new registry populated with the built-in commands."""
    return CommandRegistry(BUILTIN_COMMANDS)
