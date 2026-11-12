from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from .markers import wait


Checkpoint = TypeVar("Checkpoint")
Result = TypeVar("Result")


class ReclaimabilityError(RuntimeError):
    """Raised when a runtime cannot confirm that a sandbox was released."""


def _validate_wait_type(wait_type: str) -> None:
    if wait_type not in {"model", "tool", "human"}:
        raise ValueError("wait_type must be model, tool, or human")


def run_reclaimable_wait(
    wait_type: str,
    *,
    checkpoint: Callable[[], Checkpoint],
    release: Callable[[Checkpoint], bool],
    external_wait: Callable[[], Result],
    restore: Callable[[Checkpoint], object],
) -> Result:
    """Run an external wait only after checkpointing and releasing a sandbox.

    The checkpoint value stays inside the caller and is never sent to AFC. The
    release callback must return the literal value ``True`` before a lifecycle
    marker is emitted. The restore callback is attempted after every marked
    wait, including when the external operation fails.

    This function must run in a durable supervisor outside the managed sandbox.
    It does not make an in-sandbox model call reclaimable by itself.
    """

    _validate_wait_type(wait_type)
    saved_state = checkpoint()
    if release(saved_state) is not True:
        raise ReclaimabilityError("runtime did not confirm sandbox release")

    try:
        with wait(wait_type):
            return external_wait()
    finally:
        restore(saved_state)


async def run_reclaimable_wait_async(
    wait_type: str,
    *,
    checkpoint: Callable[[], Awaitable[Checkpoint]],
    release: Callable[[Checkpoint], Awaitable[bool]],
    external_wait: Callable[[], Awaitable[Result]],
    restore: Callable[[Checkpoint], Awaitable[object]],
) -> Result:
    """Async form of :func:`run_reclaimable_wait` for durable supervisors."""

    _validate_wait_type(wait_type)
    saved_state = await checkpoint()
    if await release(saved_state) is not True:
        raise ReclaimabilityError("runtime did not confirm sandbox release")

    try:
        with wait(wait_type):
            return await external_wait()
    finally:
        await restore(saved_state)
