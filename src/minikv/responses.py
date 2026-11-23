"""Response value types returned by command handlers.

Handlers return plain Python values so the engine stays independent of any
wire format. A future network layer can serialise these values to RESP,
while the CLI renders them in a ``redis-cli``-like style.

Mapping between Python values and Redis reply kinds:

==================  ============================
Python value        Redis reply kind
==================  ============================
``SimpleString``    status reply (``OK``, ``PONG``)
``str``             bulk string
``None``            null bulk string (``(nil)``)
``int``             integer reply
``list``            array reply
==================  ============================
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SimpleString:
    """A short status reply such as ``OK`` or ``PONG``.

    Attributes:
        value: The status text.
    """

    value: str

    def __str__(self) -> str:
        return self.value


OK = SimpleString("OK")
PONG = SimpleString("PONG")

type Response = SimpleString | str | int | None | list[Response]
