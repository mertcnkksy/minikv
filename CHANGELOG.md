# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

## [0.5.0] - Phase 5: Docker, CI and Benchmarks

### Added

- Multi-stage `Dockerfile`: virtual environment built in a separate stage, unprivileged
  `minikv` user, `/data` volume, health check using `minikv connect PING`, AOF enabled by
  default, and graceful `docker stop` through `SIGTERM`. The base image is configurable
  with the `PYTHON_IMAGE` build argument.
- `docker-compose.yml` with a named volume and a 30 s stop grace period, and `.dockerignore`.
- `scripts/docker_smoke_test.sh`: end-to-end image test (non-root user, health check,
  reads and writes, graceful stop with exit code 0, data surviving a new container).
- GitHub Actions workflow: tests on Linux, macOS and Windows with Python 3.12 and 3.13;
  mandatory `redis-cli`/`redis-py` compatibility tests; Docker build and smoke test;
  benchmark comparison in the job summary; image publishing to GHCR on `v*` tags.
- `minikv benchmark`: standard-library load generator reporting throughput and
  p50/p95/p99/max latency, with pipelining and JSON output.
- `minikv.benchmark` module (`BenchmarkConfig`, `BenchmarkResult`, `run_benchmark`).
- `MiniKVClient.pipeline()` to send many commands in one round trip; error replies are
  returned as values.
- `benchmarks/run_benchmarks.py` comparing in-memory, AOF `everysec` and AOF `always`.
- Python 3.13 and OS-independence classifiers.

## [0.4.0] - Phase 4: Persistence

### Added

- Snapshots: compact, versioned binary format (`dump.mkv`) with record count and CRC-32
  checksum, written atomically; automatic save rules (Redis defaults), save on shutdown,
  and expired keys skipped on load.
- Append-only file (`appendonly.aof`) in RESP format with `always`, `everysec` and `no`
  fsync policies, repair of a truncated last command, and detection of corruption.
- Background snapshots (`BGSAVE`) and background AOF compaction (`BGREWRITEAOF`) with a
  rewrite buffer so no concurrent write is lost; automatic rewrite when the log doubles.
- Commands: `SAVE`, `BGSAVE`, `BGREWRITEAOF`, `LASTSAVE`.
- `minikv.persistence` package: `PersistenceManager`, `PersistenceConfig`,
  `AppendOnlyFile`, `FsyncPolicy`, snapshot reader/writer and atomic file helpers.
- `minikv serve` options: `--dir`, `--save`, `--dbfilename`, `--appendonly`,
  `--appendfilename`, `--appendfsync`.
- Write listeners on `Database` (`add_write_listener` / `remove_write_listener`).
- `PersistenceError` exception.
- `minikv.server.ShutdownSignals` and `shutdown_signals()` for cross-platform graceful shutdown.
- Tests for crash recovery, restarts of real server processes, fsync policies, and
  shutdown signal handling on every platform's handler path.

### Changed

- `StorageBackend` gained the `dirty` counter and `items()`; custom backends must
  implement them.
- `Database.execute` now runs each command under a lock and notifies write listeners
  with a time-independent form of modifying commands (`SET ... EX` is logged as
  `SET ... PXAT`, the `EXPIRE` family as `PEXPIREAT` or `DEL`).
- `CommandContext` gained `persistence` and `propagate_as()`.
- `MiniKVServer` and `run_server` accept a `PersistenceManager`; the server loads data
  before accepting clients and runs persistence duties in its maintenance cycle.
- By default `minikv serve` now writes snapshots to the current directory using Redis'
  default save rules; pass `--save ""` for a purely in-memory server.

### Fixed

