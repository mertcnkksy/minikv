"""Persistence commands: SAVE, BGSAVE, BGREWRITEAOF, LASTSAVE."""

from __future__ import annotations

from collections.abc import Sequence

from minikv.commands.base import CommandContext, CommandSpec, PersistenceControl
from minikv.errors import CommandError
from minikv.responses import OK, Response, SimpleString


def _require(ctx: CommandContext) -> PersistenceControl:
    if ctx.persistence is None:
        raise CommandError("persistence is not configured")
    return ctx.persistence


def cmd_save(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``SAVE`` - write a snapshot synchronously."""
    _require(ctx).save()
    return OK


def cmd_bgsave(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``BGSAVE`` - start writing a snapshot in the background."""
    _require(ctx).background_save()
    return SimpleString("Background saving started")


def cmd_bgrewriteaof(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``BGREWRITEAOF`` - compact the append-only file in the background."""
    _require(ctx).background_rewrite_aof()
    return SimpleString("Background append only file rewriting started")


def cmd_lastsave(ctx: CommandContext, args: Sequence[str]) -> Response:
    """``LASTSAVE`` - Unix time of the last successful snapshot."""
    return _require(ctx).last_save_time()


PERSISTENCE_COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("SAVE", cmd_save, 1, "Write a snapshot to disk synchronously.", "SAVE"),
    CommandSpec("BGSAVE", cmd_bgsave, 1, "Write a snapshot to disk in the background.", "BGSAVE"),
    CommandSpec(
        "BGREWRITEAOF",
        cmd_bgrewriteaof,
        1,
        "Compact the append-only file in the background.",
        "BGREWRITEAOF",
    ),
    CommandSpec(
        "LASTSAVE", cmd_lastsave, 1, "Unix time of the last successful snapshot.", "LASTSAVE"
    ),
)
