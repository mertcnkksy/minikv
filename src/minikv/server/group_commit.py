"""Group commit: one durability sync for every client served in a loop pass.

With ``appendfsync always`` the reply to a write may only be sent once the
write is on disk. Syncing after each command costs one ``fsync`` per write,
so pipelines and concurrent clients gain nothing. Instead, each connection
executes the commands it received and then calls
:meth:`GroupCommitter.wait_durable`. The first caller schedules a single
commit with ``loop.call_soon``; every connection that executes commands
before that callback runs joins the same commit. Only after it completes
are the replies written.

The commit runs on the event loop thread, so no command can execute while
it is in progress, exactly as in Redis where the AOF is synced before
replies are sent at the end of an event-loop iteration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class GroupCommitter:
    """Coordinate one shared commit per event-loop pass.

    Args:
        needs_commit: Returns whether there is anything to make durable.
        commit: Makes pending writes durable; may raise.
    """

    def __init__(self, needs_commit: Callable[[], bool], commit: Callable[[], None]) -> None:
        self._needs_commit = needs_commit
        self._commit = commit
        self._pending: asyncio.Future[None] | None = None
        self.commits = 0
        """Number of commits performed (each covering one or more writes)."""

    async def wait_durable(self) -> None:
        """Return once everything executed so far is durable.

        Returns immediately when nothing is pending. Otherwise joins the
        scheduled commit (scheduling it if needed). Replies of read-only
        commands also wait while a commit is scheduled, so no client can
        observe a write before it is durable.

        Raises:
            Exception: Whatever the commit raised; every waiter receives it.
        """
        if self._pending is None:
            if not self._needs_commit():
                return
            loop = asyncio.get_running_loop()
            self._pending = loop.create_future()
            loop.call_soon(self._run)
        await asyncio.shield(self._pending)

    def _run(self) -> None:
        future, self._pending = self._pending, None
        assert future is not None
        try:
            self._commit()
        except Exception as exc:
            future.set_exception(exc)
            future.exception()  # mark as retrieved: waiters may have been cancelled
        else:
            self.commits += 1
            future.set_result(None)
