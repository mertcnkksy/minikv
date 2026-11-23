"""Tests for command specifications and the registry."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from minikv.commands import CommandContext, CommandRegistry, CommandSpec, default_registry
from minikv.errors import WrongArityError
from minikv.responses import OK, Response


def _noop(ctx: CommandContext, args: Sequence[str]) -> Response:
    return OK


def test_name_is_normalised_to_upper_case() -> None:
    assert CommandSpec("echo", _noop, 2, "s", "ECHO msg").name == "ECHO"


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_name_is_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        CommandSpec(name, _noop, 1, "s", "u")


def test_zero_arity_is_rejected() -> None:
    with pytest.raises(ValueError):
        CommandSpec("X", _noop, 0, "s", "u")


@pytest.mark.parametrize(
    ("arity", "count", "expected"),
    [(2, 2, True), (2, 1, False), (2, 3, False), (-2, 1, False), (-2, 2, True), (-2, 9, True)],
)
def test_accepts(arity: int, count: int, expected: bool) -> None:
    assert CommandSpec("X", _noop, arity, "s", "u").accepts(count) is expected


def test_validate_arity_raises() -> None:
    spec = CommandSpec("GET", _noop, 2, "s", "u")
    with pytest.raises(WrongArityError, match="'get' command"):
        spec.validate_arity(3)


def test_lookup_is_case_insensitive() -> None:
    registry = CommandRegistry([CommandSpec("ECHO", _noop, 2, "s", "u")])
    assert registry.get("echo") is registry.get("EcHo")
    assert "echo" in registry
    assert registry.get("nope") is None
    assert 123 not in registry


def test_duplicate_registration_fails() -> None:
    registry = CommandRegistry([CommandSpec("ECHO", _noop, 2, "s", "u")])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(CommandSpec("echo", _noop, 2, "s", "u"))


def test_default_registry_contents_and_order() -> None:
    registry = default_registry()
    assert registry.names() == ["DEL", "EXISTS", "GET", "KEYS", "PING", "SET"]
    assert [spec.name for spec in registry] == registry.names()
    assert len(registry) == 6


def test_default_registry_returns_independent_instances() -> None:
    first = default_registry()
    first.register(CommandSpec("EXTRA", _noop, 1, "s", "u"))
    assert "EXTRA" not in default_registry()
