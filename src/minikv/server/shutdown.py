"""Cross-platform graceful shutdown requests.

A server must flush its data before exiting, so it needs to *receive* a
shutdown request instead of being killed. Operating systems deliver such
requests differently:

==========  ===========================================================
Platform    Graceful shutdown requests
==========  ===========================================================
Linux/Unix  ``SIGINT`` (Ctrl+C) and ``SIGTERM`` (``kill``, systemd, Docker)
Windows     ``SIGINT`` (Ctrl+C in the console) and ``SIGBREAK``
            (Ctrl+Break, a ``CTRL_BREAK_EVENT`` sent to the process group,
            or closing the console window)
==========  ===========================================================

On Windows, *forceful* termination (``TerminateProcess``: Task Manager,
``taskkill /F``, ``Stop-Process``, or ``Popen.terminate()`` /
``send_signal(SIGTERM)``) cannot be intercepted by any program. Data safety
then relies on the append-only file's fsync policy.

Handlers are installed in one of two ways:

* **Event-loop handlers** (``loop.add_signal_handler``) on Unix. The signal
  only wakes the loop, and the callback runs as a normal loop callback.
* **Python signal handlers** (``signal.signal``) on Windows, where the
  Proactor event loop does not support ``add_signal_handler``. The handler
  does nothing but schedule the same callback with ``call_soon_threadsafe``.
  Python runs signal handlers only between bytecodes in the main thread, so
  :meth:`ShutdownSignals.wait` wakes up periodically to guarantee that a
  pending handler gets a chance to run.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Iterable
from types import FrameType, TracebackType
from typing import Any

logger = logging.getLogger("minikv.server")

SHUTDOWN_SIGNAL_NAMES: tuple[str, ...] = ("SIGINT", "SIGTERM", "SIGBREAK")
"""Signals that request a graceful shutdown, where the platform defines them."""

DEFAULT_POLL_INTERVAL = 0.2
"""Seconds between wake-ups while waiting, so Python-level handlers can run."""


def shutdown_signals() -> tuple[signal.Signals, ...]:
    """Return the graceful shutdown signals available on this platform.

    ``SIGINT`` and ``SIGTERM`` everywhere, plus ``SIGBREAK`` on Windows.
    """
    return tuple(getattr(signal, name) for name in SHUTDOWN_SIGNAL_NAMES if hasattr(signal, name))


def _loop_handlers_supported() -> bool:
    """Whether ``loop.add_signal_handler`` is expected to work on this platform."""
    return sys.platform != "win32"


class ShutdownSignals:
    """Turn shutdown signals into an awaitable request.

    Use it as a context manager around the server's lifetime; previous
    handlers are restored on exit. Install it *before* the server starts
    accepting clients, so a request arriving during startup is not lost.

    Example:
        >>> async def main() -> None:                       # doctest: +SKIP
        ...     with ShutdownSignals() as shutdown:
        ...         await server.start()
        ...         received = await shutdown.wait()
        ...         await server.stop()

    Args:
        loop: The event loop to notify. Defaults to the running loop.
        signals: Signals to handle. Defaults to :func:`shutdown_signals`.
        use_loop_handlers: Prefer event-loop handlers. Defaults to ``True``
            except on Windows. If the loop refuses, Python signal handlers
            are used instead.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        signals: Iterable[signal.Signals] | None = None,
        use_loop_handlers: bool | None = None,
    ) -> None:
        self._loop = loop if loop is not None else asyncio.get_running_loop()
        self._signals = tuple(signals) if signals is not None else shutdown_signals()
        self._use_loop_handlers = (
            _loop_handlers_supported() if use_loop_handlers is None else use_loop_handlers
        )
        self._event = asyncio.Event()
        self._received: signal.Signals | None = None
        self._loop_handled: dict[signal.Signals, Any] = {}
        self._previous: dict[signal.Signals, Any] = {}

    # -- installation -----------------------------------------------------------

    @property
    def handled(self) -> tuple[signal.Signals, ...]:
        """Signals for which a handler is currently installed."""
        return tuple(self._loop_handled) + tuple(self._previous)

    def install(self) -> None:
        """Install handlers for every configured signal.

        A signal that cannot be handled here (for example outside the main
        thread) is logged and skipped instead of failing the server.
        """
        for sig in self._signals:
            if self._use_loop_handlers:
                previous = signal.getsignal(sig)
                try:
                    self._loop.add_signal_handler(sig, self._request, sig)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass  # fall back to a Python-level handler below
                else:
                    self._loop_handled[sig] = previous
                    continue
            try:
                self._previous[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError) as exc:
                logger.warning("cannot handle %s for graceful shutdown: %s", sig.name, exc)

    def restore(self) -> None:
        """Remove the handlers and restore the previous ones."""
        for sig, previous in self._loop_handled.items():
            with contextlib.suppress(Exception):
                self._loop.remove_signal_handler(sig)
            # remove_signal_handler() always resets to the default handler;
            # put back whatever was installed before (e.g. by an embedding app).
            if previous is not None:
                with contextlib.suppress(ValueError, OSError, TypeError):
                    signal.signal(sig, previous)
        self._loop_handled.clear()
        for sig, previous in self._previous.items():
            with contextlib.suppress(ValueError, OSError, TypeError):
                signal.signal(sig, previous)
        self._previous.clear()

    def __enter__(self) -> ShutdownSignals:
        self.install()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.restore()

    # -- delivery -----------------------------------------------------------------

    def _on_signal(self, signum: int, frame: FrameType | None) -> None:
        """Python-level handler: hand the request over to the event loop."""
        with contextlib.suppress(RuntimeError):  # loop already closed
            self._loop.call_soon_threadsafe(self._request, signal.Signals(signum))

    def _request(self, sig: signal.Signals) -> None:
        if self._received is None:
            self._received = sig
            self._event.set()
        else:
            logger.warning("received %s again; shutdown is already in progress", sig.name)

    @property
    def received(self) -> signal.Signals | None:
        """The first shutdown signal received, if any."""
        return self._received

    async def wait(self, poll_interval: float = DEFAULT_POLL_INTERVAL) -> signal.Signals:
        """Wait until a shutdown signal arrives and return it.

        Args:
            poll_interval: Maximum time between wake-ups. Waking up regularly
                lets Python-level signal handlers run promptly on platforms
                where a signal does not wake the event loop by itself.
        """
        while not self._event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._event.wait(), poll_interval)
        assert self._received is not None
        return self._received
