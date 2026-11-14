from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytest.importorskip("temporalio")

from temporalio.client import Client, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio.worker import workflow_sandbox

from agent_fleet_controller.temporal import (
    TemporalActivities,
    TemporalCompletion,
    TemporalReclaimableWait,
    TemporalWaitRequest,
)
from agent_fleet_controller.workflow import (
    InMemoryCheckpointAdapter,
    InMemoryDurableBroker,
    InMemorySandboxRuntime,
    ResumeReason,
)


class _StringCheckpointAdapter:
    async def checkpoint(self, workflow_id: str) -> str:
        return f"checkpoint:{workflow_id}"


class _StringCheckpointRuntime:
    def __init__(self, workflow_id: str) -> None:
        self.allocated = {workflow_id}
        self.history = []

    async def release(self, workflow_id: str, checkpoint: str) -> bool:
        if (
            checkpoint != f"checkpoint:{workflow_id}"
            or workflow_id not in self.allocated
        ):
            return False
        self.allocated.remove(workflow_id)
        self.history.append(("released", workflow_id))
        return True

    async def is_allocated(self, workflow_id: str) -> bool:
        return workflow_id in self.allocated

    async def restore(
        self,
        workflow_id: str,
        checkpoint: str,
        *,
        reason: ResumeReason,
    ) -> None:
        assert checkpoint == f"checkpoint:{workflow_id}"
        self.allocated.add(workflow_id)
        self.history.append((f"restored:{reason.value}", workflow_id))


async def _wait_until_submitted(
    broker: InMemoryDurableBroker[str, str], operation_id: str
) -> None:
    for _ in range(1_000):
        if operation_id in broker.submission_count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("operation was not submitted")


async def _start_environment() -> WorkflowEnvironment:
    address = os.environ.get("AFC_TEMPORAL_ADDRESS")
    if address:
        return WorkflowEnvironment.from_client(await Client.connect(address))
    return await WorkflowEnvironment.start_time_skipping()


def test_workflow_replays_after_worker_restart_and_ignores_duplicate_callback() -> None:
    async def exercise() -> None:
        workflow_id = f"workflow-{uuid.uuid4()}"
        operation_id = f"operation-{uuid.uuid4()}"
        task_queue = f"afc-{uuid.uuid4()}"
        runtime = _StringCheckpointRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        markers = {}
        activities = TemporalActivities(
            checkpoints=_StringCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker_emit=lambda marker_id, kind: markers.setdefault(marker_id, kind),
        )

        async with await _start_environment() as env:
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[TemporalReclaimableWait],
                activities=activities.activities,
            ):
                handle = await env.client.start_workflow(
                    TemporalReclaimableWait.run,
                    TemporalWaitRequest(
                        wait_type="model",
                        workflow_id=workflow_id,
                        operation_id=operation_id,
                        request="sanitized-request",
                        timeout_seconds=60,
                    ),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                await _wait_until_submitted(broker, operation_id)
                assert not await runtime.is_allocated(workflow_id)

            # The callback is retained in Temporal history while no worker is
            # running. The first signal wins if the provider retries delivery.
            await handle.signal(
                TemporalReclaimableWait.complete,
                TemporalCompletion(result="first-result"),
            )
            await handle.signal(
                TemporalReclaimableWait.complete,
                TemporalCompletion(result="duplicate-result"),
            )

            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[TemporalReclaimableWait],
                activities=activities.activities,
            ):
                assert await handle.result() == "first-result"

        assert await runtime.is_allocated(workflow_id)
        assert runtime.history == [
            ("released", workflow_id),
            ("restored:success", workflow_id),
        ]
        assert [marker.value for marker in markers.values()] == [
            "model_wait_started",
            "model_wait_finished",
        ]
        assert broker.submission_count[operation_id] == 1

    asyncio.run(exercise())


def test_workflow_timeout_finishes_marker_and_restores_error_path() -> None:
    async def exercise() -> None:
        workflow_id = f"workflow-{uuid.uuid4()}"
        operation_id = f"operation-{uuid.uuid4()}"
        task_queue = f"afc-{uuid.uuid4()}"
        runtime = _StringCheckpointRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        markers = {}
        activities = TemporalActivities(
            checkpoints=_StringCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker_emit=lambda marker_id, kind: markers.setdefault(marker_id, kind),
        )

        async with await _start_environment() as env:
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[TemporalReclaimableWait],
                activities=activities.activities,
            ):
                with pytest.raises(WorkflowFailureError):
                    await env.client.execute_workflow(
                        TemporalReclaimableWait.run,
                        TemporalWaitRequest(
                            wait_type="tool",
                            workflow_id=workflow_id,
                            operation_id=operation_id,
                            request="sanitized-request",
                            timeout_seconds=1,
                        ),
                        id=workflow_id,
                        task_queue=task_queue,
                    )

        assert await runtime.is_allocated(workflow_id)
        assert runtime.history[-1] == ("restored:error", workflow_id)
        assert [marker.value for marker in markers.values()] == [
            "tool_wait_started",
            "tool_wait_finished",
        ]

    asyncio.run(exercise())


