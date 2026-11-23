"""Console entry point for the ``minikv`` command."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from minikv import __version__
from minikv.cli.repl import Repl
from minikv.engine import Database


def build_parser() -> argparse.ArgumentParser:
    """Create the argument parser for the ``minikv`` command."""
    parser = argparse.ArgumentParser(
        prog="minikv",
        description="MiniKV - a Redis-inspired in-memory key-value database.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--no-banner", action="store_true", help="do not print the welcome banner"
    )
    return parser


def _enable_line_editing() -> None:
    """Enable history and arrow-key editing where :mod:`readline` is available."""
    try:
        import readline  # noqa: F401  (imported for its side effect)
    except ImportError:  # pragma: no cover - e.g. Windows without pyreadline
        pass


def main(argv: Sequence[str] | None = None) -> int:
    """Run the MiniKV interactive shell.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    _enable_line_editing()
    return Repl(Database(), show_banner=not args.no_banner).run()
