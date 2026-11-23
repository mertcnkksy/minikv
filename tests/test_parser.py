"""Tests for the command-line tokenizer."""

from __future__ import annotations

import pytest

from minikv.errors import ParseError
from minikv.protocol import parse_command


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("PING", ["PING"]),
        ("SET key value", ["SET", "key", "value"]),
        ("  SET   key    value  ", ["SET", "key", "value"]),
        ('SET name "Ada Lovelace"', ["SET", "name", "Ada Lovelace"]),
        ("SET name 'Ada Lovelace'", ["SET", "name", "Ada Lovelace"]),
        ('SET empty ""', ["SET", "empty", ""]),
        ("", []),
        ("   ", []),
    ],
)
def test_parse_command(line: str, expected: list[str]) -> None:
    assert parse_command(line) == expected


@pytest.mark.parametrize("line", ['SET key "unterminated', "SET key 'oops"])
def test_unbalanced_quotes_raise_parse_error(line: str) -> None:
    with pytest.raises(ParseError, match="unbalanced quotes"):
        parse_command(line)
