"""Project-wide constants shared by the server, client and CLI."""

from __future__ import annotations

DEFAULT_HOST = "127.0.0.1"
"""Default interface the server binds to and the client connects to."""

DEFAULT_PORT = 6380
"""Default TCP port. Deliberately not 6379, so MiniKV can run next to a real Redis."""
