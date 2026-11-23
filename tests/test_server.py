"""Integration tests for the asyncio TCP server over real sockets."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.protocol import encode_command
from minikv.server import MiniKVServer, ServerConfig
from minikv.storage import InMemoryStorage
from tests.helpers import ServerThread, connect, recv_exactly, recv_until_closed, wait_for

Factory = Callable[..., ServerThread]


def test_ping_multibulk(server: ServerThread) -> None:
    with connect(server.port) as sock:
        sock.sendall(encode_command(["PING"]))
        assert recv_exactly(sock, 7) == b"+PONG\r\n"


def test_inline_commands(server: ServerThread) -> None:
    with connect(server.port) as sock:
        sock.sendall(b'SET greeting "hello world"\r\nGET greeting\n')
        expected = b"+OK\r\n$11\r\nhello world\r\n"
        assert recv_exactly(sock, len(expected)) == expected


def test_pipelining_returns_replies_in_order(server: ServerThread) -> None:
    batch = b"".join(encode_command(["SET", f"k{i}", str(i)]) for i in range(100))
    batch += encode_command(["DBSIZE"]) + encode_command(["GET", "k42"])
    expected = b"+OK\r\n" * 100 + b":100\r\n" + b"$2\r\n42\r\n"
    with connect(server.port) as sock:
        sock.sendall(batch)
        assert recv_exactly(sock, len(expected)) == expected


def test_requests_split_across_packets(server: ServerThread) -> None:
    data = encode_command(["SET", "split", "value"]) + encode_command(["GET", "split"])
    expected = b"+OK\r\n$5\r\nvalue\r\n"
    with connect(server.port) as sock:
        for byte in data:
            sock.sendall(bytes([byte]))
        assert recv_exactly(sock, len(expected)) == expected


def test_binary_safe_values(server: ServerThread) -> None:
    raw = bytes(range(256)) * 4
    with connect(server.port) as sock:
        sock.sendall(b"*3\r\n$3\r\nSET\r\n$3\r\nbin\r\n$1024\r\n" + raw + b"\r\n")
        assert recv_exactly(sock, 5) == b"+OK\r\n"
        sock.sendall(encode_command(["GET", "bin"]))
        assert recv_exactly(sock, 9 + 1024) == b"$1024\r\n" + raw + b"\r\n"


def test_large_value(server: ServerThread) -> None:
    value = "x" * (2 * 1024 * 1024)
    with connect(server.port) as sock:
        sock.sendall(encode_command(["SET", "big", value]) + encode_command(["GET", "big"]))
        header = b"+OK\r\n$2097152\r\n"
        reply = recv_exactly(sock, len(header) + len(value) + 2)
        assert reply.startswith(header) and len(reply) == len(header) + len(value) + 2


def test_command_errors_keep_connection_open(server: ServerThread) -> None:
    with connect(server.port) as sock:
        sock.sendall(encode_command(["NOPE"]) + encode_command(["GET"]) + b"PING\r\n")
        expected = (
            b"-ERR unknown command 'NOPE', with args beginning with:\r\n"
            b"-ERR wrong number of arguments for 'get' command\r\n"
            b"+PONG\r\n"
        )
        assert recv_exactly(sock, len(expected)) == expected


def test_protocol_error_closes_connection(server: ServerThread) -> None:
    with connect(server.port) as sock:
        sock.sendall(b"PING\r\n*1\r\n:oops\r\nPING\r\n")
        assert recv_until_closed(sock) == (
            b"+PONG\r\n-ERR Protocol error: expected '$', got ':'\r\n"
        )


def test_quit_replies_ok_and_closes(server: ServerThread) -> None:
    with connect(server.port) as sock:
        sock.sendall(b"QUIT\r\nPING\r\n")
        assert recv_until_closed(sock) == b"+OK\r\n"
    assert wait_for(lambda: server.server.client_count == 0)


def test_clients_share_the_database(server: ServerThread) -> None:
    with connect(server.port) as first, connect(server.port) as second:
        first.sendall(encode_command(["SET", "shared", "yes"]))
        assert recv_exactly(first, 5) == b"+OK\r\n"
        second.sendall(encode_command(["GET", "shared"]))
        assert recv_exactly(second, 9) == b"$3\r\nyes\r\n"
        assert server.server.client_count == 2


def test_max_clients(server_factory: Factory) -> None:
    running = server_factory(max_clients=1)
    with connect(running.port) as first:
        first.sendall(b"PING\r\n")
        assert recv_exactly(first, 7) == b"+PONG\r\n"
        with connect(running.port) as second:
            assert recv_until_closed(second) == b"-ERR max number of clients reached\r\n"
        first.sendall(b"PING\r\n")
        assert recv_exactly(first, 7) == b"+PONG\r\n"


def test_internal_errors_are_reported_not_fatal(server_factory: Factory) -> None:
    class BrokenStorage(InMemoryStorage):
        def get(self, key: str) -> str | None:
            raise RuntimeError("disk on fire")

    running = server_factory(Database(storage=BrokenStorage()))
    with connect(running.port) as sock:
        sock.sendall(b"GET k\r\nPING\r\n")
        expected = b"-ERR internal server error\r\n+PONG\r\n"
        assert recv_exactly(sock, len(expected)) == expected


def test_background_expiry_cycle(server_factory: Factory) -> None:
    clock = ManualClock()
    database = Database(clock=clock)
    running = server_factory(database, expiry_interval=0.01)
    for i in range(20):
        database.execute("SET", f"tmp{i}", "v", "PX", "100")
    database.execute("SET", "keep", "v")
    clock.advance(milliseconds=100)
    # No client sends a command: only the background cycle can reclaim memory.
    assert wait_for(lambda: len(database.storage._data) == 1)  # noqa: SLF001
    assert running.db is database


def test_stop_disconnects_clients(server_factory: Factory) -> None:
    running = server_factory()
    sock = connect(running.port)
    sock.sendall(b"PING\r\n")
    assert recv_exactly(sock, 7) == b"+PONG\r\n"
    running.call(running.server.stop())
    assert recv_until_closed(sock) == b""
    sock.close()
    assert not running.server.is_serving
    running.call(running.server.stop())  # idempotent


def test_server_introspection(server: ServerThread) -> None:
    host, port = server.server.address
    assert host == "127.0.0.1" and port > 0
    assert server.server.is_serving
    with pytest.raises(RuntimeError, match="already running"):
        server.call(server.server.start())


def test_address_requires_running_server() -> None:
    with pytest.raises(RuntimeError, match="not running"):
        _ = MiniKVServer().address


def test_async_context_manager() -> None:
    async def scenario() -> bytes:
        async with MiniKVServer(config=ServerConfig(port=0)) as server:
            reader, writer = await asyncio.open_connection(*server.address)
            writer.write(encode_command(["ECHO", "async"]))
            await writer.drain()
            reply = await reader.readexactly(11)
            writer.close()
            await writer.wait_closed()
        assert not server.is_serving
        return reply

    assert asyncio.run(scenario()) == b"$5\r\nasync\r\n"


@pytest.mark.parametrize(
    "kwargs",
    [{"port": -1}, {"port": 70000}, {"max_clients": 0}, {"expiry_interval": -1}, {"read_size": 0}],
)
def test_config_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ServerConfig(**kwargs)  # type: ignore[arg-type]