- A server that fails to start (e.g. port in use) no longer writes a snapshot on exit.
- Graceful shutdown on Windows. The server previously installed no shutdown handler there
  (asyncio's Proactor loop does not support `add_signal_handler`), so Ctrl+Break or a
  `CTRL_BREAK_EVENT` killed it without flushing the AOF or writing the final snapshot.
  The new `minikv.server.shutdown.ShutdownSignals` handles `SIGINT` and `SIGTERM` on all
  platforms and `SIGBREAK` on Windows, using event-loop handlers on Unix and Python
  signal handlers (with periodic wake-ups) on Windows.
- Shutdown handlers are now installed before the server starts, closing a race where a
  stop request arriving right after startup bypassed the graceful path.
- Handlers that existed before the server started (e.g. in an embedding application) are
  restored afterwards; asyncio's `remove_signal_handler` previously reset them to defaults.
- `run_server` exits with code `1` instead of `0` when flushing data during shutdown fails.
- Process tests are cross-platform: on Windows the server is started with
  `CREATE_NEW_PROCESS_GROUP` and stopped with `CTRL_BREAK_EVENT` instead of the POSIX
  signals Windows cannot deliver gracefully (`SIGINT` raised `ValueError`; `SIGTERM`
  called `TerminateProcess`, exiting with code 1).

## [0.3.0] - Phase 3: TCP Server and RESP Protocol

### Added

- Asyncio TCP server (`minikv serve`) speaking RESP2, with pipelining, inline commands,
  binary-safe values, a connection limit, graceful shutdown on `SIGINT`/`SIGTERM`, and a
  background active-expiry cycle.
- `minikv.protocol.resp`: RESP2 encoder, incremental request parser and reply parser,
  with Redis' protocol safety limits.
- `minikv.client.MiniKVClient`: blocking RESP client with lazy connect and reconnect.
- `minikv connect`: interactive shell or one-shot command against a running server.
- Commands: `ECHO`, `DBSIZE`, `FLUSHDB`, `HELLO` (RESP2 only; RESP3 gets `NOPROTO`), and
  connection-level `QUIT`.
- `ProtocolError`, `ReplyError` and `UnsupportedProtocolError` exceptions.
- `minikv.constants` with the default host and port (6380).
- Real-socket integration tests, an end-to-end signal test, and optional compatibility
  tests against `redis-py` and `redis-cli`.

### Changed

- `Repl` accepts any command executor (embedded `Database` or `MiniKVClient`) and gained
  `help_registry` and `banner` parameters.
- The `minikv` command gained the `serve` and `connect` sub-commands; running it without a
  sub-command still opens the embedded shell.

## [0.2.0] - Phase 2: Key Expiry

### Added

- Key expiry commands: `EXPIRE`, `PEXPIRE`, `EXPIREAT`, `PEXPIREAT`, `TTL`, `PTTL`,
  `EXPIRETIME`, `PEXPIRETIME` and `PERSIST`, with `NX`, `XX`, `GT` and `LT` flags.
- `SET` options: `EX`, `PX`, `EXAT`, `PXAT`, `KEEPTTL`, `NX` and `XX`.
- Lazy expiration on every read and heap-based active expiration before every command.
- `minikv.clock` module with the `Clock` protocol, `SystemClock` and `ManualClock`.
- Strict Redis-compatible 64-bit integer parsing (`minikv.utils.numbers`).
- `InvalidIntegerError` and `InvalidExpireTimeError` exceptions.
- `CHANGELOG.md`.

### Changed

- `StorageBackend` gained a `clock` property and expiry methods (`get_expiry`, `set_expiry`,
  `clear_expiry`, `remove_expired`); `set()` accepts `expire_at` and `keep_ttl`.
  Custom backends written for 0.1.0 must implement these.
- `CommandContext` now carries a `clock`.
- `Database` accepts an optional `clock` keyword argument and exposes a `clock` property.
- A plain `SET` now clears any existing expiry, matching Redis.
- `SET` with unexpected extra arguments raises a syntax error instead of an arity error.
- `HELP` lists command names with summaries; `HELP <command>` shows the full usage.

## [0.1.0] - Phase 1: Core Database Engine

### Added

- In-memory, thread-safe storage behind a `StorageBackend` interface.
- Commands: `PING`, `SET`, `GET`, `DEL`, `EXISTS`, `KEYS` (Redis-style glob patterns).
- Command registry with Redis-style arity validation.
- Interactive CLI with `redis-cli`-like output, quoting and `HELP`.
- Pytest suite, `pyproject.toml`, README and AGENTS.md.
