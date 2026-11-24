# MiniKV

**MiniKV** is a lightweight, Redis-inspired key-value database written in pure Python 3.12.
It speaks the Redis protocol (RESP2), so `redis-cli`, `redis-benchmark`, and Redis client libraries
can talk to it directly, and it persists data to disk with snapshots and an append-only file. It is built as a learning-oriented yet production-minded project: a small
core with clean module boundaries, full type hints, and a thorough test suite.

> **Status:** `v0.4.0` — Phase 4 (persistence). Not intended for production use.

## Features

- **Redis-compatible TCP server** built on `asyncio`, speaking RESP2 with pipelining support
- **Durable storage:** checksummed binary snapshots and an append-only file (AOF) with
  `always` / `everysec` / `no` fsync policies, background saves, and background AOF compaction
- **Works with official Redis tooling:** `redis-cli`, `redis-benchmark`, `redis-py`, and plain `telnet`/`nc`
- **Binary-safe values** over the network
- **Key expiry (TTL)** with Redis 7 semantics, lazy + active expiration, and a background expiry cycle
- **Conditional writes** with `SET ... NX | XX` and `EXPIRE ... NX | XX | GT | LT`
- **Three ways to use it:** embedded shell, network server + client shell, or as a Python library
- **Crash safety:** atomic file replacement, CRC-32 checksums, and automatic repair of an AOF
  whose last command was cut off by a crash
- **Graceful shutdown** on `SIGINT`/`SIGTERM`, connection limits, and protocol-level safety limits
- **Deterministic time** via an injectable clock, making expiry fully testable without `sleep`
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

### 1. Run the server

```bash
minikv serve --dir data --appendonly     # listens on 127.0.0.1:6380, data in ./data
```

```text
2026-11-24 10:00:00,000 INFO    minikv.server: MiniKV 0.4.0 ready to accept connections on 127.0.0.1:6380
```

