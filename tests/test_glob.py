"""Tests for Redis-style glob matching."""

from __future__ import annotations

import pytest

from minikv.utils import glob_match


@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        ("*", "anything", True),
        ("*", "", True),
        ("user:*", "user:42", True),
        ("user:*", "order:42", False),
        ("h?llo", "hello", True),
        ("h?llo", "hllo", False),
        ("h*llo", "heeeello", True),
        ("h[ae]llo", "hallo", True),
        ("h[ae]llo", "hillo", False),
        ("h[^e]llo", "hallo", True),
        ("h[^e]llo", "hello", False),
        ("h[a-c]llo", "hbllo", True),
        ("h[a-c]llo", "hdllo", False),
        ("h[c-a]llo", "hbllo", True),
        ("user:\\*", "user:*", True),
        ("user:\\*", "user:1", False),
        ("[abc", "[abc", True),
        ("a.b", "a.b", True),
        ("a.b", "axb", False),
        ("a+b", "a+b", True),
        ("", "", True),
        ("", "a", False),
        ("line*", "line1\nline2", True),
    ],
)
def test_glob_match(pattern: str, text: str, expected: bool) -> None:
    assert glob_match(pattern, text) is expected
