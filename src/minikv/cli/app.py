"""Console entry point for the ``minikv`` command.

Modes:

* ``minikv`` - interactive shell on an embedded, in-process database.
* ``minikv serve`` - run the TCP server.
* ``minikv connect`` - interactive shell (or one-shot command) against a server.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from minikv import __version__
from minikv.cli.formatting import format_error, format_response
from minikv.cli.repl import Repl
from minikv.client import MiniKVClient
from minikv.constants import DEFAULT_HOST, DEFAULT_PORT
from minikv.engine import Database
from minikv.errors import MiniKVError

_EPILOG = f"""\
examples:
  minikv                              embedded shell (data lives in this process)
  minikv serve --port {DEFAULT_PORT}              start the server (snapshots in the current dir)
  minikv serve --dir data --appendonly  log every write to data/appendonly.aof
  minikv serve --save ""              pure in-memory server, nothing written to disk
  minikv connect                      shell connected to the server
  minikv connect SET greeting hello   run one command and exit
  redis-cli -p {DEFAULT_PORT} PING                 any Redis client works too
"""


def _port(value: str) -> int:
    """argparse type for TCP ports."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port: {value!r}") from None
    if not 0 <= port <= 65_535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _positive_int(value: str) -> int:
    """argparse type for positive integers."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _save_rules(value: str) -> tuple[tuple[int, int], ...]:
    """argparse type for Redis-style save rules: ``"3600 1 300 100"`` or ``""``."""
    parts = value.split()
    if len(parts) % 2:
        raise argparse.ArgumentTypeError("save rules come in '<seconds> <changes>' pairs")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid save rules: {value!r}") from None
    rules = tuple(zip(numbers[::2], numbers[1::2], strict=True))
    if any(seconds < 1 or changes < 1 for seconds, changes in rules):
        raise argparse.ArgumentTypeError("save rule values must be positive")
    return rules


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the ``minikv`` command."""
    parser = argparse.ArgumentParser(
        prog="minikv",
        description="MiniKV - a Redis-inspired in-memory key-value database.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--no-banner", action="store_true", help="do not print the welcome banner")
    modes = parser.add_subparsers(dest="mode", metavar="{serve,connect}")

    serve = modes.add_parser("serve", help="run the MiniKV TCP server")
    serve.add_argument("--host", default=DEFAULT_HOST,
                       help=f"interface to bind (default: {DEFAULT_HOST}; 0.0.0.0 for all)")
    serve.add_argument("-p", "--port", type=_port, default=DEFAULT_PORT,
                       help=f"TCP port (default: {DEFAULT_PORT})")
    serve.add_argument("--max-clients", type=_positive_int, default=10_000,
                       help="maximum simultaneous clients (default: 10000)")
    serve.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"], type=str.upper,
                       help="logging verbosity (default: INFO)")
    persistence = serve.add_argument_group("persistence")
    persistence.add_argument("--dir", default=".", type=Path,
                             help="directory for data files (default: current directory)")
    persistence.add_argument("--dbfilename", default="dump.mkv",
                             help="snapshot file name (default: dump.mkv)")
    persistence.add_argument("--save", type=_save_rules, default=None, metavar="RULES",
                             help='snapshot rules as "<seconds> <changes> ..." pairs; "" disables '
                                  "(default: \"3600 1 300 100 60 10000\")")
    persistence.add_argument("--appendonly", action="store_true",
                             help="log every write to an append-only file")
    persistence.add_argument("--appendfilename", default="appendonly.aof",
                             help="append-only file name (default: appendonly.aof)")
    persistence.add_argument("--appendfsync", default="everysec",
                             choices=["always", "everysec", "no"],
                             help="append-only file fsync policy (default: everysec)")

    connect = modes.add_parser("connect", help="connect to a running MiniKV server")
    connect.add_argument("--host", default=DEFAULT_HOST, help=f"server host (default: {DEFAULT_HOST})")
    connect.add_argument("-p", "--port", type=_port, default=DEFAULT_PORT,
                         help=f"server port (default: {DEFAULT_PORT})")
    connect.add_argument("--no-banner", action="store_true", default=argparse.SUPPRESS,
                         help="do not print the welcome banner")
    connect.add_argument("command", nargs=argparse.REMAINDER,
                         help="run this command once and exit instead of starting a shell")
    return parser


def _enable_line_editing() -> None:
    """Enable history and arrow-key editing where :mod:`readline` is available."""
    try:
        import readline  # noqa: F401  (imported for its side effect)
    except ImportError:  # pragma: no cover - e.g. Windows without pyreadline
        pass


def _run_serve(args: argparse.Namespace) -> int:
    from minikv.persistence import DEFAULT_SAVE_RULES, PersistenceConfig, PersistenceManager
    from minikv.server import ServerConfig, run_server

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    save_rules = DEFAULT_SAVE_RULES if args.save is None else args.save
    persistence_config = PersistenceConfig(
        directory=args.dir,
        snapshot_filename=args.dbfilename,
        save_rules=save_rules,
        aof_enabled=args.appendonly,
        aof_filename=args.appendfilename,
        aof_fsync=args.appendfsync,
    )
    database = Database()
    return run_server(
        ServerConfig(host=args.host, port=args.port, max_clients=args.max_clients),
        database,
        PersistenceManager(database, persistence_config),
    )


def _run_connect(args: argparse.Namespace) -> int:
    client = MiniKVClient(args.host, args.port)
    try:
        client.connect()
    except ConnectionError as exc:
        print(exc, file=sys.stderr)
        return 1

    with client:
        if args.command:
            try:
                print(format_response(client.execute(*args.command)))
            except MiniKVError as exc:
                print(format_error(exc))
                return 1
            except ConnectionError as exc:
                print(exc, file=sys.stderr)
                return 1
            return 0

        _enable_line_editing()
        return Repl(
            client,
            prompt=f"{args.host}:{args.port}> ",
            show_banner=not args.no_banner,
            banner=f"Connected to MiniKV at {args.host}:{args.port}. "
            "Type HELP for commands, QUIT to exit.",
        ).run()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``minikv`` command.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    if args.mode == "serve":
        return _run_serve(args)
    if args.mode == "connect":
        return _run_connect(args)
    _enable_line_editing()
    return Repl(Database(), show_banner=not args.no_banner).run()