def test_provider_error_signal_restores_error_path() -> None:
    async def exercise() -> None:
        workflow_id = f"workflow-{uuid.uuid4()}"
        operation_id = f"operation-{uuid.uuid4()}"
        task_queue = f"afc-{uuid.uuid4()}"
        runtime = _StringCheckpointRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        markers = {}
        activities = TemporalActivities(
            checkpoints=_StringCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker_emit=lambda marker_id, kind: markers.setdefault(marker_id, kind),
        )

        async with await _start_environment() as env:
            async with Worker(
                env.client,
                task_queue=task_queue,
                workflows=[TemporalReclaimableWait],
                activities=activities.activities,
            ):
                handle = await env.client.start_workflow(
                    TemporalReclaimableWait.run,
                    TemporalWaitRequest(
                        wait_type="model",
                        workflow_id=workflow_id,
                        operation_id=operation_id,
                        request="sanitized-request",
                        timeout_seconds=60,
                    ),
                    id=workflow_id,
                    task_queue=task_queue,
                )
                await _wait_until_submitted(broker, operation_id)
                await handle.signal(
                    TemporalReclaimableWait.complete,
                    TemporalCompletion(error="sanitized provider failure"),
                )
                with pytest.raises(WorkflowFailureError):
                    await handle.result()

        assert await runtime.is_allocated(workflow_id)
        assert runtime.history[-1] == ("restored:error", workflow_id)
        assert [marker.value for marker in markers.values()] == [
            "model_wait_started",
            "model_wait_finished",
        ]

    asyncio.run(exercise())


if os.environ.get("AFC_TEMPORAL_INTEGRATION") != "1":
    test_workflow_replays_after_worker_restart_and_ignores_duplicate_callback = (
        pytest.mark.skip(reason="set AFC_TEMPORAL_INTEGRATION=1 to start Temporal")(
            test_workflow_replays_after_worker_restart_and_ignores_duplicate_callback
        )
    )
    test_workflow_timeout_finishes_marker_and_restores_error_path = pytest.mark.skip(
        reason="set AFC_TEMPORAL_INTEGRATION=1 to start Temporal"
    )(test_workflow_timeout_finishes_marker_and_restores_error_path)
    test_provider_error_signal_restores_error_path = pytest.mark.skip(
        reason="set AFC_TEMPORAL_INTEGRATION=1 to start Temporal"
    )(test_provider_error_signal_restores_error_path)


def test_workflow_loads_in_temporal_deterministic_sandbox() -> None:
    async def exercise() -> None:
        runner = workflow_sandbox.SandboxedWorkflowRunner()
        runner.prepare_workflow(
            TemporalReclaimableWait.__temporal_workflow_definition
        )

    asyncio.run(exercise())


def test_temporal_activities_preserve_adapter_contracts_and_marker_ids() -> None:
    async def exercise() -> None:
        workflow_id = "workflow-unit"
        runtime = InMemorySandboxRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        marker_calls = []
        bridge = TemporalActivities(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker_emit=lambda marker_id, kind: marker_calls.append(
                (marker_id, kind.value)
            ),
        )

        checkpoint = await bridge.checkpoint(_WorkflowRef(workflow_id))
        assert await bridge.release(_CheckpointRef(workflow_id, checkpoint)) is True
        assert await bridge.is_allocated(_WorkflowRef(workflow_id)) is False
        await bridge.mark(_Marker("operation:started", EventKind.MODEL_WAIT_STARTED))
        await bridge.submit(_Submission("operation", "sanitized-request"))
        await bridge.restore(
            _RestoreRef(workflow_id, checkpoint, ResumeReason.SUCCESS)
        )

        assert marker_calls == [("operation:started", "model_wait_started")]
        assert broker.submission_count == {"operation": 1}
        assert await runtime.is_allocated(workflow_id)

    from agent_fleet_controller.model import EventKind
    from agent_fleet_controller.temporal import (
        _CheckpointRef,
        _Marker,
        _RestoreRef,
        _Submission,
        _WorkflowRef,
    )
    asyncio.run(exercise())
