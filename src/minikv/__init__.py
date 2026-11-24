"""MiniKV: a lightweight, Redis-inspired in-memory key-value database."""

__version__ = "0.6.0"

from minikv.engine import Database  # noqa: E402
from minikv.errors import MiniKVError  # noqa: E402

__all__ = ["Database", "MiniKVError", "__version__"]
