# AGENTS.md

Guidance for AI coding agents (and human contributors) working on MiniKV.

## Project Overview

MiniKV is a Redis-inspired, in-memory key-value database written in pure Python 3.12.
The current release is **v0.1.0 (Phase 1: core database engine)**.

## Setup and Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # install with test dependencies
pytest                      # run the full test suite (must pass before any commit)
pytest -q tests/test_engine.py
minikv                      # start the interactive shell (or: python -m minikv)
```

## Hard Constraints

- **Standard library only** at runtime. `pytest` is the only allowed development dependency.
  Do not add new dependencies without explicit approval from the maintainer.
- **Python 3.12+.** Modern syntax (`type` aliases, `match`, `X | Y` unions) is welcome.
- **Phase scope:** do not implement TCP networking, TTL/expiry, persistence, or Docker until the
  corresponding roadmap phase is requested. Keep the architecture ready for them instead.
- **Version** lives in one place: `src/minikv/__init__.py` (`__version__`). `pyproject.toml`
  reads it dynamically.

## Architecture Rules

| Layer       | Location                | Responsibility                                          | Must not            |
|-------------|-------------------------|---------------------------------------------------------|---------------------|
| Engine      | `engine.py`             | Dispatch tokens to command handlers                     | Print, read input, format output |
| Commands    | `commands/`             | Validate arguments, implement behaviour                 | Access storage except via `CommandContext` |
| Storage     | `storage/`              | Hold data behind `StorageBackend`, thread-safely        | Know about commands or replies |
| Protocol    | `protocol/`             | Turn raw input into tokens (later: RESP encode/decode)  | Execute commands |
| CLI         | `cli/`                  | Read lines, render responses and errors                 | Contain command logic |

- Handlers return values from `responses.py` (`SimpleString`, `str`, `int`, `None`, `list`), never
  formatted text.
- All deliberate errors derive from `MiniKVError` (see `errors.py`). Raise the most specific subclass.
  Error messages follow Redis wording where a Redis equivalent exists.
- Dependencies flow downward: `cli → engine → commands → storage`. Never import the CLI from lower layers.

## Adding a New Command

1. Write a handler `cmd_<name>(ctx: CommandContext, args: Sequence[str]) -> Response` in
   `commands/builtin.py` (or a new module under `commands/` for a command family).
2. Add a `CommandSpec` with the correct Redis-style arity (count includes the command name;
   negative means "at least").
3. Add tests in `tests/test_engine.py` covering success, edge cases, and wrong arity.
4. Update the command tables in `README.md` and, if relevant, the expected names in
   `tests/test_registry.py`.

## Code Style

- Type hints on every function, method, and public attribute.
- Google-style docstrings for all public modules, classes, and functions.
- Prefer small, pure functions; avoid module-level mutable state.
- Keep lines at or under 100 characters.
- Code, comments, and documentation are written in English.

## Testing Expectations

- Every behaviour change ships with tests; bug fixes include a regression test.
- Tests must be deterministic and fast (the whole suite should finish in seconds).
- Use the `db` and `storage` fixtures from `tests/conftest.py`.
- Test the CLI through `Repl` with injected input/output rather than real terminals.

## Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `test:`,
`docs:`, `refactor:`, `chore:`. Keep each commit focused on one logical change.
