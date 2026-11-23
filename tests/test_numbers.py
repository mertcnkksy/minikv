"""Tests for strict integer parsing."""

from __future__ import annotations

import pytest

from minikv.utils import INT64_MAX, INT64_MIN, fits_int64, parse_int64


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("42", 42),
        ("-7", -7),
        (str(INT64_MAX), INT64_MAX),
        (str(INT64_MIN), INT64_MIN),
        (str(INT64_MAX + 1), None),
        (str(INT64_MIN - 1), None),
        ("", None),
        ("-", None),
        ("+1", None),
        ("007", None),
        ("-0", None),
        ("1.5", None),
        (" 1", None),
        ("1e3", None),
        ("abc", None),
    ],
)
def test_parse_int64(text: str, expected: int | None) -> None:
    assert parse_int64(text) == expected


def test_fits_int64() -> None:
    assert fits_int64(INT64_MAX)
    assert not fits_int64(INT64_MAX + 1)
    assert fits_int64(INT64_MIN)
    assert not fits_int64(INT64_MIN - 1)
