"""Tests for RESP2 encoding and the request/reply parsers."""

from __future__ import annotations

import re

import pytest

from minikv.errors import ProtocolError, ReplyError, WrongArityError
from minikv.protocol import (
    ReplyParser,
    RequestParser,
    encode_command,
    encode_error,
    encode_response,
)
from minikv.responses import OK, PONG, Response, SimpleString

# --- encoding -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (OK, b"+OK\r\n"),
        (PONG, b"+PONG\r\n"),
        (None, b"$-1\r\n"),
        (0, b":0\r\n"),
        (-2, b":-2\r\n"),
        ("hello", b"$5\r\nhello\r\n"),
        ("", b"$0\r\n\r\n"),
        ("çay", b"$4\r\n\xc3\xa7ay\r\n"),
        ([], b"*0\r\n"),
        (["a", 1, None], b"*3\r\n$1\r\na\r\n:1\r\n$-1\r\n"),
        ([["x"], []], b"*2\r\n*1\r\n$1\r\nx\r\n*0\r\n"),
    ],
)
def test_encode_response(response: Response, expected: bytes) -> None:
    assert encode_response(response) == expected


def test_encode_simple_string_strips_newlines() -> None:
    assert encode_response(SimpleString("a\r\nb")) == b"+a  b\r\n"


@pytest.mark.parametrize("bad", [True, 1.5, object(), [object()]])
def test_encode_rejects_unsupported_types(bad: object) -> None:
    with pytest.raises(TypeError):
        encode_response(bad)  # type: ignore[arg-type]


def test_encode_error() -> None:
    assert encode_error(WrongArityError("GET")) == (
        b"-ERR wrong number of arguments for 'get' command\r\n"
    )
    assert encode_error("ERR multi\nline") == b"-ERR multi line\r\n"


def test_encode_command() -> None:
    assert encode_command(["SET", "k", "v v"]) == b"*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$3\r\nv v\r\n"


def test_binary_round_trip() -> None:
    raw = bytes(range(256))
    parser = RequestParser()
    parser.feed(b"*2\r\n$4\r\nECHO\r\n$256\r\n" + raw + b"\r\n")
    command = parser.next_command()
    assert command is not None
    assert encode_response(command[1]) == b"$256\r\n" + raw + b"\r\n"


# --- request parser -----------------------------------------------------------


def _parse_all(data: bytes) -> list[list[str]]:
    parser = RequestParser()
    parser.feed(data)
    commands = []
    while (command := parser.next_command()) is not None:
        commands.append(command)
    return commands


def test_multibulk() -> None:
    assert _parse_all(b"*2\r\n$3\r\nGET\r\n$3\r\nkey\r\n") == [["GET", "key"]]


def test_pipelined_requests() -> None:
    data = encode_command(["SET", "a", "1"]) + b"PING\r\n" + encode_command(["GET", "a"])
    assert _parse_all(data) == [["SET", "a", "1"], ["PING"], ["GET", "a"]]


def test_byte_by_byte_feeding() -> None:
    data = encode_command(["SET", "key", "value with spaces"]) + b"PING\r\n"
    parser = RequestParser()
    results = []
    for byte in data:
        parser.feed(bytes([byte]))
        while (command := parser.next_command()) is not None:
            results.append(command)
    assert results == [["SET", "key", "value with spaces"], ["PING"]]
    assert parser.buffered == 0


def test_bulk_strings_may_contain_crlf() -> None:
    assert _parse_all(encode_command(["SET", "k", "a\r\nb"])) == [["SET", "k", "a\r\nb"]]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"PING\r\n", [["PING"]]),
        (b"PING\n", [["PING"]]),
        (b'SET k "a b"\r\n', [["SET", "k", "a b"]]),
        (b"\r\n\r\nPING\r\n", [["PING"]]),
        (b"*0\r\nPING\r\n", [["PING"]]),
        (b"*-1\r\nPING\r\n", [["PING"]]),
    ],
)
def test_inline_and_empty_requests(data: bytes, expected: list[list[str]]) -> None:
    assert _parse_all(data) == expected


