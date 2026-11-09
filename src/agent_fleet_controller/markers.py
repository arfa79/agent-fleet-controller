from __future__ import annotations

import contextlib
import json
import os
import socket
from collections.abc import Iterator

from .model import EventKind, WAIT_PAIRS


MARKER_SOCKET_ENV = "AFC_MARKER_SOCKET"
MARKER_KINDS = frozenset(
    kind for pair in WAIT_PAIRS.items() for kind in pair
)


class MarkerError(RuntimeError):
    """Raised when lifecycle instrumentation is unavailable or invalid."""


def emit(kind: EventKind | str) -> None:
    """Emit a content-free, safely reclaimable wait marker to the AFC recorder."""

    try:
        parsed = kind if isinstance(kind, EventKind) else EventKind(kind)
    except ValueError as exc:
        raise MarkerError(f"unsupported lifecycle marker: {kind}") from exc
    if parsed not in MARKER_KINDS:
        raise MarkerError(f"{parsed.value} cannot be emitted by an agent")

    socket_path = os.environ.get(MARKER_SOCKET_ENV)
    if not socket_path:
        raise MarkerError("not running inside 'afc record'")

    payload = json.dumps({"kind": parsed.value}, separators=(",", ":")).encode("ascii")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.settimeout(1.0)
            client.connect(socket_path)
            client.sendall(payload)
    except OSError as exc:
        raise MarkerError("unable to reach the AFC recorder") from exc


@contextlib.contextmanager
def wait(wait_type: str) -> Iterator[None]:
    """Mark a safely reclaimable model, tool, or human wait without content.

    The marker asserts that no process inside the managed sandbox must keep
    running for the enclosed operation to finish. It must not wrap active
    sandbox work merely because the caller is blocked waiting for that work.
    """

    try:
        started = EventKind(f"{wait_type}_wait_started")
    except ValueError as exc:
        raise MarkerError("wait_type must be model, tool, or human") from exc
    emit(started)
    try:
        yield
    finally:
        emit(WAIT_PAIRS[started])
