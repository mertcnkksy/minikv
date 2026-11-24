"""A small load generator for MiniKV (and Redis) servers.

Similar in spirit to ``redis-benchmark`` but written with the standard
library only, so it runs wherever MiniKV runs (including Windows and the
Docker image). Each client is a thread with its own connection; requests
can be pipelined to measure throughput without per-request round trips.

Because the clients are Python threads, very fast servers can be limited
by the benchmark itself; use ``redis-benchmark`` for maximum load. The tool
is meant for comparing MiniKV configurations and versions with each other.

Example:
    >>> from minikv.benchmark import BenchmarkConfig, run_benchmark   # doctest: +SKIP
    >>> results = run_benchmark(BenchmarkConfig(port=6380, requests=10_000))
"""

from __future__ import annotations

import statistics
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from minikv.client import MiniKVClient
from minikv.constants import DEFAULT_HOST, DEFAULT_PORT
from minikv.errors import ReplyError

SUPPORTED_TESTS: tuple[str, ...] = ("PING", "SET", "GET")
"""Commands the benchmark can generate."""


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Benchmark settings.

    Attributes:
        host: Server host.
        port: Server port.
        requests: Total requests per test.
        clients: Parallel connections (one thread each).
        pipeline: Requests sent per round trip.
        data_size: Size of ``SET`` values in bytes.
        keyspace: Number of distinct keys used by ``SET``/``GET``.
        tests: Commands to benchmark, in order (``SET`` before ``GET`` so
            the keys exist).
        timeout: Socket timeout in seconds.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    requests: int = 10_000
    clients: int = 10
    pipeline: int = 1
    data_size: int = 3
    keyspace: int = 10_000
    tests: tuple[str, ...] = SUPPORTED_TESTS
    timeout: float = 10.0

    def __post_init__(self) -> None:
        for name in ("requests", "clients", "pipeline", "keyspace"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.data_size < 0:
            raise ValueError("data_size must not be negative")
        tests = tuple(test.upper() for test in self.tests)
        unknown = [test for test in tests if test not in SUPPORTED_TESTS]
        if unknown or not tests:
            supported = ", ".join(SUPPORTED_TESTS)
            raise ValueError(f"unknown tests {unknown or '(none)'}; supported: {supported}")
        object.__setattr__(self, "tests", tests)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Outcome of one benchmarked command.

    Latencies are per round trip (a whole pipeline batch), in milliseconds.
    """

    test: str
    requests: int
    clients: int
    pipeline: int
    seconds: float
    errors: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    @property
    def requests_per_second(self) -> float:
        """Throughput over the whole test."""
        return self.requests / self.seconds if self.seconds > 0 else float("inf")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dictionary, including the throughput."""
        data = asdict(self)
        data["requests_per_second"] = round(self.requests_per_second, 2)
        return data


def _command(test: str, index: int, value: str, keyspace: int) -> list[str]:
    key = f"key:{index % keyspace}"
    if test == "SET":
        return ["SET", key, value]
    if test == "GET":
        return ["GET", key]
    return ["PING"]


def _percentiles(samples: list[float]) -> tuple[float, float, float, float]:
    if len(samples) == 1:
        only = samples[0]
        return only, only, only, only
    cuts = statistics.quantiles(samples, n=100, method="inclusive")
    return cuts[49], cuts[94], cuts[98], max(samples)


def _split(total: int, parts: int) -> list[range]:
    """Split ``range(total)`` into ``parts`` contiguous, nearly equal ranges."""
    size, extra = divmod(total, parts)
    ranges, start = [], 0
    for part in range(parts):
        end = start + size + (1 if part < extra else 0)
        ranges.append(range(start, end))
        start = end
    return ranges


def _run_test(config: BenchmarkConfig, test: str) -> BenchmarkResult:
    clients = min(config.clients, config.requests)
    value = "x" * config.data_size
    barrier = threading.Barrier(clients + 1)
    latencies: list[list[float]] = [[] for _ in range(clients)]
    errors = [0] * clients
    failures: list[BaseException] = []

    def worker(slot: int, indexes: range) -> None:
        client = MiniKVClient(config.host, config.port, timeout=config.timeout)
        try:
            client.connect()
        except BaseException as exc:
            failures.append(exc)
            barrier.abort()
            return
        try:
            barrier.wait()
            for start in range(indexes.start, indexes.stop, config.pipeline):
                stop = min(start + config.pipeline, indexes.stop)
                batch = [_command(test, i, value, config.keyspace) for i in range(start, stop)]
                began = time.perf_counter()
                replies = client.pipeline(batch)
                latencies[slot].append((time.perf_counter() - began) * 1000)
                errors[slot] += sum(isinstance(reply, ReplyError) for reply in replies)
        except threading.BrokenBarrierError:
            pass
        except BaseException as exc:
            failures.append(exc)
        finally:
            client.close()

    threads = [
        threading.Thread(target=worker, args=(slot, indexes), daemon=True)
        for slot, indexes in enumerate(_split(config.requests, clients))
    ]
    for thread in threads:
        thread.start()
    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        pass
    started = time.perf_counter()
    for thread in threads:
        thread.join()
    elapsed = time.perf_counter() - started

    if failures:
        failure = failures[0]
        if isinstance(failure, ConnectionError):
            raise failure
        raise ConnectionError(f"benchmark client failed: {failure}") from failure

    samples = [sample for per_client in latencies for sample in per_client]
    p50, p95, p99, worst = _percentiles(samples)
    return BenchmarkResult(
        test=test,
        requests=config.requests,
        clients=clients,
        pipeline=config.pipeline,
        seconds=elapsed,
        errors=sum(errors),
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        max_ms=worst,
    )


def run_benchmark(config: BenchmarkConfig) -> list[BenchmarkResult]:
    """Run every configured test against a running server.

    Raises:
        ConnectionError: If the server cannot be reached or a connection fails.
    """
    return [_run_test(config, test) for test in config.tests]


def format_results(results: Sequence[BenchmarkResult]) -> str:
    """Render results as an aligned text table."""
    header = ("Test", "Requests", "Clients", "Pipeline", "Req/s", "p50 ms", "p95 ms", "p99 ms",
              "max ms", "Errors")
    rows = [
        (
            r.test, f"{r.requests:,}", str(r.clients), str(r.pipeline),
            f"{r.requests_per_second:,.0f}", f"{r.p50_ms:.3f}", f"{r.p95_ms:.3f}",
            f"{r.p99_ms:.3f}", f"{r.max_ms:.3f}", str(r.errors),
        )
        for r in results
    ]
    widths = [max(len(row[i]) for row in (header, *rows)) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i])
                       for i, cell in enumerate(row)) for row in (header, *rows)]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)
