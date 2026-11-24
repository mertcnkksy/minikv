"""Tests for the interactive shell and console entry point."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from minikv import Database, __version__
from minikv.cli import Repl, main
from minikv.client import MiniKVClient
from minikv.cli.app import build_parser
from minikv.storage import InMemoryStorage
from tests.helpers import (
    ServerThread,
    free_port,
    kill_if_running,
    spawn_minikv,
    stop_process,
    wait_for,
)

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def scripted_input(lines: Iterable[str | BaseException]) -> Callable[[str], str]:
    """Return an ``input``-like function that replays ``lines``, then raises EOFError."""
    iterator = iter(lines)

    def _input(prompt: str) -> str:
        try:
            item = next(iterator)
        except StopIteration:
            raise EOFError from None
        if isinstance(item, BaseException):
            raise item
        return item

    return _input


def run_session(lines: Iterable[str | BaseException], database: Database | None = None) -> str:
    """Run a REPL session over ``lines`` and return everything it printed."""
    output = io.StringIO()
    repl = Repl(
        database or Database(),
        input_func=scripted_input(lines),
        output=output,
        show_banner=False,
    )
    assert repl.run() == 0
    return output.getvalue()


def test_ttl_session_output() -> None:
    out = run_session(["SET k v EX 100", "TTL k", "TTL nope", "SET k v NX"])
    assert out.splitlines()[:4] == ["OK", "(integer) 100", "(integer) -2", "(nil)"]


def test_basic_session() -> None:
    out = run_session(["SET name Ada", "GET name", "GET nobody", "EXISTS name", "KEYS *"])
    assert out.splitlines() == ["OK", '"Ada"', "(nil)", "(integer) 1", '1) "name"', ""]


def test_errors_are_displayed_and_session_continues() -> None:
    out = run_session(["GET", "BOGUS", 'SET k "x', "PING"])
    lines = out.splitlines()
    assert lines[0] == "(error) ERR wrong number of arguments for 'get' command"
    assert lines[1].startswith("(error) ERR unknown command 'BOGUS'")
    assert lines[2] == "(error) ERR invalid request: unbalanced quotes"
    assert lines[3] == "PONG"


def test_blank_lines_are_ignored() -> None:
    assert run_session(["", "   ", "PING"]).splitlines() == ["PONG", ""]


@pytest.mark.parametrize("command", ["QUIT", "exit"])
def test_quit_stops_processing(command: str) -> None:
    out = run_session(["PING", command, "PING"])
    assert out.splitlines() == ["PONG", "Bye!"]


def test_keyboard_interrupt_does_not_exit() -> None:
    out = run_session([KeyboardInterrupt(), "PING"])
    assert "PONG" in out


def test_help_lists_commands() -> None:
    out = run_session(["HELP"])
    for name in ("SET", "GET", "DEL", "KEYS", "EXPIRE", "TTL", "PERSIST", "QUIT"):
        assert f"  {name} " in out
    assert max(len(line) for line in out.splitlines()) <= 100


def test_help_for_single_command() -> None:
    assert run_session(["help get"]).splitlines()[0] == "GET key"
    assert run_session(["HELP set"]).splitlines()[0].startswith("SET key value [NX|XX]")


def test_help_for_unknown_command() -> None:
    assert "no help for unknown command 'nope'" in run_session(["HELP nope"])


def test_unexpected_exceptions_keep_shell_alive() -> None:
    class BrokenStorage(InMemoryStorage):
        def get(self, key: str) -> str | None:
            raise RuntimeError("disk on fire")

    out = run_session(["GET k", "PING"], Database(storage=BrokenStorage()))
    assert out.splitlines()[:2] == ["(error) ERR internal error: disk on fire", "PONG"]


def test_banner_is_shown_by_default() -> None:
    output = io.StringIO()
    Repl(Database(), input_func=scripted_input([]), output=output).run()
    assert output.getvalue().startswith(f"MiniKV {__version__}")


def test_main_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.strip() == f"minikv {__version__}"


def test_python_dash_m_entry_point() -> None:
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    result = subprocess.run(
        [sys.executable, "-m", "minikv", "--no-banner"],
        input="SET a 1\nGET a\nQUIT\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=True,
    )
    assert "OK" in result.stdout
    assert '"1"' in result.stdout
    assert "Bye!" in result.stdout


# --- Phase 3: server and connect modes ------------------------------------------


def test_parser_serve_options() -> None:
    args = build_parser().parse_args(["serve", "--port", "7000", "--log-level", "debug"])
    assert (args.mode, args.port, args.log_level, args.host) == ("serve", 7000, "DEBUG", "127.0.0.1")


def test_parser_persistence_options(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "serve", "--dir", str(tmp_path), "--save", "900 1 60 100", "--appendonly",
        "--appendfsync", "always", "--dbfilename", "x.mkv", "--appendfilename", "x.aof",
    ])
    assert args.dir == tmp_path
    assert args.save == ((900, 1), (60, 100))
    assert (args.appendonly, args.appendfsync) == (True, "always")
    assert (args.dbfilename, args.appendfilename) == ("x.mkv", "x.aof")
    assert build_parser().parse_args(["serve", "--save", ""]).save == ()
    defaults = build_parser().parse_args(["serve"])
    assert (defaults.save, defaults.appendonly, defaults.appendfsync) == (None, False, "everysec")


@pytest.mark.parametrize("rules", ["900", "a b", "0 1", "10 -1"])
def test_parser_rejects_bad_save_rules(rules: str) -> None:
    with pytest.raises(SystemExit) as info:
        build_parser().parse_args(["serve", "--save", rules])
    assert info.value.code == 2


def test_parser_benchmark_defaults() -> None:
    args = build_parser().parse_args(["benchmark"])
    assert (args.mode, args.requests, args.clients, args.pipeline) == ("benchmark", 10_000, 10, 1)
    assert args.tests == ("PING", "SET", "GET") and not args.json


def test_parser_connect_options() -> None:
    args = build_parser().parse_args(["connect", "-p", "7001", "SET", "k", "v"])
    assert (args.mode, args.port, args.command) == ("connect", 7001, ["SET", "k", "v"])


@pytest.mark.parametrize(
    "argv",
    [["serve", "--port", "99999"], ["serve", "--port", "abc"], ["serve", "--max-clients", "0"]],
)
def test_parser_rejects_bad_values(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as info:
        build_parser().parse_args(argv)
    assert info.value.code == 2


def test_connect_one_shot(server: ServerThread, capsys: pytest.CaptureFixture[str]) -> None:
    port = str(server.port)
    assert main(["connect", "-p", port, "SET", "greeting", "hello world"]) == 0
    assert main(["connect", "-p", port, "GET", "greeting"]) == 0
    assert capsys.readouterr().out.splitlines() == ["OK", '"hello world"']


def test_connect_one_shot_error_exit_code(
    server: ServerThread, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect", "-p", str(server.port), "GET"]) == 1
    assert capsys.readouterr().out.startswith("(error) ERR wrong number of arguments")


def test_connect_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["connect", "-p", str(free_port()), "PING"]) == 1
    assert "Could not connect to MiniKV" in capsys.readouterr().err


def test_repl_over_network(server: ServerThread) -> None:
    client = MiniKVClient(port=server.port)
    output = io.StringIO()
    repl = Repl(client, input_func=scripted_input(["SET k v EX 50", "TTL k", "HELP", "QUIT"]),
                output=output, show_banner=False)
    repl.run()
    client.close()
    lines = output.getvalue().splitlines()
    assert lines[:2] == ["OK", "(integer) 50"]
    assert any(line.startswith("  HELLO ") for line in lines)  # help from default registry
    assert lines[-1] == "Bye!"


def test_repl_reports_connection_errors() -> None:
    class Offline:
        def execute(self, *command: str) -> object:
            raise ConnectionError("Could not connect to MiniKV at 127.0.0.1:1: refused")

    output = io.StringIO()
    Repl(Offline(), input_func=scripted_input(["PING", "PING"]), output=output,  # type: ignore[arg-type]
         show_banner=False).run()
    assert output.getvalue().count("Could not connect") == 2


def test_serve_reports_port_in_use(
    server: ServerThread, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    assert main(["serve", "--port", str(server.port), "--dir", str(tmp_path)]) == 1
    assert "could not start server" in caplog.text
    assert list(tmp_path.iterdir()) == []  # a server that never started writes nothing


@pytest.mark.parametrize("sig_name", ["SIGINT", "SIGTERM"])
def test_server_process_end_to_end(sig_name: str, tmp_path: Path) -> None:
    """Start a real server process, talk to it, and stop it gracefully.

    On Windows the graceful request is delivered as ``CTRL_BREAK_EVENT``
    (see ``tests.helpers.request_graceful_shutdown``).
    """
    port = free_port()
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    process = spawn_minikv("serve", "--port", str(port), "--save", "", cwd=tmp_path)
    try:
        client = MiniKVClient(port=port, timeout=1)

        def ready() -> bool:
            try:
                return client.execute("PING") is not None
            except ConnectionError:
                return False

        assert wait_for(ready, timeout=10), "server did not start"
        assert client.execute("SET", "k", "v") is not None
        client.close()

        cli = subprocess.run(
            [sys.executable, "-m", "minikv", "connect", "-p", str(port), "GET", "k"],
            capture_output=True, text=True, env=env, timeout=30, check=True,
        )
        assert cli.stdout.strip() == '"v"'

        output = stop_process(process, sig_name)
    finally:
        kill_if_running(process)
    assert process.returncode == 0
    assert "ready to accept connections" in output
    assert "server stopped" in output
    assert list(tmp_path.iterdir()) == []  # --save "" keeps the server purely in memory
