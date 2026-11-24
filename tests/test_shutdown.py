"""Tests for cross-platform graceful shutdown.

Signals are raised in-process with :func:`signal.raise_signal`, which works
on both Unix and Windows (including ``SIGBREAK``), so these tests exercise
the real handler path of whichever platform runs them.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import minikv.server.shutdown as shutdown_module
from minikv import Database
from minikv.persistence import PersistenceConfig, PersistenceManager, read_snapshot
from minikv.server import MiniKVServer, ServerConfig, ShutdownSignals, run_server, shutdown_signals

SIGNAL_NAMES = [sig.name for sig in shutdown_signals()]


def _noop(signum: int, frame: object) -> None:
    """Safety handler: a stray signal must never kill the test runner."""


@pytest.fixture
def safe_signals() -> Iterator[dict[signal.Signals, Any]]:
    """Replace shutdown signal handlers with no-ops, restoring the originals after."""
    originals = {sig: signal.signal(sig, _noop) for sig in shutdown_signals()}
    yield originals
    for sig, handler in originals.items():
        signal.signal(sig, handler)


# --- platform coverage --------------------------------------------------------


def test_shutdown_signals_for_this_platform() -> None:
    signals = shutdown_signals()
    assert signal.SIGINT in signals
    assert signal.SIGTERM in signals
    assert (getattr(signal, "SIGBREAK", None) in signals) == (sys.platform == "win32")


# --- ShutdownSignals ------------------------------------------------------------


async def _raise_and_wait(sig: signal.Signals, use_loop_handlers: bool | None) -> tuple[Any, ...]:
    with ShutdownSignals(use_loop_handlers=use_loop_handlers) as shutdown:
        assert sig in shutdown.handled
        signal.raise_signal(sig)
        received = await asyncio.wait_for(shutdown.wait(poll_interval=0.05), timeout=5)
        return received, shutdown.received


@pytest.mark.usefixtures("safe_signals")
@pytest.mark.parametrize("use_loop_handlers", [None, False], ids=["platform-default", "python-handlers"])
@pytest.mark.parametrize("sig_name", SIGNAL_NAMES)
def test_signal_requests_shutdown(sig_name: str, use_loop_handlers: bool | None) -> None:
    sig = getattr(signal, sig_name)
    received, recorded = asyncio.run(_raise_and_wait(sig, use_loop_handlers))
    assert received == sig and recorded == sig


@pytest.mark.parametrize("use_loop_handlers", [None, False], ids=["platform-default", "python-handlers"])
def test_previous_handlers_are_restored(
    safe_signals: dict[signal.Signals, Any], use_loop_handlers: bool | None
) -> None:
    async def scenario() -> None:
        with ShutdownSignals(use_loop_handlers=use_loop_handlers) as shutdown:
            assert set(shutdown.handled) == set(shutdown_signals())
        assert shutdown.handled == ()

    asyncio.run(scenario())
    for sig in shutdown_signals():
        assert signal.getsignal(sig) is _noop


@pytest.mark.usefixtures("safe_signals")
def test_repeated_signals_keep_the_first(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> signal.Signals:
        with ShutdownSignals(use_loop_handlers=False) as shutdown:
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGTERM)
            await asyncio.sleep(0.05)
            return await shutdown.wait(poll_interval=0.05)

    assert asyncio.run(scenario()) == signal.SIGINT
    assert "received SIGTERM again; shutdown is already in progress" in caplog.text


def test_outside_main_thread_logs_instead_of_failing(caplog: pytest.LogCaptureFixture) -> None:
    errors: list[BaseException] = []
    handled: list[tuple[signal.Signals, ...]] = []

    def worker() -> None:
        async def scenario() -> None:
            with ShutdownSignals() as shutdown:
                handled.append(shutdown.handled)

        try:
            asyncio.run(scenario())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    with caplog.at_level(logging.WARNING, logger="minikv.server"):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(10)
    assert errors == []
    assert handled == [()]
    assert "cannot handle SIGINT for graceful shutdown" in caplog.text


# --- run_server: graceful stop flushes persistence ----------------------------


def _signal_after_start(
    monkeypatch: pytest.MonkeyPatch,
    sig: signal.Signals,
    *,
    writes: list[list[str]] = (),  # type: ignore[assignment]
    before_start: bool = False,
) -> None:
    """Patch ``MiniKVServer.start`` to apply ``writes`` and then raise ``sig``.

    A watchdog cancels the server after 10 s, so a broken handler fails the
    test instead of hanging it.
    """
    real_start = MiniKVServer.start

    async def start(self: MiniKVServer) -> None:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        assert task is not None
        loop.call_later(10, task.cancel)
        if before_start:
            signal.raise_signal(sig)  # arrives while data is still loading
        await real_start(self)
        for command in writes:
            self.database.execute(*command)
        if not before_start:
            loop.call_later(0.05, signal.raise_signal, sig)

    monkeypatch.setattr(MiniKVServer, "start", start)


def _manager(tmp_path: Path, **config: Any) -> PersistenceManager:
    return PersistenceManager(Database(), PersistenceConfig(directory=tmp_path, **config))


@pytest.mark.usefixtures("safe_signals")
@pytest.mark.parametrize("python_handlers", [False, True], ids=["platform-default", "python-handlers"])
@pytest.mark.parametrize("sig_name", SIGNAL_NAMES)
def test_run_server_flushes_aof_and_snapshot_on_shutdown(
    sig_name: str,
    python_handlers: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sig = getattr(signal, sig_name)
    if python_handlers:  # the handler strategy Windows uses, on any platform
        monkeypatch.setattr(shutdown_module, "_loop_handlers_supported", lambda: False)
    manager = _manager(tmp_path, aof_enabled=True, aof_fsync="no", save_rules=((3600, 1),))
    _signal_after_start(
        monkeypatch, sig, writes=[["SET", "user:1", "Ada"], ["SET", "temp", "x", "EX", "3600"]]
    )

    with caplog.at_level(logging.INFO):
        exit_code = run_server(ServerConfig(port=0), manager.database, manager)

    assert exit_code == 0
    assert f"shutdown signal received ({sig_name})" in caplog.text
    assert "server stopped" in caplog.text
    # Even with appendfsync "no", the log is complete on disk after shutdown...
    assert b"Ada" in (tmp_path / "appendonly.aof").read_bytes()
    # ...and the final snapshot was written.
    assert {key for key, _, _ in read_snapshot(tmp_path / "dump.mkv")} == {"temp", "user:1"}

    for options in ({"aof_enabled": True, "save_rules": ()}, {"save_rules": ()}):
        reloaded = _manager(tmp_path, **options)
        reloaded.load()
        assert reloaded.database.execute("GET", "user:1") == "Ada"
        assert reloaded.database.execute("EXISTS", "temp") == 1


@pytest.mark.usefixtures("safe_signals")
def test_signal_during_startup_is_not_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    manager = _manager(tmp_path, save_rules=())
    _signal_after_start(monkeypatch, signal.SIGINT, before_start=True)
    with caplog.at_level(logging.INFO):
        assert run_server(ServerConfig(port=0), manager.database, manager) == 0
    assert "shutdown signal received (SIGINT)" in caplog.text
    assert "server stopped" in caplog.text


@pytest.mark.usefixtures("safe_signals")
def test_failed_flush_on_shutdown_exits_with_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    manager = _manager(tmp_path, aof_enabled=True, save_rules=())

    def broken_close(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk unavailable")

    _signal_after_start(monkeypatch, signal.SIGINT)
    monkeypatch.setattr(manager, "close", broken_close)
    assert run_server(ServerConfig(port=0), manager.database, manager) == 1
    assert "data may not be fully saved" in caplog.text


# --- real process using the Python-handler strategy -----------------------------

_FORCE_PYTHON_HANDLERS = """
import sys
import minikv.server.shutdown as shutdown
shutdown._loop_handlers_supported = lambda: False  # Windows strategy, on any platform
from minikv.cli import main
raise SystemExit(main(sys.argv[1:]))
"""


def test_process_with_python_handlers_shuts_down_gracefully(tmp_path: Path) -> None:
    """External shutdown request handled by ``signal.signal`` handlers.

    On Unix this sends SIGTERM to a server forced to use the Windows handler
    strategy; on Windows it is the real CTRL_BREAK_EVENT / SIGBREAK path.
    """
    from minikv.client import MiniKVClient
    from tests.helpers import free_port, kill_if_running, spawn_python, stop_process, wait_for

    port = free_port()
    process = spawn_python(
        "-c", _FORCE_PYTHON_HANDLERS, "serve", "--port", str(port), "--dir", str(tmp_path),
        "--appendonly", "--appendfsync", "no", "--save", "3600 1",
    )
    try:
        client = MiniKVClient(port=port, timeout=1)

        def ready() -> bool:
            try:
                client.execute("PING")
                return True
            except ConnectionError:
                return False

        assert wait_for(ready, timeout=10), "server did not start"
        client.execute("SET", "k", "v")
        client.close()
        output = stop_process(process, "SIGTERM")
    finally:
        kill_if_running(process)

    assert process.returncode == 0, output
    assert "shutting down gracefully" in output
    assert "server stopped" in output
    assert b"SET" in (tmp_path / "appendonly.aof").read_bytes()
    assert [key for key, _, _ in read_snapshot(tmp_path / "dump.mkv")] == ["k"]