def test_incomplete_request_returns_none() -> None:
    parser = RequestParser()
    parser.feed(b"*2\r\n$3\r\nGET\r\n$3\r\nke")
    assert parser.next_command() is None
    parser.feed(b"y\r\n")
    assert parser.next_command() == ["GET", "key"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"*abc\r\n", "invalid multibulk length"),
        (b"*2\r\n:1\r\n", "expected '$', got ':'"),
        (b"*1\r\n$-5\r\n", "invalid bulk length"),
        (b"*1\r\n$x\r\n", "invalid bulk length"),
        (b"*1\r\n$3\r\nabcXY", "not terminated by CRLF"),
        (b'SET k "oops\r\n', "unbalanced quotes"),
    ],
)
def test_protocol_errors(data: bytes, message: str) -> None:
    parser = RequestParser()
    parser.feed(data)
    with pytest.raises(ProtocolError, match=re.escape(message)) as info:
        parser.next_command()
    assert str(info.value).startswith("ERR Protocol error: ")


def test_limits_are_enforced() -> None:
    parser = RequestParser(max_inline_size=16, max_bulk_length=4, max_multibulk_length=2)
    parser.feed(b"x" * 17)
    with pytest.raises(ProtocolError, match="too big inline request"):
        parser.next_command()

    parser = RequestParser(max_bulk_length=4)
    parser.feed(b"*1\r\n$5\r\n")
    with pytest.raises(ProtocolError, match="invalid bulk length"):
        parser.next_command()

    parser = RequestParser(max_multibulk_length=2)
    parser.feed(b"*3\r\n")
    with pytest.raises(ProtocolError, match="invalid multibulk length"):
        parser.next_command()

    parser = RequestParser(max_inline_size=16)
    parser.feed(b"*" + b"1" * 20)
    with pytest.raises(ProtocolError, match="too big mbulk count string"):
        parser.next_command()


# --- reply parser -------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"+OK\r\n", OK),
        (b":42\r\n", 42),
        (b"$5\r\nhello\r\n", "hello"),
        (b"$0\r\n\r\n", ""),
        (b"$-1\r\n", None),
        (b"*-1\r\n", None),
        (b"*0\r\n", []),
        (b"*2\r\n$1\r\na\r\n*1\r\n:1\r\n", ["a", [1]]),
    ],
)
def test_reply_parser(data: bytes, expected: Response) -> None:
    parser = ReplyParser()
    parser.feed(data)
    assert parser.next_reply() == (True, expected)
    assert parser.next_reply() == (False, None)


def test_reply_parser_errors_are_values() -> None:
    parser = ReplyParser()
    parser.feed(b"-WRONGTYPE bad type\r\n*2\r\n-ERR inner\r\n:1\r\n")
    ok, first = parser.next_reply()
    assert ok and isinstance(first, ReplyError)
    assert first.prefix == "WRONGTYPE" and str(first) == "WRONGTYPE bad type"
    ok, second = parser.next_reply()
    assert ok and isinstance(second, list) and isinstance(second[0], ReplyError)


def test_reply_parser_incremental() -> None:
    data = encode_response(["hello", 7, None])
    parser = ReplyParser()
    for byte in data[:-1]:
        parser.feed(bytes([byte]))
        assert parser.next_reply() == (False, None)
    parser.feed(data[-1:])
    assert parser.next_reply() == (True, ["hello", 7, None])


@pytest.mark.parametrize("data", [b"?what\r\n", b":abc\r\n"])
def test_reply_parser_rejects_garbage(data: bytes) -> None:
    parser = ReplyParser()
    parser.feed(data)
    with pytest.raises(ProtocolError):
        parser.next_reply()


def test_reply_error_without_prefix() -> None:
    error = ReplyError("boom")
    assert error.prefix == "ERR" and str(error) == "ERR boom"
