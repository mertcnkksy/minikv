"""Render responses and errors in a ``redis-cli``-like style."""

from __future__ import annotations

from minikv.responses import Response, SimpleString


def quote(value: str) -> str:
    """Return ``value`` wrapped in double quotes with special characters escaped."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def format_response(response: Response) -> str:
    """Convert a response value into display text.

    Args:
        response: A value returned by :meth:`minikv.Database.execute`.

    Returns:
        Text such as ``OK``, ``"value"``, ``(nil)``, ``(integer) 2`` or a
        numbered list.

    Raises:
        TypeError: If ``response`` is not a supported response type.
    """
    match response:
        case None:
            return "(nil)"
        case SimpleString(value=value):
            return value
        case bool():
            raise TypeError("bool is not a valid response type")
        case int():
            return f"(integer) {response}"
        case str():
            return quote(response)
        case list():
            return _format_array(response)
    raise TypeError(f"unsupported response type: {type(response).__name__}")


def _format_array(items: list[Response]) -> str:
    """Format an array reply as a numbered, indented list."""
    if not items:
        return "(empty array)"
    width = len(str(len(items)))
    lines: list[str] = []
    for position, item in enumerate(items, start=1):
        prefix = f"{position:>{width}}) "
        first, *rest = format_response(item).split("\n")
        lines.append(prefix + first)
        lines.extend(" " * len(prefix) + line for line in rest)
    return "\n".join(lines)


def format_error(error: BaseException) -> str:
    """Format an error as ``(error) <message>``."""
    return f"(error) {error}"
