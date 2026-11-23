"""Tests for CLI response formatting."""

from __future__ import annotations

import pytest

from minikv.cli.formatting import format_error, format_response, quote
from minikv.errors import WrongArityError
from minikv.responses import OK, PONG, Response


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (OK, "OK"),
        (PONG, "PONG"),
        (None, "(nil)"),
        (3, "(integer) 3"),
        (0, "(integer) 0"),
        ("hello", '"hello"'),
        ("", '""'),
        ([], "(empty array)"),
        (["a", "b"], '1) "a"\n2) "b"'),
    ],
)
def test_format_response(response: Response, expected: str) -> None:
    assert format_response(response) == expected


def test_format_long_array_aligns_numbers() -> None:
    rendered = format_response([str(i) for i in range(10)]).split("\n")
    assert rendered[0] == ' 1) "0"'
    assert rendered[9] == '10) "9"'


def test_format_nested_array() -> None:
    assert format_response(["a", ["b", "c"]]) == '1) "a"\n2) 1) "b"\n   2) "c"'


def test_quote_escapes_special_characters() -> None:
    assert quote('say "hi"\n\\') == '"say \\"hi\\"\\n\\\\"'


@pytest.mark.parametrize("bad", [True, 1.5, object()])
def test_unsupported_types_raise(bad: object) -> None:
    with pytest.raises(TypeError):
        format_response(bad)  # type: ignore[arg-type]


def test_format_error() -> None:
    assert format_error(WrongArityError("GET")) == (
        "(error) ERR wrong number of arguments for 'get' command"
    )