MiniKV uses port **6380** by default so it can run next to a real Redis on 6379. Stop it with
`Ctrl+C`; on the next start your data is loaded back (see [Persistence](#persistence)).

#### Stopping the server safely

On shutdown MiniKV stops accepting clients, closes connections, fsyncs the append-only file and,
when save rules are configured, writes a final snapshot. It then exits with code `0` (or `1` if data
could not be flushed). These requests trigger a graceful shutdown:

| Platform      | Graceful shutdown                                                              |
|---------------|--------------------------------------------------------------------------------|
| Linux / macOS | `Ctrl+C` (`SIGINT`), `kill <pid>` / systemd / Docker (`SIGTERM`)               |
| Windows       | `Ctrl+C` (`SIGINT`), `Ctrl+Break` or closing the console window (`SIGBREAK`), `CTRL_BREAK_EVENT` sent to the server's process group |

On Windows, *forceful* termination — Task Manager, `taskkill /F`, `Stop-Process`, or
`Popen.terminate()` / `send_signal(SIGTERM)` from Python — calls `TerminateProcess`, which no program
can intercept. To stop a MiniKV process started from Python on Windows, start it with
`creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` and send `signal.CTRL_BREAK_EVENT`. With the
append-only file enabled, even a forceful kill loses at most the writes not yet fsynced
(about one second with `everysec`).

### 2. Connect with the built-in client

```bash
minikv connect
```

```text
Connected to MiniKV at 127.0.0.1:6380. Type HELP for commands, QUIT to exit.
127.0.0.1:6380> SET user:1 "Ada Lovelace"
OK
127.0.0.1:6380> SET session:42 "logged-in" EX 60
OK
127.0.0.1:6380> TTL session:42
(integer) 60
127.0.0.1:6380> KEYS *
1) "session:42"
2) "user:1"
127.0.0.1:6380> QUIT
Bye!
```

Run a single command and exit (useful in scripts; the exit code is `1` on errors):

```bash
minikv connect GET user:1
```

### 3. …or use any Redis tool

```bash
redis-cli -p 6380 SET greeting "hello world"
redis-cli -p 6380 GET greeting
printf 'PING\r\n' | nc 127.0.0.1 6380        # inline commands work too
```

```python
import redis

r = redis.Redis(port=6380, protocol=2, decode_responses=True)
r.set("token", "abc123", ex=60)
print(r.get("token"), r.ttl("token"))        # abc123 60
```

> MiniKV speaks RESP2. Recent `redis-py` versions try RESP3 first and fall back automatically,
> printing a notice; pass `protocol=2` to skip the negotiation.

### Embedded mode

Without a server, `minikv` starts a shell on an in-process database (data lives only in that process):

```bash
minikv
```

### CLI reference

| Command                         | Description                                         |
|---------------------------------|-----------------------------------------------------|
| `minikv`                        | Interactive shell on an embedded database           |
| `minikv serve [options]`        | Run the TCP server                                  |
| `minikv connect [options] [cmd]`| Shell connected to a server, or run one command     |
| `minikv --version`              | Print the version                                   |

`serve` options:

| Option               | Default                    | Description                                         |
|----------------------|----------------------------|-----------------------------------------------------|
| `--host`             | `127.0.0.1`                | Interface to bind; `0.0.0.0` accepts remote clients |
| `-p`, `--port`       | `6380`                     | TCP port                                            |
| `--max-clients`      | `10000`                    | Maximum simultaneous connections                    |
| `--log-level`        | `INFO`                     | `DEBUG`, `INFO`, `WARNING` or `ERROR`               |
| `--dir`              | current directory          | Directory for data files (created if missing)       |
| `--save RULES`       | `"3600 1 300 100 60 10000"`| Snapshot rules as `<seconds> <changes>` pairs; `""` disables |
| `--dbfilename`       | `dump.mkv`                 | Snapshot file name                                  |
| `--appendonly`       | off                        | Log every write to the append-only file             |
| `--appendfilename`   | `appendonly.aof`           | Append-only file name                               |
| `--appendfsync`      | `everysec`                 | `always`, `everysec` or `no`                        |

`connect` options: `--host`, `-p/--port`, `--no-banner`.

## Commands

### Strings and keys

| Command                   | Description                                                | Reply                    |
|---------------------------|------------------------------------------------------------|--------------------------|
| `SET key value [options]` | Store a string value (see options below)                   | `OK`, or `(nil)` if an `NX`/`XX` condition fails |
| `GET key`                 | Retrieve the value of a key                                | The value or `(nil)`     |
| `DEL key [key ...]`       | Delete one or more keys                                    | Number of keys removed   |
| `EXISTS key [key ...]`    | Count existing keys (repeated keys are counted repeatedly) | Number of existing keys  |
| `KEYS pattern`            | List keys matching a glob pattern                          | Sorted list of keys      |

`SET` options: `EX seconds`, `PX milliseconds`, `EXAT unix-seconds`, `PXAT unix-ms`, `KEEPTTL`,
`NX` (only if the key does not exist), `XX` (only if it exists). A plain `SET` removes any existing
expiry, exactly like Redis.

### Expiry

| Command                               | Description                                    | Reply |
|---------------------------------------|------------------------------------------------|-------|
| `EXPIRE key seconds [NX\|XX\|GT\|LT]`  | Set a time to live in seconds                  | `1` if set, `0` otherwise |
| `PEXPIRE key ms [NX\|XX\|GT\|LT]`      | Set a time to live in milliseconds             | `1` / `0` |
| `EXPIREAT key unix-seconds [flags]`   | Expire at an absolute Unix time (seconds)      | `1` / `0` |
| `PEXPIREAT key unix-ms [flags]`       | Expire at an absolute Unix time (milliseconds) | `1` / `0` |
| `TTL key` / `PTTL key`                | Remaining lifetime in seconds / milliseconds   | Value, `-1`, or `-2` |
| `EXPIRETIME key` / `PEXPIRETIME key`  | Absolute expiry as a Unix time                 | Timestamp, `-1`, or `-2` |
| `PERSIST key`                         | Remove the key's expiry                        | `1` if removed, `0` otherwise |

`-2` means the key does not exist; `-1` means it exists without an expiry. Flags: `NX` (only if no
expiry), `XX` (only if an expiry exists), `GT`/`LT` (only if the new expiry is later/earlier; a key
without expiry counts as infinite). A zero or negative `EXPIRE` deletes the key.

### Server and connection

| Command                 | Description                                              | Reply             |
|-------------------------|----------------------------------------------------------|-------------------|
| `PING [message]`        | Health check; echoes `message` if given                  | `PONG` or message |
| `ECHO message`          | Return the message                                       | The message       |
| `DBSIZE`                | Number of live keys                                      | Integer           |
| `FLUSHDB [ASYNC\|SYNC]`  | Delete every key                                         | `OK`              |
| `HELLO [protover]`      | Protocol handshake; only version `2` is supported        | Server info, or `NOPROTO` error |
| `QUIT`                  | Close the connection                                     | `OK`              |

### Persistence

| Command        | Description                                              | Reply                     |
|----------------|----------------------------------------------------------|---------------------------|
| `SAVE`         | Write a snapshot synchronously                           | `OK`                      |
| `BGSAVE`       | Write a snapshot in the background                       | `Background saving started` |
| `BGREWRITEAOF` | Compact the append-only file in the background           | `Background append only file rewriting started` |
| `LASTSAVE`     | Unix time of the last successful snapshot                | Integer                   |

These commands return `ERR persistence is not configured` on a pure in-memory database
(for example the embedded shell).

Shell-only: `HELP [command]`, `EXIT`. Command names and options are case-insensitive; keys and
values are case-sensitive. Unlike Redis, `KEYS` returns results sorted.

## Persistence

MiniKV offers the same two durability mechanisms as Redis, and they can be combined.

**Snapshots** store the whole dataset in `dump.mkv` at a point in time. They are written
automatically according to the `--save` rules (for example, `"300 100"` means "if at least 100 keys
changed and 300 seconds passed"), on `SAVE`/`BGSAVE`, and on shutdown. They are compact and fast to
load, but writes since the last snapshot are lost if the process crashes.

**The append-only file** (`--appendonly`) records every write in `appendonly.aof`, in the same RESP
format clients use, so you can read it with any text editor. With the default `everysec` policy at
most about one second of writes can be lost on a power failure; a crash of the MiniKV process alone
never loses an acknowledged write.

| `--appendfsync` | Durability                                  | Measured `SET` throughput* |
|-----------------|---------------------------------------------|----------------------------|
| (AOF off)       | Snapshots only                              | ~45,000 req/s              |
| `no`            | The OS decides when to flush                | —                          |
| `everysec`      | At most ~1 s of writes lost on power loss   | ~36,000 req/s              |
| `always`        | Every write is on disk before the reply     | ~5,700 req/s               |

\* `redis-benchmark -t set -n 20000 -c 20` in a small container; indicative only.

On startup the AOF is loaded when it is enabled and exists, because it is the more complete
record; otherwise the snapshot is used. Turning AOF on for an existing snapshot-only dataset is
safe: the log is first created from the loaded data.

### How it works

- **Only real changes are logged.** The storage keeps a *dirty counter* of modifications; a command
  is logged only if it changed the counter. Reads, failed `SET ... NX`, or `DEL` of a missing key
  never touch the log.
- **Time-independent log entries.** Relative expiry is rewritten to absolute time before logging:
  `SET k v EX 60` becomes `SET k v PXAT <unix-ms>` and `EXPIRE k 60` becomes `PEXPIREAT k <unix-ms>`.
  Replaying the log a week later therefore reproduces exactly the right expiry times.
- **Ordered logging.** Executing a command and logging it happen under one lock, so the log order
  always matches the execution order.
- **Point-in-time background jobs.** `BGSAVE` and `BGREWRITEAOF` copy the dataset under the lock
  (cheap: references to immutable strings) and write it from a background thread, so clients keep
  being served.
- **Rewrite hand-over.** While the compacted AOF is written, new writes go to both the old log and
  an in-memory buffer. When the copy is complete, the buffer is appended and the new file atomically
  replaces the old one — no write is lost.
- **Atomic, verified files.** Files are written to a temporary file, `fsync`-ed and moved into place
  with `os.replace`. Snapshots end with a record count and a CRC-32 checksum and are rejected if
  corrupted. If the last AOF command was cut off by a crash, it is dropped and the file is truncated
  to the last complete command (like Redis' `aof-load-truncated yes`); corruption elsewhere stops the
  server instead of silently losing data.

Snapshot file format (big-endian):

```text
"MINIKV" | version u16 | records... | 0xFF | count u64 | crc32 u32
record: 0x01 key value | 0x02 expire_at_ms(i64) key value      (strings: u32 length + UTF-8 bytes)
```

## Using MiniKV as a Library

Embedded:

```python
from minikv import Database, MiniKVError

db = Database()
db.execute("SET", "token", "abc123", "EX", "60")
print(db.execute("TTL", "token"))                  # 60

try:
    db.execute("EXPIRE", "token", "soon")
except MiniKVError as exc:
    print(exc)                                      # ERR value is not an integer or out of range
```

Network client:

```python
from minikv.client import MiniKVClient

with MiniKVClient(port=6380) as client:
    client.execute("SET", "greeting", "hello")
    print(client.execute("GET", "greeting"))       # hello
```

Server inside your own asyncio application:

```python
import asyncio
from minikv.server import MiniKVServer, ServerConfig

async def main() -> None:
    async with MiniKVServer(config=ServerConfig(port=6380)) as server:
        print("listening on", server.address)
        await server.serve_forever()

asyncio.run(main())
```

Persistence for an embedded database:

```python
from pathlib import Path
from minikv import Database
from minikv.persistence import PersistenceConfig, PersistenceManager

db = Database()
manager = PersistenceManager(db, PersistenceConfig(directory=Path("data"), aof_enabled=True))
manager.load()          # restore previous data
manager.start()         # start logging writes
db.execute("SET", "k", "v")
manager.close()         # flush and close (also takes a final snapshot if save rules exist)
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

## Architecture

```text
src/minikv/
├── __init__.py          # Public API and version
├── __main__.py          # `python -m minikv`
├── constants.py         # Default host and port
├── engine.py            # Database: dispatches commands, runs active expiry
├── clock.py             # Clock protocol, SystemClock, ManualClock
├── client.py            # Blocking RESP client
├── errors.py            # Exception hierarchy (MiniKVError and subclasses)
├── responses.py         # Wire-format-agnostic reply types
├── commands/
│   ├── base.py          # CommandSpec, CommandContext, argument helpers
│   ├── registry.py      # Case-insensitive command registry
│   ├── builtin.py       # PING, SET, GET, DEL, EXISTS, KEYS
│   ├── expiry.py        # EXPIRE family, TTL family, PERSIST
│   ├── server.py        # ECHO, DBSIZE, FLUSHDB, HELLO
│   └── persistence.py   # SAVE, BGSAVE, BGREWRITEAOF, LASTSAVE
├── storage/
│   ├── base.py          # StorageBackend abstract interface
│   └── memory.py        # Thread-safe dict + expiry heap backend
├── persistence/
│   ├── manager.py       # PersistenceManager: load, save, rewrite, automatic duties
│   ├── snapshot.py      # Checksummed binary snapshot format
│   ├── aof.py           # Append-only file, fsync policies, replay and repair
│   └── fileutil.py      # Atomic writes and directory fsync
├── protocol/
│   ├── parser.py        # Inline command tokenizer
│   └── resp.py          # RESP2 encoder, request parser, reply parser
├── server/
│   ├── server.py        # MiniKVServer, ServerConfig, run_server
│   ├── connection.py    # Per-client read/execute/write loop
│   └── shutdown.py      # Cross-platform graceful shutdown signals
├── cli/
│   ├── app.py           # `minikv`, `minikv serve`, `minikv connect`
│   ├── repl.py          # Interactive shell (embedded or remote)
│   └── formatting.py    # redis-cli-style rendering
└── utils/
    ├── glob.py          # Redis-style glob matching
    └── numbers.py       # Strict 64-bit integer parsing
```

Request flow over the network:

```text
socket ─▶ RequestParser ─▶ ClientConnection ─▶ Database.execute ─▶ handler ─▶ StorageBackend
                                  │                  │               │
socket ◀─ encode_response ◀───────┴─── Response ◀────┼───────────────┘
                                                     └─▶ write listeners ─▶ AppendOnlyFile
```

The embedded shell uses the same `Database.execute` path; only parsing and rendering differ.

### Server design

- **Single-threaded event loop, like Redis.** All clients share one `asyncio` loop and commands
  run one at a time on it, so every command is atomic without locks in the command layer, while
  thousands of idle connections cost almost nothing.
- **Pipelining.** Each read may contain many commands; all of them are executed and their replies
  are written back in a single `write` call.
- **Incremental, linear-time parsing.** The request parser keeps state between reads, so a large
  value arriving in many small packets is copied only once.
- **Pure protocol layer.** `protocol/resp.py` never touches sockets, which keeps it exhaustively
  testable and shared between the server and the client.
- **Safety limits.** Inline requests are capped at 64 KB, bulk strings at 512 MB, and requests at
  1M arguments. Malformed input gets a `Protocol error` reply and the connection is closed, because
  the byte stream can no longer be trusted.
- **Binary safety.** Values are decoded with UTF-8 `surrogateescape`, so arbitrary bytes round-trip
  unchanged while ordinary text stays ordinary `str`.
- **Cross-platform graceful shutdown.** `minikv.server.shutdown` installs event-loop signal
  handlers on Unix and Python signal handlers (including `SIGBREAK`) on Windows, where the Proactor
  event loop does not support `add_signal_handler`. Handlers are installed before data is loaded,
  so a stop request during startup is not lost, and previous handlers are restored afterwards.
- **Background maintenance cycle.** Every 100 ms the server purges expired keys, fsyncs the AOF
  under the `everysec` policy, and checks the automatic snapshot and AOF rewrite rules, mirroring
  Redis' `serverCron`.

### Expiry

Expiry timestamps are absolute integer milliseconds since the Unix epoch. Reads check the requested
key and delete it if it is due (lazy expiration), and the engine plus the server's background cycle
purge due keys from a min-heap in `O(k log n)` (active expiration). Stale heap entries left behind by
TTL changes are skipped and periodically compacted.

## Performance

Indicative numbers from `redis-benchmark` against MiniKV on CPython 3.12 inside a small container
(20 clients, persistence disabled). Real Redis is far faster; these numbers are a baseline. See
[Persistence](#persistence) for the cost of each fsync policy.

| Test | No pipelining | Pipeline of 16 |
|------|---------------|----------------|
| SET  | ~45,000 req/s | ~100,000 req/s |
| GET  | ~59,000 req/s | ~110,000 req/s |
| PING | ~55,000 req/s | —              |

```bash
redis-benchmark -p 6380 -t ping,set,get -n 20000 -c 20 -q
redis-benchmark -p 6380 -t set,get -n 100000 -c 20 -P 16 -q
```

## Development

```bash
pytest                                   # full suite
pytest tests/test_server.py -v           # one module
```

The suite includes real-socket integration tests, end-to-end tests that start real server
processes, stop them gracefully and restart them to verify that data survives, and crash-recovery
tests for truncated and corrupted files. Process tests are cross-platform: on Unix they send
`SIGINT`/`SIGTERM`; on Windows the server runs in its own process group and receives
`CTRL_BREAK_EVENT` (see `tests/helpers.py`). Shutdown handlers are also tested in-process with
`signal.raise_signal`, which exercises the real handler path of the platform running the tests. Compatibility tests against `redis-py` and `redis-cli`
run when those tools are installed and are skipped otherwise. See [`CHANGELOG.md`](CHANGELOG.md)
for release history.

## Roadmap

| Phase | Version | Scope                                                          |
|-------|---------|----------------------------------------------------------------|
| 1     | 0.1.0   | ✅ Core engine, CLI, tests                                      |
| 2     | 0.2.0   | ✅ Key expiry: `EXPIRE`, `TTL`, `SET ... EX`, active expiration |
| 3     | 0.3.0   | ✅ TCP server speaking RESP2, compatible with `redis-cli`       |
| 4     | 0.4.0   | ✅ Persistence: snapshots and append-only file                  |
| 5     | 0.5.0   | Docker image, CI pipeline, benchmarks                          |

## Known Limitations

- If a disk write fails after a command was applied in memory, the client receives an error but the
  in-memory change stays; the log and memory can diverge until the next successful rewrite.
- Background jobs copy the list of keys and values, briefly holding the lock and temporarily using
  extra memory proportional to the number of keys. Redis avoids this with `fork()` and copy-on-write.
- Snapshots are read fully into memory when loading, and `KEYS` scans the whole keyspace.
- Python's GIL means background threads share the CPU with the event loop.

## Contributing

Contributions are welcome. Please read [`AGENTS.md`](AGENTS.md) for the project's conventions
(they apply to humans as well as AI coding assistants), add tests for every change, and make sure
`pytest` passes before opening a pull request.
