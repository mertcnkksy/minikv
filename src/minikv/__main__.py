"""Allow running MiniKV with ``python -m minikv``."""

from minikv.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
