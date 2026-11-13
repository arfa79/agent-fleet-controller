from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar

from .durable import ReclaimabilityError
from .markers import wait


CheckpointT = TypeVar("CheckpointT")
RequestT = TypeVar("RequestT")
ResultT = TypeVar("ResultT")


class CheckpointAdapter(Protocol[CheckpointT]):
    """Persist the application state needed to resume a workflow."""

    async def checkpoint(self, workflow_id: str) -> CheckpointT:
        """Return an opaque reference to durably persisted workflow state."""


class SandboxRuntime(Protocol[CheckpointT]):
    """Manage the sandbox allocation associated with a workflow."""

    async def release(self, workflow_id: str, checkpoint: CheckpointT) -> bool:
        """Release the sandbox and return literal ``True`` only on confirmation."""

    async def is_allocated(self, workflow_id: str) -> bool:
        """Return whether the workflow currently owns a sandbox allocation."""

    async def restore(
        self,
        workflow_id: str,
        checkpoint: CheckpointT,
        *,
        reason: "ResumeReason",
    ) -> None:
        """Restore a sandbox from the checkpoint on either resume path."""


class DurableOperationBroker(Protocol[RequestT, ResultT]):
    """Submit an external operation and retrieve its result by stable ID."""

    async def submit(self, operation_id: str, request: RequestT) -> None:
        """Idempotently submit an external operation."""

    async def result(self, operation_id: str) -> ResultT:
        """Wait for a result that remains retrievable without the sandbox."""


class ResumeReason(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


MarkerFactory = Callable[[str], contextlib.AbstractContextManager[None]]


class DurableWorkflowRunner(Generic[CheckpointT, RequestT, ResultT]):
    """Coordinate one reclaimable wait from a supervisor outside the sandbox.

    This runner enforces the integration boundary but is not itself a durable
    workflow engine. Production callers still need durable replay around it.
    """

    def __init__(
        self,
        *,
        checkpoints: CheckpointAdapter[CheckpointT],
        runtime: SandboxRuntime[CheckpointT],
        broker: DurableOperationBroker[RequestT, ResultT],
        marker: MarkerFactory = wait,
    ) -> None:
        self._checkpoints = checkpoints
        self._runtime = runtime
        self._broker = broker
        self._marker = marker

    async def run(
        self,
        wait_type: str,
        *,
        workflow_id: str,
        operation_id: str,
        request: RequestT,
        timeout_seconds: float,
    ) -> ResultT:
        """Release, wait durably, and restore on success, failure, or timeout."""

        _validate_inputs(wait_type, workflow_id, operation_id, timeout_seconds)
        checkpoint = await self._checkpoints.checkpoint(workflow_id)
        if await self._runtime.release(workflow_id, checkpoint) is not True:
            raise ReclaimabilityError("runtime did not confirm sandbox release")

        reason = ResumeReason.ERROR
        try:
            if await self._runtime.is_allocated(workflow_id):
                raise ReclaimabilityError(
                    "runtime reported a sandbox allocation after release"
                )

            with self._marker(wait_type):
                await self._broker.submit(operation_id, request)
                result = await asyncio.wait_for(
                    self._broker.result(operation_id),
                    timeout=timeout_seconds,
                )
            reason = ResumeReason.SUCCESS
            return result
        finally:
            await self._runtime.restore(
                workflow_id,
                checkpoint,
                reason=reason,
            )


def _validate_inputs(
    wait_type: str,
    workflow_id: str,
    operation_id: str,
    timeout_seconds: float,
) -> None:
    if wait_type not in {"model", "tool", "human"}:
        raise ValueError("wait_type must be model, tool, or human")
    if not workflow_id:
        raise ValueError("workflow_id must not be empty")
    if not operation_id:
        raise ValueError("operation_id must not be empty")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")


@dataclass(frozen=True)
class InMemoryCheckpoint:
    workflow_id: str
    sequence: int


class InMemoryCheckpointAdapter:
    """Content-free checkpoint adapter for local integration proofs only."""

    def __init__(self) -> None:
        self._sequences: dict[str, int] = {}

    async def checkpoint(self, workflow_id: str) -> InMemoryCheckpoint:
        sequence = self._sequences.get(workflow_id, 0) + 1
        self._sequences[workflow_id] = sequence
        return InMemoryCheckpoint(workflow_id=workflow_id, sequence=sequence)


class InMemorySandboxRuntime:
    """Allocation-state fake for proving lifecycle ordering locally."""

    def __init__(self, *workflow_ids: str) -> None:
        self._allocated = set(workflow_ids)
        self.history: list[tuple[str, str]] = []

    async def release(
        self,
        workflow_id: str,
        checkpoint: InMemoryCheckpoint,
    ) -> bool:
        if (
            checkpoint.workflow_id != workflow_id
            or workflow_id not in self._allocated
        ):
            return False
        self._allocated.remove(workflow_id)
        self.history.append(("released", workflow_id))
        return True

    async def is_allocated(self, workflow_id: str) -> bool:
        return workflow_id in self._allocated

    @property
    def allocated_workflows(self) -> frozenset[str]:
        """Return a read-only allocation snapshot for local proof assertions."""

        return frozenset(self._allocated)

    async def restore(
        self,
        workflow_id: str,
        checkpoint: InMemoryCheckpoint,
        *,
        reason: ResumeReason,
    ) -> None:
        if checkpoint.workflow_id != workflow_id:
            raise ValueError("checkpoint does not belong to workflow")
        self._allocated.add(workflow_id)
        self.history.append((f"restored:{reason.value}", workflow_id))


@dataclass
class _Operation(Generic[ResultT]):
    future: asyncio.Future[ResultT]


class InMemoryDurableBroker(Generic[RequestT, ResultT]):
    """Idempotent callback broker for local integration proofs only."""

    def __init__(self) -> None:
        self._operations: dict[str, _Operation[ResultT]] = {}
        self.submission_count: dict[str, int] = {}

    async def submit(self, operation_id: str, request: RequestT) -> None:
        del request
        self.submission_count[operation_id] = (
            self.submission_count.get(operation_id, 0) + 1
        )
        if operation_id not in self._operations:
            self._operations[operation_id] = _Operation(
                future=asyncio.get_running_loop().create_future()
            )

    async def result(self, operation_id: str) -> ResultT:
        operation = self._operations.get(operation_id)
        if operation is None:
            raise KeyError("operation was not submitted")
        return await asyncio.shield(operation.future)

    async def complete(self, operation_id: str, result: ResultT) -> bool:
        """Complete once; return ``False`` for a duplicate callback."""

        operation = self._operations.get(operation_id)
        if operation is None:
            raise KeyError("operation was not submitted")
        if operation.future.done():
            return False
        operation.future.set_result(result)
        return True

    async def fail(self, operation_id: str, error: Exception) -> bool:
        """Fail once; return ``False`` for a duplicate callback."""

        operation = self._operations.get(operation_id)
        if operation is None:
            raise KeyError("operation was not submitted")
        if operation.future.done():
            return False
        operation.future.set_exception(error)
        return True
