"""Tests for client pipelining, the benchmark module, its CLI and comparison script."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from minikv import Database
from minikv.benchmark import BenchmarkConfig, _split, format_results, run_benchmark
from minikv.cli import main
from minikv.client import MiniKVClient
from minikv.commands import BUILTIN_COMMANDS, CommandRegistry
from minikv.errors import ReplyError
from minikv.responses import OK, PONG
from tests.helpers import SRC_DIR, ServerThread, free_port

Factory = Callable[..., ServerThread]
ROOT = Path(__file__).resolve().parents[1]

# --- MiniKVClient.pipeline ------------------------------------------------------


def test_pipeline_returns_replies_in_order(server: ServerThread) -> None:
    with MiniKVClient(port=server.port) as client:
        replies = client.pipeline([["SET", "a", "1"], ["GET", "a"], ["GET", "missing"], ["PING"]])
    assert replies == [OK, "1", None, PONG]


def test_pipeline_returns_errors_as_values(server: ServerThread) -> None:
    with MiniKVClient(port=server.port) as client:
        replies = client.pipeline([["BOGUS"], ["SET", "k", "v"], ["GET"]])
        assert isinstance(replies[0], ReplyError) and isinstance(replies[2], ReplyError)
        assert replies[1] == OK
        assert client.execute("GET", "k") == "v"  # connection still in sync


def test_pipeline_edge_cases(server: ServerThread) -> None:
    client = MiniKVClient(port=server.port)
    assert client.pipeline([]) == []
    assert not client.connected  # nothing to send, so no connection opened
    with pytest.raises(ValueError):
        client.pipeline([["PING"], []])
    assert client.pipeline([["PING"], ["QUIT"]]) == [PONG, OK]
    assert not client.connected
    assert client.execute("PING") == PONG
    client.close()


def test_pipeline_connection_refused() -> None:
    with pytest.raises(ConnectionError):
        MiniKVClient(port=free_port()).pipeline([["PING"]])


# --- run_benchmark --------------------------------------------------------------


@pytest.mark.parametrize("pipeline", [1, 10])
def test_run_benchmark(server: ServerThread, pipeline: int) -> None:
    config = BenchmarkConfig(port=server.port, requests=300, clients=3, pipeline=pipeline,
                             keyspace=50, data_size=8)
    results = run_benchmark(config)
    assert [result.test for result in results] == ["PING", "SET", "GET"]
    for result in results:
        assert result.requests == 300 and result.errors == 0
        assert result.clients == 3 and result.pipeline == pipeline
        assert result.requests_per_second > 0
        assert 0 <= result.p50_ms <= result.p95_ms <= result.p99_ms <= result.max_ms
    assert server.db.execute("DBSIZE") == 50
    assert server.db.execute("GET", "key:7") == "x" * 8


def test_errors_are_counted(server_factory: Factory) -> None:
    registry = CommandRegistry(spec for spec in BUILTIN_COMMANDS if spec.name != "GET")
    running = server_factory(Database(registry=registry))
    (result,) = run_benchmark(BenchmarkConfig(port=running.port, requests=40, clients=2, tests=("get",)))
    assert result.errors == 40


def test_more_clients_than_requests(server: ServerThread) -> None:
    (result,) = run_benchmark(BenchmarkConfig(port=server.port, requests=2, clients=8, tests=("PING",)))
    assert result.clients == 2 and result.requests == 2


def test_unreachable_server() -> None:
    with pytest.raises(ConnectionError, match="Could not connect"):
        run_benchmark(BenchmarkConfig(port=free_port(), requests=10, clients=2))


def test_split_is_exhaustive() -> None:
    parts = _split(10, 3)
    assert [len(part) for part in parts] == [4, 3, 3]
    assert [i for part in parts for i in part] == list(range(10))


@pytest.mark.parametrize(
    "kwargs",
    [{"requests": 0}, {"clients": 0}, {"pipeline": 0}, {"keyspace": 0}, {"data_size": -1},
     {"tests": ("INCR",)}, {"tests": ()}],
)
def test_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        BenchmarkConfig(**kwargs)  # type: ignore[arg-type]


def test_format_results(server: ServerThread) -> None:
    results = run_benchmark(BenchmarkConfig(port=server.port, requests=20, clients=1))
    table = format_results(results).splitlines()
    assert table[0].split()[:5] == ["Test", "Requests", "Clients", "Pipeline", "Req/s"]
    assert set(table[1]) <= {"-", " "}
    assert [line.split()[0] for line in table[2:]] == ["PING", "SET", "GET"]


# --- minikv benchmark CLI ---------------------------------------------------------


def test_cli_benchmark_json(server: ServerThread, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["benchmark", "-p", str(server.port), "-n", "50", "-c", "2", "-P", "5",
                 "-t", "ping,SET", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert [item["test"] for item in data] == ["PING", "SET"]
    assert all(item["requests_per_second"] > 0 and item["pipeline"] == 5 for item in data)


def test_cli_benchmark_table(server: ServerThread, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["benchmark", "-p", str(server.port), "-n", "30", "-c", "2"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Benchmarking MiniKV at 127.0.0.1:{server.port}")
    assert "Req/s" in out and "GET" in out


def test_cli_benchmark_failures(server_factory: Factory, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["benchmark", "-p", str(free_port()), "-n", "10"]) == 1
    assert "Could not connect" in capsys.readouterr().err

    registry = CommandRegistry(spec for spec in BUILTIN_COMMANDS if spec.name != "GET")
    running = server_factory(Database(registry=registry))
    assert main(["benchmark", "-p", str(running.port), "-n", "10", "-t", "get"]) == 1
    assert "10 request(s) failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [["-t", "incr"], ["-t", ","], ["-n", "0"], ["-c", "0"], ["-P", "0"], ["-d", "-1"]],
)
def test_cli_benchmark_rejects_bad_options(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["benchmark", *argv])
    assert info.value.code == 2


# --- benchmarks/run_benchmarks.py ---------------------------------------------------


def test_comparison_script(tmp_path: Path) -> None:
    output, raw = tmp_path / "results.md", tmp_path / "results.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "run_benchmarks.py"),
         "--modes", "memory,aof-always", "--pipelines", "1,4", "-n", "200", "-c", "2",
         "--output", str(output), "--json", str(raw)],
        capture_output=True, text=True, timeout=120, env={**_env(), "PYTHONPATH": str(SRC_DIR)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    markdown = output.read_text(encoding="utf-8")
    assert "| memory | SET | 1 |" in markdown and "| aof-always | GET | 4 |" in markdown
    data = json.loads(raw.read_text(encoding="utf-8"))
    assert len(data["results"]) == 2 * 2 * 2
    assert all(row["errors"] == 0 for row in data["results"])


def test_comparison_script_rejects_unknown_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "benchmarks" / "run_benchmarks.py"), "--modes", "turbo"],
        capture_output=True, text=True, timeout=60, env={**_env(), "PYTHONPATH": str(SRC_DIR)},
    )
    assert result.returncode == 2 and "unknown modes: turbo" in result.stderr


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)
