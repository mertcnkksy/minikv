"""Keep version numbers in packaging files in sync with ``minikv.__version__``."""

from __future__ import annotations

import re
from pathlib import Path

from minikv import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_label_matches_version() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', text)
    assert match is not None and match.group(1) == __version__


def test_compose_image_tag_matches_version() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"image:\s*minikv:(\S+)", text)
    assert match is not None and match.group(1) == __version__


def test_changelog_has_an_entry_for_this_version() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in text


def test_readme_status_mentions_this_version() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"`v{__version__}`" in text
