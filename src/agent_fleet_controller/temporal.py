from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from .model import EventKind, WAIT_PAIRS
from .workflow import (
    CheckpointAdapter,
    DurableOperationBroker,
    ResumeReason,
    SandboxRuntime,
)


@dataclass(frozen=True)
class TemporalWaitRequest:
    """Serializable input for one reclaimable Temporal workflow."""

    wait_type: str
    workflow_id: str
    operation_id: str
    request: Any
    timeout_seconds: float
    activity_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class TemporalCompletion:
    """Content returned through a durable callback signal."""

    result: Any = None
    error: str | None = None


@dataclass(frozen=True)
class _WorkflowRef:
    workflow_id: str


@dataclass(frozen=True)
class _CheckpointRef:
    workflow_id: str
    checkpoint: Any


@dataclass(frozen=True)
class _RestoreRef:
    workflow_id: str
    checkpoint: Any
    reason: ResumeReason


@dataclass(frozen=True)
class _Submission:
    operation_id: str
    request: Any


@dataclass(frozen=True)
class _Marker:
    marker_id: str
    kind: EventKind


class TemporalActivities:
    """Temporal activity bridge for AFC's existing adapter contracts.

    Register ``activities`` on a worker. The callback receiver is intentionally
    outside this class: it should signal ``TemporalReclaimableWait.complete``
    by the stable Temporal workflow ID after authenticating the provider event.
    """

    def __init__(
        self,
        *,
        checkpoints: CheckpointAdapter[Any],
        runtime: SandboxRuntime[Any],
        broker: DurableOperationBroker[Any, Any],
        marker_emit: Callable[[str, EventKind], None],
    ) -> None:
        self._checkpoints = checkpoints
        self._runtime = runtime
        self._broker = broker
        self._marker_emit = marker_emit

    @property
    def activities(self) -> list[Callable[..., Any]]:
        return [
            self.checkpoint,
            self.release,
            self.is_allocated,
            self.restore,
            self.mark,
            self.submit,
        ]

    @activity.defn(name="afc_checkpoint")
    async def checkpoint(self, ref: _WorkflowRef) -> Any:
        return await self._checkpoints.checkpoint(ref.workflow_id)

    @activity.defn(name="afc_release")
    async def release(self, ref: _CheckpointRef) -> bool:
        return await self._runtime.release(ref.workflow_id, ref.checkpoint)

    @activity.defn(name="afc_is_allocated")
    async def is_allocated(self, ref: _WorkflowRef) -> bool:
        return await self._runtime.is_allocated(ref.workflow_id)

    @activity.defn(name="afc_restore")
    async def restore(self, ref: _RestoreRef) -> None:
        await self._runtime.restore(
            ref.workflow_id,
            ref.checkpoint,
            reason=ref.reason,
        )

    @activity.defn(name="afc_mark")
    async def mark(self, marker: _Marker) -> None:
        # Temporal activities are at-least-once. The sink must deduplicate this
        # stable ID before it emits to AFC or another lifecycle event store.
        self._marker_emit(marker.marker_id, marker.kind)

    @activity.defn(name="afc_submit")
    async def submit(self, submission: _Submission) -> None:
        await self._broker.submit(submission.operation_id, submission.request)


@workflow.defn(name="afc-reclaimable-wait")
class TemporalReclaimableWait:
    """Replay-safe workflow for one externally completed sandbox wait."""

    def __init__(self) -> None:
        self._completion: TemporalCompletion | None = None

    @workflow.signal
    def complete(self, completion: TemporalCompletion) -> None:
        """Accept the first callback and ignore duplicate deliveries."""

        if self._completion is None:
            self._completion = completion

    @workflow.run
    async def run(self, request: TemporalWaitRequest) -> Any:
        try:
            _validate_request(request)
        except ValueError as exc:
            raise ApplicationError(
                str(exc),
                type="AFC_INVALID_REQUEST",
                non_retryable=True,
            ) from exc
        activity_timeout = timedelta(seconds=request.activity_timeout_seconds)
        retry_policy = RetryPolicy(maximum_attempts=3)

        async def execute(name: str, arg: Any, *, result_type: type | None = None):
            return await workflow.execute_activity(
                name,
                arg,
                result_type=result_type,
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )

        workflow_ref = _WorkflowRef(request.workflow_id)
        checkpoint = await execute("afc_checkpoint", workflow_ref)
        released = await execute(
            "afc_release",
            _CheckpointRef(request.workflow_id, checkpoint),
            result_type=bool,
        )
        if released is not True:
            raise ApplicationError(
                "runtime did not confirm sandbox release",
                type="AFC_RECLAIMABILITY",
                non_retryable=True,
            )

        marker_started = False

        async def restore(reason: ResumeReason) -> None:
            try:
                if marker_started:
                    await execute(
                        "afc_mark",
                        _Marker(
                            f"{request.operation_id}:finished",
                            WAIT_PAIRS[started],
                        ),
                    )
            finally:
                await execute(
                    "afc_restore",
                    _RestoreRef(request.workflow_id, checkpoint, reason),
                )

        try:
            if await execute("afc_is_allocated", workflow_ref, result_type=bool):
                raise ApplicationError(
                    "runtime reported a sandbox allocation after release",
                    type="AFC_RECLAIMABILITY",
                    non_retryable=True,
                )

            started = EventKind(f"{request.wait_type}_wait_started")
            await execute(
                "afc_mark",
                _Marker(f"{request.operation_id}:started", started),
            )
            marker_started = True
            await execute(
                "afc_submit",
                _Submission(request.operation_id, request.request),
            )
            try:
                await workflow.wait_condition(
                    lambda: self._completion is not None,
                    timeout=timedelta(seconds=request.timeout_seconds),
                )
            except TimeoutError as exc:
                raise ApplicationError(
                    "external operation timed out",
                    type="AFC_EXTERNAL_OPERATION_TIMEOUT",
                    non_retryable=True,
                ) from exc
            completion = self._completion
            if completion is None:
                raise RuntimeError("completion signal was not retained")
            if completion.error is not None:
                raise ApplicationError(
                    "external operation failed",
                    completion.error,
                    type="AFC_EXTERNAL_OPERATION_FAILED",
                    non_retryable=True,
                )
        except Exception:
            await restore(ResumeReason.ERROR)
            raise
        else:
            result = completion.result
            await restore(ResumeReason.SUCCESS)
            return result


def _validate_request(request: TemporalWaitRequest) -> None:
    if request.wait_type not in {"model", "tool", "human"}:
        raise ValueError("wait_type must be model, tool, or human")
    if not request.workflow_id:
        raise ValueError("workflow_id must not be empty")
    if not request.operation_id:
        raise ValueError("operation_id must not be empty")
    if not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if (
        not math.isfinite(request.activity_timeout_seconds)
        or request.activity_timeout_seconds <= 0
    ):
        raise ValueError("activity_timeout_seconds must be greater than zero")
