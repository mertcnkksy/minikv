"""Core key-value commands: PING, SET, GET, DEL, EXISTS, KEYS."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from minikv.commands.base import CommandContext, CommandSpec, require_int
from minikv.errors import CommandSyntaxError, InvalidExpireTimeError, WrongArityError
from minikv.responses import OK, PONG, Response
from minikv.utils import compile_glob, fits_int64

_EXPIRE_UNITS: dict[str, tuple[int, bool]] = {
    # option: (milliseconds per unit, is absolute timestamp)
    "EX": (1000, False),
    "PX": (1, False),
    "EXAT": (1000, True),
    "PXAT": (1, True),
}


@dataclass(slots=True)
class SetOptions:
    """Parsed options of the ``SET`` command.

    Attributes:
        condition: ``"NX"``, ``"XX"`` or ``None``.
        expire_at: Absolute expiry time in milliseconds, or ``None``.
        keep_ttl: Whether ``KEEPTTL`` was given.
    """

    condition: str | None = None
    expire_at: int | None = None
    keep_ttl: bool = False


def parse_set_options(options: Sequence[str], now_ms: int) -> SetOptions:
    """Parse the optional arguments of ``SET`` following Redis rules.

    Args:
        options: Tokens after ``SET key value``.
        now_ms: Current time, used to resolve relative expiry options.

    Returns:
        The parsed options.

    Raises:
        CommandSyntaxError: On unknown, duplicated or conflicting options.
        InvalidIntegerError: If an expiry amount is not an integer.
        InvalidExpireTimeError: If an expiry amount is non-positive or overflows.
    """
    parsed = SetOptions()
    has_expiry = False
    index = 0
    while index < len(options):
        option = options[index].upper()
        if option in ("NX", "XX"):
            if parsed.condition is not None:
                raise CommandSyntaxError()
            parsed.condition = option
        elif option == "KEEPTTL":
            if has_expiry or parsed.keep_ttl:
                raise CommandSyntaxError()
            parsed.keep_ttl = True
        elif option in _EXPIRE_UNITS:
            if has_expiry or parsed.keep_ttl or index + 1 >= len(options):
                raise CommandSyntaxError()
            index += 1
            amount = require_int(options[index])
            if amount <= 0:
                raise InvalidExpireTimeError("SET")
            unit_ms, absolute = _EXPIRE_UNITS[option]
            expire_at = amount * unit_ms + (0 if absolute else now_ms)
            if not fits_int64(amount * unit_ms) or not fits_int64(expire_at):
                raise InvalidExpireTimeError("SET")
            parsed.expire_at = expire_at
            has_expiry = True
        else:
            raise CommandSyntaxError()
        index += 1
    return parsed


def cmd_ping(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``PING [message]`` - return ``PONG`` or echo ``message``."""
    if not args:
        return PONG
    if len(args) == 1:
        return args[0]
    raise WrongArityError("PING")


def cmd_set(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``SET key value [NX | XX] [EX s | PX ms | EXAT ts | PXAT ms-ts | KEEPTTL]``.

    Without ``KEEPTTL``, any existing expiry is replaced (or removed when no
    expiry option is given). Returns ``OK``, or ``None`` when an ``NX``/``XX``
    condition prevents the write.
    """
    key, value, *rest = args
    options = parse_set_options(rest, ctx.now_ms())
    if options.condition is not None:
        exists = ctx.storage.exists(key)
        if (options.condition == "NX") == exists:
            return None
    ctx.storage.set(key, value, options.expire_at, keep_ttl=options.keep_ttl)
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


CORE_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("PING", cmd_ping, -1, "Test the connection or echo a message.", "PING [message]"),
    CommandSpec(
        "SET",
        cmd_set,
        -3,
        "Set a key's value, optionally with a condition or expiry.",
        "SET key value [NX|XX] [EX s|PX ms|EXAT ts|PXAT ms-ts|KEEPTTL]",
    ),
    CommandSpec("GET", cmd_get, 2, "Get the value of a key.", "GET key"),
    CommandSpec("DEL", cmd_del, -2, "Delete one or more keys.", "DEL key [key ...]"),
    CommandSpec("EXISTS", cmd_exists, -2, "Count how many of the keys exist.", "EXISTS key [key ...]"),
    CommandSpec("KEYS", cmd_keys, 2, "Find all keys matching a glob pattern.", "KEYS pattern"),
)
