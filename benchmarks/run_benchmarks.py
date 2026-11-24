#!/usr/bin/env python3
"""Compare MiniKV throughput and latency across persistence modes.

Each mode starts a fresh server process with its own temporary data
directory, runs ``minikv.benchmark`` against it and stops it gracefully.
Results are printed as a Markdown table and can be saved as Markdown and JSON.

    python benchmarks/run_benchmarks.py
    python benchmarks/run_benchmarks.py --requests 50000 --pipelines 1,16 \\
        --output benchmark-results.md --json benchmark-results.json

The script only needs the standard library and MiniKV itself; it works on
Linux, macOS and Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    import minikv
except ImportError:  # running from a source checkout without installing
    sys.path.insert(0, str(ROOT / "src"))
    import minikv

from minikv.benchmark import BenchmarkConfig, BenchmarkResult, run_benchmark
from minikv.client import MiniKVClient

MODES: dict[str, list[str]] = {
    "memory": ["--save", ""],
    "aof-everysec": ["--save", "", "--appendonly", "--appendfsync", "everysec"],
    "aof-always": ["--save", "", "--appendonly", "--appendfsync", "always"],
}
WINDOWS = sys.platform == "win32"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(port: int, data_dir: Path, options: list[str]) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")])}
    # A separate process group on Windows lets us send CTRL_BREAK_EVENT to the server only.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0
    process = subprocess.Popen(
        [sys.executable, "-m", "minikv", "serve", "--port", str(port), "--dir", str(data_dir),
         "--log-level", "WARNING", *options],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, creationflags=flags,
    )
    client = MiniKVClient(port=port, timeout=1)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{process.communicate()[0]}")
        try:
            client.execute("PING")
            client.close()
            return process
        except ConnectionError:
            time.sleep(0.05)
    process.kill()
    raise RuntimeError("server did not start in time")


def _stop_server(process: subprocess.Popen[str]) -> None:
    process.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGTERM)
    try:
        process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise RuntimeError("server did not stop gracefully") from None
    if process.returncode != 0:
        raise RuntimeError(f"server exited with code {process.returncode}")


def run(modes: list[str], pipelines: list[int], requests: int, clients: int,
        data_size: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for mode in modes:
        with tempfile.TemporaryDirectory(prefix=f"minikv-bench-{mode}-") as data_dir:
            port = _free_port()
            process = _start_server(port, Path(data_dir), MODES[mode])
            try:
                for pipeline in pipelines:
                    config = BenchmarkConfig(
                        port=port, requests=requests, clients=clients, pipeline=pipeline,
                        data_size=data_size, tests=("SET", "GET"),
                    )
                    results: list[BenchmarkResult] = run_benchmark(config)
                    for result in results:
                        rows.append({"mode": mode, **result.to_dict()})
                        print(f"  {mode:13s} {result.test:4s} pipeline={pipeline:<3d} "
                              f"{result.requests_per_second:>10,.0f} req/s", file=sys.stderr)
            finally:
                if process.poll() is None:
                    _stop_server(process)
    return rows


def to_markdown(rows: list[dict[str, object]], environment: dict[str, object]) -> str:
    lines = [
        "# MiniKV benchmark results",
        "",
        f"MiniKV {environment['minikv']} · Python {environment['python']} · "
        f"{environment['platform']} · {environment['cpus']} CPUs · "
        f"{environment['requests']:,} requests · {environment['clients']} clients",
        "",
        "| Mode | Test | Pipeline | Req/s | p50 ms | p99 ms | Errors |",
        "|------|------|---------:|------:|-------:|-------:|-------:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['mode']} | {row['test']} | {row['pipeline']} | "
            f"{row['requests_per_second']:,.0f} | {row['p50_ms']:.3f} | "
            f"{row['p99_ms']:.3f} | {row['errors']} |"
        )
    lines += ["", "Latency is measured per round trip (a whole pipeline batch)."]
    return "\n".join(lines) + "\n"


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modes", type=_csv, default=list(MODES),
                        help=f"comma-separated modes (default: {','.join(MODES)})")
    parser.add_argument("--pipelines", type=_csv, default=["1", "16"],
                        help="comma-separated pipeline depths (default: 1,16)")
    parser.add_argument("-n", "--requests", type=int, default=20_000, help="requests per test")
    parser.add_argument("-c", "--clients", type=int, default=10, help="parallel clients")
    parser.add_argument("-d", "--data-size", type=int, default=3, help="SET value size in bytes")
    parser.add_argument("--output", type=Path, help="write the Markdown table to this file")
    parser.add_argument("--json", type=Path, help="write raw results as JSON to this file")
    args = parser.parse_args(argv)

    unknown = [mode for mode in args.modes if mode not in MODES]
    if unknown:
        parser.error(f"unknown modes: {', '.join(unknown)}; choose from {', '.join(MODES)}")
    pipelines = [int(value) for value in args.pipelines]

    environment = {
        "minikv": minikv.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "cpus": os.cpu_count(),
        "requests": args.requests,
        "clients": args.clients,
    }
    print(f"Running {len(args.modes)} mode(s) x {len(pipelines)} pipeline depth(s)...", file=sys.stderr)
    rows = run(args.modes, pipelines, args.requests, args.clients, args.data_size)

    markdown = to_markdown(rows, environment)
    print(markdown)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps({"environment": environment, "results": rows}, indent=2),
                             encoding="utf-8")
    return 1 if any(row["errors"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
