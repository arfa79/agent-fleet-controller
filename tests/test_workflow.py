from __future__ import annotations

import asyncio
import contextlib

import pytest

from agent_fleet_controller.durable import ReclaimabilityError
from agent_fleet_controller.workflow import (
    DurableWorkflowRunner,
    InMemoryCheckpointAdapter,
    InMemoryDurableBroker,
    InMemorySandboxRuntime,
)


def test_local_proof_releases_during_wait_and_restores_after_callback() -> None:
    async def exercise() -> None:
        workflow_id = "workflow-1"
        operation_id = "operation-1"
        runtime = InMemorySandboxRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        marker_events: list[str] = []

        @contextlib.contextmanager
        def marker(wait_type: str):
            assert workflow_id not in runtime.allocated_workflows
            marker_events.append(f"{wait_type}:started")
            try:
                yield
            finally:
                assert workflow_id not in runtime.allocated_workflows
                marker_events.append(f"{wait_type}:finished")

        runner = DurableWorkflowRunner(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker=marker,
        )
        task = asyncio.create_task(
            runner.run(
                "model",
                workflow_id=workflow_id,
                operation_id=operation_id,
                request="sanitized-request",
                timeout_seconds=1,
            )
        )
        while operation_id not in broker.submission_count:
            await asyncio.sleep(0)

        assert not await runtime.is_allocated(workflow_id)
        assert await broker.complete(operation_id, "first-result") is True
        assert await broker.complete(operation_id, "duplicate-result") is False
        assert await task == "first-result"
        assert await runtime.is_allocated(workflow_id)
        assert marker_events == ["model:started", "model:finished"]
        assert runtime.history == [
            ("released", workflow_id),
            ("restored:success", workflow_id),
        ]

    asyncio.run(exercise())


def test_timeout_restores_on_error_path() -> None:
    async def exercise() -> None:
        workflow_id = "workflow-timeout"
        runtime = InMemorySandboxRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()

        @contextlib.contextmanager
        def marker(wait_type: str):
            yield

        runner = DurableWorkflowRunner(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker=marker,
        )
        with pytest.raises(asyncio.TimeoutError):
            await runner.run(
                "tool",
                workflow_id=workflow_id,
                operation_id="operation-timeout",
                request="sanitized-request",
                timeout_seconds=0.001,
            )

        assert await runtime.is_allocated(workflow_id)
        assert runtime.history[-1] == ("restored:error", workflow_id)

    asyncio.run(exercise())


def test_broker_failure_restores_on_error_path() -> None:
    async def exercise() -> None:
        workflow_id = "workflow-failure"
        operation_id = "operation-failure"
        runtime = InMemorySandboxRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()

        @contextlib.contextmanager
        def marker(wait_type: str):
            yield

        runner = DurableWorkflowRunner(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker=marker,
        )
        task = asyncio.create_task(
            runner.run(
                "model",
                workflow_id=workflow_id,
                operation_id=operation_id,
                request="sanitized-request",
                timeout_seconds=1,
            )
        )
        while operation_id not in broker.submission_count:
            await asyncio.sleep(0)
        assert await broker.fail(operation_id, RuntimeError("provider failed"))

        with pytest.raises(RuntimeError, match="provider failed"):
            await task
        assert await runtime.is_allocated(workflow_id)
        assert runtime.history[-1] == ("restored:error", workflow_id)

    asyncio.run(exercise())


def test_unconfirmed_release_does_not_start_operation() -> None:
    async def exercise() -> None:
        runtime = InMemorySandboxRuntime()
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        marker_called = False

        @contextlib.contextmanager
        def marker(wait_type: str):
            nonlocal marker_called
            marker_called = True
            yield

        runner = DurableWorkflowRunner(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker=marker,
        )
        with pytest.raises(ReclaimabilityError, match="did not confirm"):
            await runner.run(
                "human",
                workflow_id="not-enrolled",
                operation_id="operation-never-started",
                request="sanitized-request",
                timeout_seconds=1,
            )

        assert marker_called is False
        assert broker.submission_count == {}
        assert runtime.history == []

    asyncio.run(exercise())


def test_allocation_check_rejects_inconsistent_release_confirmation() -> None:
    class InconsistentRuntime(InMemorySandboxRuntime):
        async def release(self, workflow_id, checkpoint) -> bool:
            self.history.append(("release-claimed", workflow_id))
            return True

    async def exercise() -> None:
        workflow_id = "still-allocated"
        runtime = InconsistentRuntime(workflow_id)
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()
        marker_called = False

        @contextlib.contextmanager
        def marker(wait_type: str):
            nonlocal marker_called
            marker_called = True
            yield

        runner = DurableWorkflowRunner(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=broker,
            marker=marker,
        )
        with pytest.raises(ReclaimabilityError, match="after release"):
            await runner.run(
                "model",
                workflow_id=workflow_id,
                operation_id="operation-never-started",
                request="sanitized-request",
                timeout_seconds=1,
            )

        assert marker_called is False
        assert broker.submission_count == {}
        assert runtime.history[-1] == ("restored:error", workflow_id)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("wait_type", "workflow_id", "operation_id", "timeout_seconds"),
    [
        ("network", "workflow", "operation", 1),
        ("model", "", "operation", 1),
        ("model", "workflow", "", 1),
        ("model", "workflow", "operation", 0),
        ("model", "workflow", "operation", float("nan")),
    ],
)
def test_invalid_inputs_have_no_side_effects(
    wait_type: str,
    workflow_id: str,
    operation_id: str,
    timeout_seconds: float,
) -> None:
    async def exercise() -> None:
        runtime = InMemorySandboxRuntime("workflow")
        runner = DurableWorkflowRunner(
            checkpoints=InMemoryCheckpointAdapter(),
            runtime=runtime,
            broker=InMemoryDurableBroker(),
        )
        with pytest.raises(ValueError):
            await runner.run(
                wait_type,
                workflow_id=workflow_id,
                operation_id=operation_id,
                request=None,
                timeout_seconds=timeout_seconds,
            )
        assert runtime.history == []

    asyncio.run(exercise())
