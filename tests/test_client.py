"""Tests for the blocking RESP client against a live server."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from minikv.client import MiniKVClient
from minikv.errors import ReplyError
from minikv.responses import OK, PONG
from tests.helpers import ServerThread, free_port


def test_round_trip_of_every_reply_type(server: ServerThread) -> None:
    with MiniKVClient(port=server.port) as client:
        assert client.execute("PING") == PONG
        assert client.execute("SET", "name", "Ada Lovelace", "EX", "60") == OK
        assert client.execute("GET", "name") == "Ada Lovelace"
        assert client.execute("GET", "missing") is None
        assert client.execute("TTL", "name") == 60
        assert client.execute("KEYS", "*") == ["name"]
        hello = client.execute("HELLO")
        assert isinstance(hello, list) and hello[-1] == []


def test_error_replies_raise(server: ServerThread) -> None:
    with MiniKVClient(port=server.port) as client:
        with pytest.raises(ReplyError) as info:
            client.execute("BOGUS")
        assert info.value.prefix == "ERR"
        with pytest.raises(ReplyError) as info:
            client.execute("HELLO", "3")
        assert info.value.prefix == "NOPROTO"
        assert client.execute("PING") == PONG  # connection still usable


def test_unicode_and_binary_values(server: ServerThread) -> None:
    binary = bytes(range(256)).decode("utf-8", "surrogateescape")
    with MiniKVClient(port=server.port) as client:
        client.execute("SET", "tr", "çay ve simit ☕")
        client.execute("SET", "bin", binary)
        assert client.execute("GET", "tr") == "çay ve simit ☕"
        assert client.execute("GET", "bin") == binary


def test_lazy_connect_and_close() -> None:
    client = MiniKVClient(port=1)
    assert not client.connected
    client.close()  # closing an unopened client is fine


def test_connection_refused() -> None:
    port = free_port()
    client = MiniKVClient(port=port)
    with pytest.raises(ConnectionError, match=f"Could not connect to MiniKV at 127.0.0.1:{port}"):
        client.execute("PING")
    assert not client.connected


def test_quit_closes_and_next_command_reconnects(server: ServerThread) -> None:
    client = MiniKVClient(port=server.port)
    assert client.execute("QUIT") == OK
    assert not client.connected
    assert client.execute("PING") == PONG
    client.close()


def test_reconnects_after_server_restart(server_factory: Callable[..., ServerThread]) -> None:
    first = server_factory()
    port = first.port
    client = MiniKVClient(port=port)
    assert client.execute("PING") == PONG
    first.call(first.server.stop())

    with pytest.raises(ConnectionError):
        client.execute("PING")
    assert not client.connected

    server_factory(port=port)
    assert client.execute("PING") == PONG
    client.close()


def test_empty_command_rejected() -> None:
    with pytest.raises(ValueError):
        MiniKVClient().execute()
