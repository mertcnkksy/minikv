# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/).

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
