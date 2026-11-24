# AGENTS.md

Guidance for AI coding agents (and human contributors) working on MiniKV.

## Project Overview

MiniKV is a Redis-inspired, in-memory key-value database written in pure Python 3.12.
The current release is **v0.6.0** (group commit). The roadmap in `README.md` lists the next versions.

## Setup and Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # install with test dependencies
pytest                      # run the full test suite (must pass before any commit)
pytest -q tests/test_engine.py
minikv                      # embedded interactive shell (or: python -m minikv)
minikv serve                # TCP server on 127.0.0.1:6380 (snapshots in the current dir)
minikv serve --dir data --appendonly --save ""   # AOF only, data in ./data
minikv connect              # shell connected to the server
minikv benchmark            # throughput and latency of a running server
docker build -t minikv . && bash scripts/docker_smoke_test.sh minikv
python benchmarks/run_benchmarks.py --requests 5000
```

## Hard Constraints

- **Standard library only** at runtime. `pytest` is the only allowed development dependency.
  Do not add new dependencies without explicit approval from the maintainer.
- **Python 3.12+.** Modern syntax (`type` aliases, `match`, `X | Y` unions) is welcome.
- **Scope:** the roadmap is complete. Propose new features or behaviour changes to the
  maintainer before implementing them. Keep the architecture ready for them instead.
- **Changelog:** every user-visible change gets an entry in `CHANGELOG.md`.
- **Version** lives in one place: `src/minikv/__init__.py` (`__version__`). `pyproject.toml`
  reads it dynamically.

## Architecture Rules

| Layer       | Location                | Responsibility                                          | Must not            |
|-------------|-------------------------|---------------------------------------------------------|---------------------|
| Engine      | `engine.py`             | Dispatch tokens to command handlers                     | Print, read input, format output |
| Commands    | `commands/`             | Validate arguments, implement behaviour                 | Access storage or time except via `CommandContext` |
| Storage     | `storage/`              | Hold data and expiry metadata, thread-safely            | Know about commands or replies |
| Persistence | `persistence/`          | Load, snapshot, log and compact data on disk            | Mutate storage after `start()` except through `Database.execute` |
| Clock       | `clock.py`              | The only place that reads system time                   | Be bypassed with direct `time` calls |
| Protocol    | `protocol/`             | Tokenise inline input; encode/decode RESP2              | Touch sockets or execute commands |
| Server      | `server/`               | Accept clients, run the read/execute/write loop          | Contain command logic or block the event loop |
| Client      | `client.py`             | Send commands, decode replies                           | Retry non-idempotent commands |
| CLI         | `cli/`                  | Read lines, render responses and errors                 | Contain command logic |

- Handlers return values from `responses.py` (`SimpleString`, `str`, `int`, `None`, `list`), never
  formatted text.
- All deliberate errors derive from `MiniKVError` (see `errors.py`). Raise the most specific subclass.
  Error messages follow Redis wording where a Redis equivalent exists.
- Dependencies flow downward: `cli/server → engine → commands → storage → clock`. Never import
  the CLI or server from lower layers.

## Persistence Rules

- A command is logged only if it changed `storage.dirty`. Every storage mutation that changes
  the dataset must increment `dirty`; removals caused by expiry must not.
- A handler whose effect depends on the current time **must** call `ctx.propagate_as(...)` with
  an equivalent absolute-time command (see `cmd_set` and the `EXPIRE` family). Forgetting this
  makes AOF replay produce different data — add a test in `tests/test_propagation.py`.
- Never write a data file in place: use `atomic_write()` or a temporary file plus `os.replace`.
- Snapshot format changes require bumping `FORMAT_VERSION` and keeping old versions readable.
- Background jobs take their copy of the data while holding `database.lock`.
- **Never send a reply before the writes it acknowledges are durable.** In the server, every
  reply goes through `ClientConnection._wait_durable()` (group commit). New reply paths must do
  the same, and a failed commit must close the connection rather than acknowledge.
- `AppendOnlyFile(deferred=True)` is only for callers that call `commit()` before acknowledging;
  the embedded default (`deferred=False`) must keep syncing every write under `always`.
- Tests that touch disk use `tmp_path`; nothing may be written to the working directory.

## Server and Protocol Rules

- The server is single-threaded by design: handlers run on the event loop and must never block
  (no sleeping, no blocking I/O). Long-running work needs a design discussion first.
- Connection-level behaviour (`QUIT`, future per-client state) lives in `server/connection.py`;
  data commands live in `commands/`.
- Protocol errors reply with `ERR Protocol error: ...` and close the connection.
- Keep error messages identical to Redis wherever Redis has an equivalent; clients depend on them.
- Server tests use the `server` / `server_factory` fixtures (ephemeral ports) and helpers in
  `tests/helpers.py`. Never hard-code ports in tests; use `free_port()` when one is needed.

## Cross-Platform Rules (Linux, macOS, Windows)

- All shutdown signal handling lives in `server/shutdown.py`. Never call `signal.signal` or
  `loop.add_signal_handler` elsewhere, and never assume `SIGBREAK` or POSIX-only signals exist:
  use `shutdown_signals()`.
- Tests that start server processes must use `spawn_minikv()` / `spawn_python()` and stop them
  with `stop_process()` / `request_graceful_shutdown()` from `tests/helpers.py`. Never call
  `Popen.send_signal(signal.SIGINT/SIGTERM)` or `terminate()` directly: on Windows the first
  raises `ValueError` and the second kills the process without a graceful shutdown.
- Tests that raise signals in-process use `signal.raise_signal` together with the
  `safe_signals` fixture, so a regression fails the test instead of killing the test runner.
- Use `pathlib` and `os.replace`; close a file before replacing or deleting it (Windows
  refuses to replace open files). Never rely on directory `fsync` succeeding.

## Expiry Rules

- Expiry timestamps are absolute `int` milliseconds since the Unix epoch. Never use floats.
- Read the current time with `ctx.now_ms()` in handlers and `self._clock.time_ms()` in storage.
- Storage backends must hide expired keys from **every** read method (lazy expiration) and
  implement `remove_expired()` for active expiration.
- Integer arguments are parsed with `require_int()` so that error messages match Redis.
- Expiry tests use the `clock` fixture (`ManualClock`) and `clock.advance(...)`. Never use
  `time.sleep` in tests.

## Adding a New Command

1. Write a handler `cmd_<name>(ctx: CommandContext, args: Sequence[str]) -> Response` in the
   matching module: `commands/builtin.py` for core key-value commands, `commands/expiry.py` for
   expiry commands, `commands/server.py` for server utilities, or a new module under `commands/` for a new command family (then add its
   tuple to `BUILTIN_COMMANDS` in `commands/__init__.py`).
2. Add a `CommandSpec` with the correct Redis-style arity (count includes the command name;
   negative means "at least").
3. Add tests covering success, edge cases, invalid input, and wrong arity.
4. Update the command tables in `README.md`, the expected names in `tests/test_registry.py`,
   and `CHANGELOG.md`.

## Code Style

- Type hints on every function, method, and public attribute.
- Google-style docstrings for all public modules, classes, and functions.
- Prefer small, pure functions; avoid module-level mutable state.
- Keep lines at or under 100 characters.
- Code, comments, and documentation are written in English.

## Testing Expectations

- Every behaviour change ships with tests; bug fixes include a regression test.
- Tests must be deterministic and fast (the whole suite should finish in seconds).
- Use the `db`, `storage`, `clock`, `server`, and `server_factory` fixtures from `tests/conftest.py`.
- Optional third-party tools (`redis-py`, `redis-cli`) may be used in tests only behind
  `pytest.importorskip` / `skipif`, never as required dependencies.
- Test the CLI through `Repl` with injected input/output rather than real terminals.

## Docker, CI and Benchmark Rules

- The image must stay dependency-free, run as the unprivileged `minikv` user, keep data in
  `/data`, and shut down gracefully on `SIGTERM`. Run `scripts/docker_smoke_test.sh` after any
  change to the `Dockerfile`, the CLI options, logging messages it checks, or shutdown code.
- Keep `.github/workflows/ci.yml` green on every OS and Python version in the matrix; never
  remove a matrix entry or mark a job `continue-on-error` to hide a failure.
- Validate changes with `actionlint` (workflow), `hadolint` (Dockerfile) and `shellcheck`
  (scripts) when they are available.
- Version strings outside `src/minikv/__init__.py` (Dockerfile label, Compose image tag,
  CHANGELOG heading, README status) are checked by `tests/test_project_metadata.py`; update them
  together when releasing.
- Performance figures in the README must state how they were measured (tool, requests,
  clients, pipeline, environment) and be described as indicative.
- Benchmark code must not require tools beyond the standard library and MiniKV.

## Commit Conventions

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `test:`,
`docs:`, `refactor:`, `chore:`. Keep each commit focused on one logical change.
