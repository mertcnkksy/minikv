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
from minikv.storage import InMemoryStorage

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
