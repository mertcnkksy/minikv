"""Redis-style glob pattern matching.

Supported syntax (matching Redis ``KEYS``):

* ``*`` matches any sequence of characters, including none.
* ``?`` matches exactly one character.
* ``[abc]`` matches one of the listed characters.
* ``[^abc]`` matches any character not listed.
* ``[a-z]`` matches a character range (reversed ranges are accepted).
* ``\\x`` matches the character ``x`` literally.

An unterminated ``[`` is treated as a literal character.
"""

from __future__ import annotations

import re
from functools import lru_cache


def _read_char(pattern: str, index: int) -> tuple[str, int]:
    """Read one (possibly escaped) character starting at ``index``.

    Returns:
        The character and the index just after it.
    """
    if pattern[index] == "\\" and index + 1 < len(pattern):
        return pattern[index + 1], index + 2
    return pattern[index], index + 1


def _translate_class(pattern: str, start: int) -> tuple[str, int] | None:
    """Translate a ``[...]`` character class beginning after ``[``.

    Returns:
        The regex fragment and the index just after ``]``, or ``None`` if
        the class is not terminated.
    """
    length = len(pattern)
    index = start
    negate = index < length and pattern[index] == "^"
    if negate:
        index += 1

    parts: list[str] = []
    while index < length and pattern[index] != "]":
        first, index = _read_char(pattern, index)
        if index + 1 < length and pattern[index] == "-" and pattern[index + 1] != "]":
            last, index = _read_char(pattern, index + 1)
            low, high = sorted((first, last))
            parts.append(f"{re.escape(low)}-{re.escape(high)}")
        else:
            parts.append(re.escape(first))

    if index >= length or not parts:
        return None
    return f"[{'^' if negate else ''}{''.join(parts)}]", index + 1


@lru_cache(maxsize=256)
def compile_glob(pattern: str) -> re.Pattern[str]:
    """Compile a Redis-style glob pattern into a regular expression.

    Args:
        pattern: The glob pattern.

    Returns:
        A compiled regex; use :meth:`re.Pattern.fullmatch` to test keys.
    """
    fragments: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            fragments.append(".*")
            index += 1
        elif char == "?":
            fragments.append(".")
            index += 1
        elif char == "[":
            translated = _translate_class(pattern, index + 1)
            if translated is None:
                fragments.append(re.escape(char))
                index += 1
            else:
                fragment, index = translated
                fragments.append(fragment)
        else:
            literal, index = _read_char(pattern, index)
            fragments.append(re.escape(literal))
    return re.compile("".join(fragments), re.DOTALL)


def glob_match(pattern: str, text: str) -> bool:
    """Return ``True`` if ``text`` matches the glob ``pattern`` entirely."""
    return compile_glob(pattern).fullmatch(text) is not None
