"""Key expiry commands.

``EXPIRE``, ``PEXPIRE``, ``EXPIREAT``, ``PEXPIREAT``, ``TTL``, ``PTTL``,
``EXPIRETIME``, ``PEXPIRETIME`` and ``PERSIST``, following Redis 7 semantics.
"""

from __future__ import annotations

from collections.abc import Sequence

from minikv.commands.base import CommandContext, CommandHandler, CommandSpec, require_int
from minikv.errors import CommandError, InvalidExpireTimeError
from minikv.responses import Response
from minikv.utils import fits_int64

KEY_MISSING = -2
"""Reply of TTL-style commands when the key does not exist."""

NO_EXPIRY = -1
"""Reply of TTL-style commands when the key exists but has no expiry."""


def _parse_expire_flags(flags: Sequence[str]) -> set[str]:
    """Parse ``NX``/``XX``/``GT``/``LT`` flags, rejecting invalid combinations."""
    parsed: set[str] = set()
    for flag in flags:
        upper = flag.upper()
        if upper not in ("NX", "XX", "GT", "LT"):
            raise CommandError(f"Unsupported option {flag}")
        parsed.add(upper)
    if "NX" in parsed and parsed & {"XX", "GT", "LT"}:
        raise CommandError("NX and XX, GT or LT options at the same time are not compatible")
    if {"GT", "LT"} <= parsed:
        raise CommandError("GT and LT options at the same time are not compatible")
    return parsed


def _make_expire_handler(name: str, unit_ms: int, absolute: bool) -> CommandHandler:
    """Build a handler for one member of the ``EXPIRE`` family.

    Args:
        name: Command name, used in error messages.
        unit_ms: Milliseconds per unit of the amount argument.
        absolute: Whether the amount is a Unix timestamp rather than a duration.
    """

    def handler(ctx: CommandContext, args: Sequence[str]) -> Response:
        key, raw_amount, *raw_flags = args
        amount = require_int(raw_amount)
        flags = _parse_expire_flags(raw_flags)

        now = ctx.now_ms()
        scaled = amount * unit_ms
        expire_at = scaled if absolute else now + scaled
        if not fits_int64(scaled) or not fits_int64(expire_at):
            raise InvalidExpireTimeError(name)

        if not ctx.storage.exists(key):
            return 0
        current = ctx.storage.get_expiry(key)
        # A key without expiry behaves as if its TTL were infinite for GT/LT.
        if "NX" in flags and current is not None:
            return 0
        if "XX" in flags and current is None:
            return 0
        if "GT" in flags and (current is None or expire_at <= current):
            return 0
        if "LT" in flags and current is not None and expire_at >= current:
            return 0

        if expire_at <= now:
            ctx.storage.delete(key)
            ctx.propagate_as(["DEL", key])
        else:
            ctx.storage.set_expiry(key, expire_at)
            ctx.propagate_as(["PEXPIREAT", key, str(expire_at)])
        return 1

    handler.__name__ = f"cmd_{name.lower()}"
    handler.__doc__ = f"``{name} key amount [NX|XX|GT|LT]`` - set a key's expiry."
    return handler


def _remaining_ms(ctx: CommandContext, key: str) -> int:
    """Return the key's remaining lifetime in ms, or ``KEY_MISSING``/``NO_EXPIRY``."""
    if not ctx.storage.exists(key):
        return KEY_MISSING
    expire_at = ctx.storage.get_expiry(key)
    if expire_at is None:
        return NO_EXPIRY
    return max(expire_at - ctx.now_ms(), 0)


def cmd_ttl(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``TTL key`` - remaining time to live in seconds (rounded), -1 or -2."""
    remaining = _remaining_ms(ctx, args[0])
    return remaining if remaining < 0 else (remaining + 500) // 1000


def cmd_pttl(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``PTTL key`` - remaining time to live in milliseconds, -1 or -2."""
    return _remaining_ms(ctx, args[0])


def _expire_time(ctx: CommandContext, key: str, divisor: int) -> int:
    if not ctx.storage.exists(key):
        return KEY_MISSING
    expire_at = ctx.storage.get_expiry(key)
    return NO_EXPIRY if expire_at is None else expire_at // divisor


def cmd_expiretime(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``EXPIRETIME key`` - absolute expiry as a Unix timestamp in seconds, -1 or -2."""
    return _expire_time(ctx, args[0], 1000)


def cmd_pexpiretime(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``PEXPIRETIME key`` - absolute expiry as a Unix timestamp in ms, -1 or -2."""
    return _expire_time(ctx, args[0], 1)


def cmd_persist(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``PERSIST key`` - remove a key's expiry; 1 if removed, 0 otherwise."""
    return 1 if ctx.storage.clear_expiry(args[0]) else 0


_FLAGS = "[NX|XX|GT|LT]"

EXPIRY_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        "EXPIRE",
        _make_expire_handler("EXPIRE", 1000, absolute=False),
        -3,
        "Set a key's time to live in seconds.",
        f"EXPIRE key seconds {_FLAGS}",
    ),
    CommandSpec(
        "PEXPIRE",
        _make_expire_handler("PEXPIRE", 1, absolute=False),
        -3,
        "Set a key's time to live in milliseconds.",
        f"PEXPIRE key milliseconds {_FLAGS}",
    ),
    CommandSpec(
        "EXPIREAT",
        _make_expire_handler("EXPIREAT", 1000, absolute=True),
        -3,
        "Set a key's expiry as a Unix timestamp in seconds.",
        f"EXPIREAT key unix-seconds {_FLAGS}",
    ),
    CommandSpec(
        "PEXPIREAT",
        _make_expire_handler("PEXPIREAT", 1, absolute=True),
        -3,
        "Set a key's expiry as a Unix timestamp in milliseconds.",
        f"PEXPIREAT key unix-ms {_FLAGS}",
    ),
    CommandSpec("TTL", cmd_ttl, 2, "Get a key's time to live in seconds.", "TTL key"),
    CommandSpec("PTTL", cmd_pttl, 2, "Get a key's time to live in milliseconds.", "PTTL key"),
    CommandSpec(
        "EXPIRETIME",
        cmd_expiretime,
        2,
        "Get a key's expiry as a Unix timestamp in seconds.",
        "EXPIRETIME key",
    ),
    CommandSpec(
        "PEXPIRETIME",
        cmd_pexpiretime,
        2,
        "Get a key's expiry as a Unix timestamp in milliseconds.",
        "PEXPIRETIME key",
    ),
    CommandSpec("PERSIST", cmd_persist, 2, "Remove a key's expiry.", "PERSIST key"),
)
