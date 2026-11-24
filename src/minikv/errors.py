"""Exception hierarchy for MiniKV.

Every error raised deliberately by MiniKV derives from :class:`MiniKVError`.
Each error carries a short, Redis-style prefix (``ERR``) so that front ends
(the CLI today, a network server later) can render errors consistently.
"""

from __future__ import annotations

from collections.abc import Sequence


class MiniKVError(Exception):
    """Base class for all MiniKV errors.

    Attributes:
        prefix: Short error code shown before the message (e.g. ``ERR``).
        message: Human-readable description of the problem.
    """

    prefix: str = "ERR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return f"{self.prefix} {self.message}"


class ParseError(MiniKVError):
    """Raised when a raw command line cannot be tokenised."""


class CommandError(MiniKVError):
    """Base class for errors raised while executing a command."""


class UnknownCommandError(CommandError):
    """Raised when a command name is not registered."""

    def __init__(self, name: str, args: Sequence[str] = ()) -> None:
        preview = " ".join(f"'{arg}'" for arg in args[:5])
        message = f"unknown command '{name}', with args beginning with: {preview}".rstrip()
        super().__init__(message)
        self.name = name


class WrongArityError(CommandError):
    """Raised when a command receives the wrong number of arguments."""

    def __init__(self, name: str) -> None:
        super().__init__(f"wrong number of arguments for '{name.lower()}' command")
        self.name = name


class CommandSyntaxError(CommandError):
    """Raised when a command's arguments are syntactically invalid."""

    def __init__(self, message: str = "syntax error") -> None:
        super().__init__(message)


class InvalidIntegerError(CommandError):
    """Raised when an argument that must be a 64-bit integer is not one."""

    def __init__(self) -> None:
        super().__init__("value is not an integer or out of range")


class InvalidExpireTimeError(CommandError):
    """Raised when an expiry value is non-positive or would overflow."""

    def __init__(self, command: str) -> None:
        super().__init__(f"invalid expire time in '{command.lower()}' command")
        self.command = command


class ProtocolError(MiniKVError):
    """Raised when a client sends bytes that violate the RESP protocol.

    A server must reply with this error and close the connection, because
    the byte stream can no longer be trusted to be in sync.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(f"Protocol error: {detail}")
        self.detail = detail


class ReplyError(MiniKVError):
    """An error reply received from a MiniKV (or Redis) server.

    Args:
        reply: The full error line without the leading ``-``,
            e.g. ``"ERR unknown command 'FOO'"``.
    """

    def __init__(self, reply: str) -> None:
        prefix, _, message = reply.partition(" ")
        if not message:
            prefix, message = "ERR", reply
        super().__init__(message)
        self.prefix = prefix


class UnsupportedProtocolError(CommandError):
    """Raised by ``HELLO`` when a client asks for a protocol version MiniKV lacks."""

    prefix = "NOPROTO"

    def __init__(self) -> None:
        super().__init__("unsupported protocol version")


class PersistenceError(MiniKVError):
    """Raised when data cannot be saved to or loaded from disk."""
