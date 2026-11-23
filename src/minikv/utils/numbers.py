"""Strict integer parsing with Redis semantics."""

from __future__ import annotations

import re

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

_INTEGER_RE = re.compile(r"-?(?:0|[1-9][0-9]*)")


def parse_int64(text: str) -> int | None:
    """Parse ``text`` as a signed 64-bit integer the way Redis does.

    Leading ``+`` signs, leading zeros, whitespace, decimals and values
    outside the signed 64-bit range are rejected.

    Args:
        text: The string to parse.

    Returns:
        The integer, or ``None`` if ``text`` is not a valid 64-bit integer.

    Example:
        >>> parse_int64("42"), parse_int64("4.2"), parse_int64("007")
        (42, None, None)
    """
    if _INTEGER_RE.fullmatch(text) is None or text == "-0":
        return None
    value = int(text)
    if not INT64_MIN <= value <= INT64_MAX:
        return None
    return value


def fits_int64(value: int) -> bool:
    """Return ``True`` if ``value`` fits in a signed 64-bit integer."""
    return INT64_MIN <= value <= INT64_MAX
