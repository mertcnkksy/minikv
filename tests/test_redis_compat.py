"""Compatibility tests against official Redis tooling.

These tests are skipped automatically when ``redis-py`` or ``redis-cli`` is
not installed, so they never become a hard dependency.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.helpers import ServerThread


def test_redis_py_client(server: ServerThread) -> None:
    redis = pytest.importorskip("redis")
    client = redis.Redis(port=server.port, protocol=2, decode_responses=True)
    try:
        assert client.ping() is True
        assert client.set("user:1", "Ada", ex=60) is True
        assert client.get("user:1") == "Ada"
        assert client.ttl("user:1") == 60
        assert client.set("user:1", "Grace", nx=True) is None
        assert client.exists("user:1", "missing") == 1
        assert client.keys("user:*") == ["user:1"]
        assert client.expire("user:1", 10, gt=True) is False
        assert client.persist("user:1") is True
        assert client.delete("user:1") == 1

        pipe = client.pipeline(transaction=False)
        for i in range(500):
            pipe.set(f"k{i}", i)
        assert all(pipe.execute())
        assert client.dbsize() == 500

        with pytest.raises(redis.ResponseError, match="unknown command"):
            client.execute_command("BOGUS")
    finally:
        client.close()


def test_redis_py_binary_values(server: ServerThread) -> None:
    redis = pytest.importorskip("redis")
    client = redis.Redis(port=server.port, protocol=2)
    try:
        payload = bytes(range(256)) * 10
        client.set(b"\x00bin\xff", payload)
        assert client.get(b"\x00bin\xff") == payload
    finally:
        client.close()


@pytest.mark.skipif(shutil.which("redis-cli") is None, reason="redis-cli is not installed")
def test_redis_cli(server: ServerThread) -> None:
    def cli(*args: str) -> str:
        result = subprocess.run(
            ["redis-cli", "-p", str(server.port), *args],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout.strip()

    assert cli("PING") == "PONG"
    assert cli("SET", "greeting", "hello world", "EX", "100") == "OK"
    assert cli("GET", "greeting") == "hello world"
    assert cli("TTL", "greeting") == "100"
    assert cli("BOGUS").startswith("ERR unknown command")
