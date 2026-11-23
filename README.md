# MiniKV

**MiniKV** is a lightweight, Redis-inspired, in-memory key-value database written in pure Python 3.12.
It is built as a learning-oriented yet production-minded project: a small core with clean module
boundaries, full type hints, thorough tests, and an architecture designed to grow into a networked,
persistent data store.

> **Status:** `v0.1.0` — Phase 1 (core database engine). Not intended for production use.

## Features

- **Redis-style commands:** `PING`, `SET`, `GET`, `DEL`, `EXISTS`, `KEYS`
- **Interactive shell** with `redis-cli`-like output, quoting support, command history, and built-in `HELP`
- **Redis-compatible glob patterns** for `KEYS` (`*`, `?`, `[abc]`, `[^abc]`, `[a-z]`, `\` escapes)
- **Thread-safe storage backend**, ready for a concurrent network server
- **Embeddable Python API** — use MiniKV as a library without the CLI
- **Zero runtime dependencies** — standard library only
- **Fully typed** and documented with docstrings

## Requirements

- Python **3.12** or newer

## Installation

```bash
git clone https://github.com/<mertcnkksy>/minikv.git
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
MiniKV 0.1.0 - type HELP for commands, QUIT to exit.
minikv> PING
PONG
minikv> SET user:1 "Ada Lovelace"
OK
minikv> SET user:2 Alan
OK
minikv> GET user:1
"Ada Lovelace"
minikv> GET user:3
(nil)
minikv> EXISTS user:1 user:2 user:3
(integer) 2
minikv> KEYS user:*
1) "user:1"
2) "user:2"
minikv> DEL user:2
(integer) 1
minikv> GET
(error) ERR wrong number of arguments for 'get' command
minikv> QUIT
Bye!
```

CLI options:

| Option        | Description                    |
|---------------|--------------------------------|
| `--version`   | Print the version and exit     |
| `--no-banner` | Skip the welcome banner        |

## Commands

| Command                | Description                                                   | Reply                        |
|------------------------|---------------------------------------------------------------|------------------------------|
| `PING [message]`       | Health check; echoes `message` if given                       | `PONG` or the message        |
| `SET key value`        | Store a string value, overwriting any existing value          | `OK`                         |
| `GET key`              | Retrieve the value of a key                                   | The value or `(nil)`         |
| `DEL key [key ...]`    | Delete one or more keys                                       | Number of keys removed       |
| `EXISTS key [key ...]` | Count existing keys (repeated keys are counted repeatedly)    | Number of existing keys      |
| `KEYS pattern`         | List keys matching a glob pattern                             | Sorted list of keys          |

Shell-only commands: `HELP [command]`, `QUIT`, `EXIT`.

Notes:

- Command names are case-insensitive; keys and values are case-sensitive.
- Use single or double quotes for values that contain spaces.
- Unlike Redis, `KEYS` returns results in sorted order for deterministic output.

## Using MiniKV as a Library

```python
from minikv import Database, MiniKVError

db = Database()
db.execute("SET", "language", "Python")
print(db.execute("GET", "language"))        # Python
print(db.execute_line('SET motto "keep it simple"'))  # OK

try:
    db.execute("GET")
except MiniKVError as exc:
    print(exc)                                # ERR wrong number of arguments for 'get' command
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
├── engine.py            # Database: dispatches commands to handlers
├── errors.py            # Exception hierarchy (MiniKVError and subclasses)
├── responses.py         # Wire-format-agnostic reply types
├── commands/
│   ├── base.py          # CommandSpec, CommandContext, handler type
│   ├── registry.py      # Case-insensitive command registry
│   └── builtin.py       # PING, SET, GET, DEL, EXISTS, KEYS
├── storage/
│   ├── base.py          # StorageBackend abstract interface
│   └── memory.py        # Thread-safe dict-based backend
├── protocol/
│   └── parser.py        # Inline command tokenizer
├── cli/
│   ├── app.py           # Console entry point (argparse)
│   ├── repl.py          # Interactive read-eval-print loop
│   └── formatting.py    # redis-cli-style rendering
└── utils/
    └── glob.py          # Redis-style glob matching
```

Request flow:

```text
user input ──▶ protocol.parse_command ──▶ Database.execute ──▶ CommandRegistry
                                                                    │
           cli.formatting ◀── Response ◀── handler(ctx, args) ◀─────┘
                                                │
                                         StorageBackend
```

Key design decisions:

- **The engine is front-end agnostic.** `Database` accepts tokens and returns plain Python values.
  The CLI and a future TCP server share the same execution path; only parsing and rendering differ.
- **Commands are data.** Each command is a `CommandSpec` with a Redis-style arity, so argument
  validation is centralised and new commands are a single registration away.
- **Handlers depend on a context, not on globals.** `CommandContext` can grow (clock, persistence log,
  client state) without changing handler signatures.
- **Storage sits behind an interface.** Expiry and durability can be added by new backends or
  wrappers without touching command logic.

## Development

Run the test suite:

```bash
pytest
```

Run a specific test file verbosely:

```bash
pytest tests/test_engine.py -v
```

Tests can also run without installing the package, since `pyproject.toml` adds `src/` to the
Python path for pytest.

## Roadmap

| Phase | Version | Scope                                                          |
|-------|---------|----------------------------------------------------------------|
| 1     | 0.1.0   | ✅ Core engine, CLI, tests                                      |
| 2     | 0.2.0   | Key expiry: `EXPIRE`, `TTL`, `SET ... EX`                       |
| 3     | 0.3.0   | TCP server speaking RESP, compatible with `redis-cli`          |
| 4     | 0.4.0   | Persistence: snapshots and append-only file                    |
| 5     | 0.5.0   | Docker image, CI pipeline, benchmarks                          |

## Contributing

Contributions are welcome. Please read [`AGENTS.md`](AGENTS.md) for the project's conventions
(they apply to humans as well as AI coding assistants), add tests for every change, and make sure
`pytest` passes before opening a pull request.
