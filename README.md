# MiniKV

**MiniKV** is a lightweight, Redis-inspired, in-memory key-value database written in pure Python 3.12.
It is built as a learning-oriented yet production-minded project: a small core with clean module
boundaries, full type hints, thorough tests, and an architecture designed to grow into a networked,
persistent data store.

> **Status:** `v0.2.0` — Phase 2 (key expiry). Not intended for production use.

## Features

- **Redis-style commands:** `PING`, `SET`, `GET`, `DEL`, `EXISTS`, `KEYS`
- **Key expiry (TTL)** with Redis 7 semantics: `EXPIRE`, `PEXPIRE`, `EXPIREAT`, `PEXPIREAT`,
  `TTL`, `PTTL`, `EXPIRETIME`, `PEXPIRETIME`, `PERSIST`, plus `SET ... EX | PX | EXAT | PXAT | KEEPTTL`
- **Conditional writes** with `SET ... NX | XX` and `EXPIRE ... NX | XX | GT | LT`
- **Lazy + active expiration**, using a min-heap so expired keys are reclaimed in `O(k log n)`
- **Interactive shell** with `redis-cli`-like output, quoting support, command history, and built-in `HELP`
- **Redis-compatible glob patterns** for `KEYS` (`*`, `?`, `[abc]`, `[^abc]`, `[a-z]`, `\` escapes)
- **Thread-safe storage backend**, ready for a concurrent network server
- **Deterministic time** via an injectable clock, making expiry fully testable without `sleep`
- **Embeddable Python API** — use MiniKV as a library without the CLI
- **Zero runtime dependencies** — standard library only

## Requirements

- Python **3.12** or newer

## Installation

```bash
git clone https://github.com/<your-username>/minikv.git
cd minikv
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Quick Start

Start the interactive shell:

```bash
minikv            # or: python -m minikv
```

Example session:

```text
MiniKV 0.2.0 - type HELP for commands, QUIT to exit.
minikv> SET user:1 "Ada Lovelace"
OK
minikv> GET user:1
"Ada Lovelace"
minikv> SET session:42 "logged-in" EX 2
OK
minikv> TTL session:42
(integer) 2
minikv> SET lock:job worker-1 NX PX 1500
OK
minikv> SET lock:job worker-2 NX
(nil)
minikv> GET session:42          # two seconds later
(nil)
minikv> TTL session:42
(integer) -2
minikv> EXPIRE user:1 100
(integer) 1
minikv> PERSIST user:1
(integer) 1
minikv> TTL user:1
(integer) -1
minikv> QUIT
Bye!
```

CLI options:

| Option        | Description                    |
|---------------|--------------------------------|
| `--version`   | Print the version and exit     |
| `--no-banner` | Skip the welcome banner        |

## Commands

### Core

| Command                | Description                                                | Reply                    |
|------------------------|------------------------------------------------------------|--------------------------|
| `PING [message]`       | Health check; echoes `message` if given                    | `PONG` or the message    |
| `SET key value [options]` | Store a string value (see options below)                | `OK`, or `(nil)` if an `NX`/`XX` condition fails |
| `GET key`              | Retrieve the value of a key                                | The value or `(nil)`     |
| `DEL key [key ...]`    | Delete one or more keys                                    | Number of keys removed   |
| `EXISTS key [key ...]` | Count existing keys (repeated keys are counted repeatedly) | Number of existing keys  |
| `KEYS pattern`         | List keys matching a glob pattern                          | Sorted list of keys      |

`SET` options:

| Option            | Meaning                                                              |
|-------------------|----------------------------------------------------------------------|
| `EX seconds`      | Expire after the given number of seconds                             |
| `PX milliseconds` | Expire after the given number of milliseconds                        |
| `EXAT timestamp`  | Expire at a Unix time in seconds                                     |
| `PXAT timestamp`  | Expire at a Unix time in milliseconds                                |
| `KEEPTTL`         | Keep the key's current expiry instead of clearing it                 |
| `NX`              | Only set the key if it does **not** exist                            |
| `XX`              | Only set the key if it **already** exists                            |

A plain `SET` (without `KEEPTTL`) removes any existing expiry, exactly like Redis.

### Expiry

| Command                                  | Description                                     | Reply |
|------------------------------------------|-------------------------------------------------|-------|
| `EXPIRE key seconds [NX\|XX\|GT\|LT]`     | Set a time to live in seconds                   | `1` if set, `0` otherwise |
| `PEXPIRE key ms [NX\|XX\|GT\|LT]`         | Set a time to live in milliseconds              | `1` / `0` |
| `EXPIREAT key unix-seconds [flags]`      | Expire at an absolute Unix time (seconds)       | `1` / `0` |
| `PEXPIREAT key unix-ms [flags]`          | Expire at an absolute Unix time (milliseconds)  | `1` / `0` |
| `TTL key`                                | Remaining lifetime in seconds                   | Seconds, `-1`, or `-2` |
| `PTTL key`                               | Remaining lifetime in milliseconds              | Milliseconds, `-1`, or `-2` |
| `EXPIRETIME key`                         | Absolute expiry as a Unix time (seconds)        | Timestamp, `-1`, or `-2` |
| `PEXPIRETIME key`                        | Absolute expiry as a Unix time (milliseconds)   | Timestamp, `-1`, or `-2` |
| `PERSIST key`                            | Remove the key's expiry                         | `1` if removed, `0` otherwise |

TTL reply codes: `-2` means the key does not exist; `-1` means it exists without an expiry.

Expiry flags: `NX` (only if no expiry), `XX` (only if an expiry exists), `GT` (only if the new expiry
is later), `LT` (only if the new expiry is earlier). For `GT`/`LT`, a key without expiry is treated
as having an infinite TTL. A zero or negative `EXPIRE` deletes the key immediately.

### Shell-only

`HELP [command]`, `QUIT`, `EXIT`.

Notes:

- Command names and options are case-insensitive; keys and values are case-sensitive.
- Use single or double quotes for values that contain spaces.
- Unlike Redis, `KEYS` returns results in sorted order for deterministic output.

## Using MiniKV as a Library

```python
from minikv import Database, MiniKVError

db = Database()
db.execute("SET", "token", "abc123", "EX", "60")
print(db.execute("TTL", "token"))                 # 60
print(db.execute_line('SET motto "keep it simple"'))  # OK

try:
    db.execute("EXPIRE", "token", "soon")
except MiniKVError as exc:
    print(exc)                                     # ERR value is not an integer or out of range
```

Time is injectable, so expiry can be tested instantly:

```python
from minikv import Database
from minikv.clock import ManualClock

clock = ManualClock()
db = Database(clock=clock)
db.execute("SET", "k", "v", "PX", "500")
clock.advance(milliseconds=500)
assert db.execute("GET", "k") is None
```

Custom commands can be registered without modifying the engine:

```python
from minikv import Database
from minikv.commands import CommandSpec, default_registry

def cmd_dbsize(ctx, args):
    return len(ctx.storage)

registry = default_registry()
registry.register(CommandSpec("DBSIZE", cmd_dbsize, arity=1,
                              summary="Return the number of keys.", usage="DBSIZE"))
db = Database(registry=registry)
```

## Architecture

```text
src/minikv/
├── __init__.py          # Public API and version
├── __main__.py          # `python -m minikv`
├── engine.py            # Database: dispatches commands, runs active expiry
├── clock.py             # Clock protocol, SystemClock, ManualClock
├── errors.py            # Exception hierarchy (MiniKVError and subclasses)
├── responses.py         # Wire-format-agnostic reply types
├── commands/
│   ├── base.py          # CommandSpec, CommandContext, argument helpers
│   ├── registry.py      # Case-insensitive command registry
│   ├── builtin.py       # PING, SET (with options), GET, DEL, EXISTS, KEYS
│   └── expiry.py        # EXPIRE family, TTL family, PERSIST
├── storage/
│   ├── base.py          # StorageBackend abstract interface (incl. expiry)
│   └── memory.py        # Thread-safe dict + expiry heap backend
├── protocol/
│   └── parser.py        # Inline command tokenizer
├── cli/
│   ├── app.py           # Console entry point (argparse)
│   ├── repl.py          # Interactive read-eval-print loop
│   └── formatting.py    # redis-cli-style rendering
└── utils/
    ├── glob.py          # Redis-style glob matching
    └── numbers.py       # Strict 64-bit integer parsing
```

Request flow:

```text
user input ──▶ protocol.parse_command ──▶ Database.execute ──▶ CommandRegistry
                                               │                     │
                                   storage.remove_expired()          ▼
           cli.formatting ◀── Response ◀── handler(ctx, args) ──▶ StorageBackend ◀── Clock
```

### How expiry works

Expiry timestamps are stored as absolute integer milliseconds since the Unix epoch, like Redis.
MiniKV combines two strategies:

1. **Lazy expiration.** Every storage read checks whether the requested key is due and deletes it
   on the spot. Callers can never observe an expired value.
2. **Active expiration.** Before each command, the engine asks the storage to purge every key whose
   time has passed. Expiry times live in a min-heap, so this costs `O(k log n)` for `k` expired keys
   and is effectively free when nothing is due. When a key's TTL changes, its old heap entry is left
   in place and skipped later; the heap is rebuilt once stale entries clearly outnumber live ones.

### Key design decisions

- **The engine is front-end agnostic.** `Database` accepts tokens and returns plain Python values.
  The CLI and a future TCP server share the same execution path; only parsing and rendering differ.
- **Commands are data.** Each command is a `CommandSpec` with a Redis-style arity, so argument
  validation is centralised and new commands are a single registration away.
- **Handlers depend on a context, not on globals.** `CommandContext` carries the storage and clock
  and can grow (persistence log, client state) without changing handler signatures.
- **Time is a dependency.** No code outside `clock.py` reads the system time, which makes every
  expiry rule testable in microseconds.
- **Storage owns expiry bookkeeping.** Command handlers never see expired keys, and a future
  persistent backend only has to honour the same interface.

## Development

Run the test suite:

```bash
pytest
```

Run a specific test file verbosely:

```bash
pytest tests/test_expiry_commands.py -v
```

Tests can also run without installing the package, since `pyproject.toml` adds `src/` to the
Python path for pytest. See [`CHANGELOG.md`](CHANGELOG.md) for release history.

## Roadmap

| Phase | Version | Scope                                                          |
|-------|---------|----------------------------------------------------------------|
| 1     | 0.1.0   | ✅ Core engine, CLI, tests                                      |
| 2     | 0.2.0   | ✅ Key expiry: `EXPIRE`, `TTL`, `SET ... EX`, active expiration |
| 3     | 0.3.0   | TCP server speaking RESP, compatible with `redis-cli`          |
| 4     | 0.4.0   | Persistence: snapshots and append-only file                    |
| 5     | 0.5.0   | Docker image, CI pipeline, benchmarks                          |

## Contributing

Contributions are welcome. Please read [`AGENTS.md`](AGENTS.md) for the project's conventions
(they apply to humans as well as AI coding assistants), add tests for every change, and make sure
`pytest` passes before opening a pull request.
