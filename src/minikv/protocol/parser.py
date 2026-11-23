"""Tokenise human-typed command lines.

The CLI accepts inline commands similar to ``redis-cli``: whitespace
separates arguments and single or double quotes group arguments containing
spaces. A RESP parser for network clients will live alongside this module.
"""

from __future__ import annotations

import shlex

from minikv.errors import ParseError


def parse_command(line: str) -> list[str]:
    """Split a command line into tokens.

    Args:
        line: Raw text such as ``SET name "Ada Lovelace"``.

    Returns:
        The list of tokens. An empty or whitespace-only line yields ``[]``.

    Raises:
        ParseError: If the line contains unbalanced quotes.

    Example:
        >>> parse_command('SET name "Ada Lovelace"')
        ['SET', 'name', 'Ada Lovelace']
    """
    try:
        return shlex.split(line, posix=True)
    except ValueError as exc:
        raise ParseError("invalid request: unbalanced quotes") from exc
