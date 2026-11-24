"""Tests for the database engine and built-in commands."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from minikv import Database
from minikv.commands import CommandContext, CommandRegistry, CommandSpec, default_registry
from minikv.errors import (
    CommandError,
    CommandSyntaxError,
    MiniKVError,
    ParseError,
    UnknownCommandError,
    WrongArityError,
)
from minikv.responses import OK, PONG, Response
from minikv.storage import InMemoryStorage


# --- PING -------------------------------------------------------------------

def test_ping(db: Database) -> None:
    assert db.execute("PING") == PONG


def test_ping_echoes_message(db: Database) -> None:
    assert db.execute("PING", "hello world") == "hello world"


def test_ping_too_many_args(db: Database) -> None:
    with pytest.raises(WrongArityError):
        db.execute("PING", "a", "b")


# --- SET / GET --------------------------------------------------------------

def test_set_returns_ok_and_get_returns_value(db: Database) -> None:
    assert db.execute("SET", "name", "Ada") == OK
    assert db.execute("GET", "name") == "Ada"


def test_get_missing_returns_none(db: Database) -> None:
    assert db.execute("GET", "missing") is None


def test_set_overwrites(db: Database) -> None:
    db.execute("SET", "k", "1")
    db.execute("SET", "k", "2")
    assert db.execute("GET", "k") == "2"


@pytest.mark.parametrize(
    "command",
    [("SET",), ("SET", "k"), ("GET",), ("GET", "a", "b")],
)
def test_set_get_wrong_arity(db: Database, command: tuple[str, ...]) -> None:
    with pytest.raises(WrongArityError):
        db.execute(*command)


def test_set_unknown_option_is_syntax_error(db: Database) -> None:
    with pytest.raises(CommandSyntaxError):
        db.execute("SET", "k", "v", "extra")


# --- DEL / EXISTS -----------------------------------------------------------

def test_del_counts_removed_keys(db: Database) -> None:
    db.execute("SET", "a", "1")
    db.execute("SET", "b", "2")
    assert db.execute("DEL", "a", "b", "missing") == 2
    assert db.execute("GET", "a") is None


def test_del_duplicate_key_counted_once(db: Database) -> None:
    db.execute("SET", "a", "1")
    assert db.execute("DEL", "a", "a") == 1


def test_exists_counts_including_duplicates(db: Database) -> None:
    db.execute("SET", "a", "1")
    assert db.execute("EXISTS", "a") == 1
    assert db.execute("EXISTS", "a", "a", "missing") == 2
    assert db.execute("EXISTS", "missing") == 0


@pytest.mark.parametrize("name", ["DEL", "EXISTS"])
def test_multi_key_commands_require_a_key(db: Database, name: str) -> None:
    with pytest.raises(WrongArityError):
        db.execute(name)


# --- KEYS -------------------------------------------------------------------

def test_keys_matches_pattern_sorted(db: Database) -> None:
    for key in ("user:2", "user:1", "order:1"):
        db.execute("SET", key, "x")
    assert db.execute("KEYS", "user:*") == ["user:1", "user:2"]
    assert db.execute("KEYS", "*") == ["order:1", "user:1", "user:2"]


def test_keys_empty_database(db: Database) -> None:
    assert db.execute("KEYS", "*") == []


def test_keys_requires_exactly_one_pattern(db: Database) -> None:
    with pytest.raises(WrongArityError):
        db.execute("KEYS")
    with pytest.raises(WrongArityError):
        db.execute("KEYS", "a", "b")


# --- Dispatch & errors ------------------------------------------------------

@pytest.mark.parametrize("name", ["ping", "Ping", "PING"])
def test_command_names_are_case_insensitive(db: Database, name: str) -> None:
    assert db.execute(name) == PONG


def test_keys_and_values_are_case_sensitive(db: Database) -> None:
    db.execute("SET", "Key", "Value")
    assert db.execute("GET", "key") is None
    assert db.execute("GET", "Key") == "Value"


def test_unknown_command(db: Database) -> None:
    with pytest.raises(UnknownCommandError) as info:
        db.execute("FLY", "to", "moon")
    assert str(info.value) == "ERR unknown command 'FLY', with args beginning with: 'to' 'moon'"


def test_empty_command(db: Database) -> None:
    with pytest.raises(CommandError, match="empty command"):
        db.execute()


def test_non_string_tokens_rejected(db: Database) -> None:
    with pytest.raises(TypeError):
        db.execute("SET", "k", 1)  # type: ignore[arg-type]


def test_errors_share_a_common_base(db: Database) -> None:
    with pytest.raises(MiniKVError):
        db.execute("NOPE")


# --- execute_line -----------------------------------------------------------

def test_execute_line_with_quoted_value(db: Database) -> None:
    db.execute_line('SET greeting "hello world"')
    assert db.execute_line("GET greeting") == "hello world"


def test_execute_line_parse_error(db: Database) -> None:
    with pytest.raises(ParseError):
        db.execute_line('SET k "oops')


def test_execute_line_blank(db: Database) -> None:
    with pytest.raises(CommandError):
        db.execute_line("   ")


# --- Extensibility ----------------------------------------------------------

def test_custom_storage_is_used() -> None:
    storage = InMemoryStorage()
    database = Database(storage=storage)
    database.execute("SET", "k", "v")
    assert storage.get("k") == "v"
    assert database.storage is storage


def test_custom_command_can_be_registered() -> None:
    def cmd_strlen(ctx: CommandContext, args: Sequence[str]) -> Response:
        value = ctx.storage.get(args[0])
        return 0 if value is None else len(value)

    registry = default_registry()
    registry.register(CommandSpec("STRLEN", cmd_strlen, 2, "Value length.", "STRLEN key"))
    database = Database(registry=registry)
    database.execute("SET", "a", "hello")
    assert database.execute("strlen", "a") == 5


def test_empty_registry_knows_no_commands() -> None:
    with pytest.raises(UnknownCommandError):
        Database(registry=CommandRegistry()).execute("PING")
