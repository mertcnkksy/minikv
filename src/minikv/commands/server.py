"""Server and connection utility commands: ECHO, DBSIZE, FLUSHDB, HELLO."""

from __future__ import annotations

from collections.abc import Sequence

from minikv import __version__
from minikv.commands.base import CommandContext, CommandSpec
from minikv.errors import CommandError, CommandSyntaxError, UnsupportedProtocolError
from minikv.utils import parse_int64

SUPPORTED_PROTOCOL = 2
"""The only RESP version MiniKV speaks."""
from minikv.responses import OK, Response


def cmd_echo(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``ECHO message`` - return ``message`` unchanged."""
    return args[0]


def cmd_dbsize(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``DBSIZE`` - return the number of live keys."""
    return len(ctx.storage)


def cmd_flushdb(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``FLUSHDB [ASYNC | SYNC]`` - delete every key.

    The mode is accepted for Redis compatibility; MiniKV always flushes
    synchronously.
    """
    if len(args) > 1 or (args and args[0].upper() not in ("ASYNC", "SYNC")):
        raise CommandSyntaxError()
    ctx.storage.clear()
    return OK


def cmd_hello(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``HELLO [protover]`` - negotiate the protocol and describe the server.

    Clients such as recent ``redis-py`` versions send ``HELLO 3`` on connect
    and fall back to RESP2 when they receive a ``NOPROTO`` error.
    """
    if len(args) > 1:
        raise CommandSyntaxError()
    if args:
        version = parse_int64(args[0])
        if version is None:
            raise CommandError("Protocol version is not an integer or out of range")
        if version != SUPPORTED_PROTOCOL:
            raise UnsupportedProtocolError()
    return [
        "server", "minikv",
        "version", __version__,
        "proto", SUPPORTED_PROTOCOL,
        "mode", "standalone",
        "role", "master",
        "modules", [],
    ]


SERVER_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("ECHO", cmd_echo, 2, "Return the given message.", "ECHO message"),
    CommandSpec("DBSIZE", cmd_dbsize, 1, "Return the number of keys.", "DBSIZE"),
    CommandSpec("FLUSHDB", cmd_flushdb, -1, "Delete all keys.", "FLUSHDB [ASYNC|SYNC]"),
    CommandSpec(
        "HELLO", cmd_hello, -1, "Negotiate the protocol and show server info.", "HELLO [protover]"
    ),
)
