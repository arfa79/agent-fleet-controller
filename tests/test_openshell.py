from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, replace

import pytest

from agent_fleet_controller.openshell import (
    OpenShellBinding,
    OpenShellCheckpoint,
    OpenShellCheckpointAdapter,
    OpenShellPhase,
    OpenShellRuntimeError,
    OpenShellSandboxRuntime,
)
from agent_fleet_controller.workflow import (
    DurableWorkflowRunner,
    InMemoryCheckpointAdapter,
    InMemoryDurableBroker,
    ResumeReason,
)


@dataclass(frozen=True)
class _Sandbox:
    id: str
    name: str
    workspace: str
    phase: int


class _Client:
    def __init__(self, sandbox: _Sandbox) -> None:
        self.sandbox = sandbox
        self.calls = []
        self.failure: Exception | None = None

    def _call(self, operation: str, name: str, workspace: str) -> _Sandbox:
        self.calls.append((operation, name, workspace))
        if self.failure is not None:
            raise self.failure
        return self.sandbox

    def get(self, name: str, *, workspace: str) -> _Sandbox:
        return self._call("get", name, workspace)

    def stop(self, name: str, *, workspace: str) -> _Sandbox:
        self._call("stop", name, workspace)
        self.sandbox = replace(self.sandbox, phase=OpenShellPhase.STOPPING)
        return self.sandbox

    def wait_stopped(
        self,
        name: str,
        *,
        workspace: str,
        timeout_seconds: float,
    ) -> _Sandbox:
        assert timeout_seconds == 30
        self._call("wait_stopped", name, workspace)
        self.sandbox = replace(self.sandbox, phase=OpenShellPhase.STOPPED)
        return self.sandbox

    def start(self, name: str, *, workspace: str) -> _Sandbox:
        self._call("start", name, workspace)
        self.sandbox = replace(self.sandbox, phase=OpenShellPhase.STARTING)
        return self.sandbox

    def wait_ready(
        self,
        name: str,
        *,
        workspace: str,
        timeout_seconds: float,
    ) -> _Sandbox:
        assert timeout_seconds == 30
        self._call("wait_ready", name, workspace)
        self.sandbox = replace(self.sandbox, phase=OpenShellPhase.READY)
        return self.sandbox


class _Restorer:
    def __init__(self) -> None:
        self.calls = []
        self.failure: Exception | None = None

    async def restore(
        self,
        workflow_id: str,
        binding: OpenShellBinding,
        checkpoint,
        *,
        reason: ResumeReason,
    ) -> None:
        self.calls.append((workflow_id, binding, checkpoint, reason))
        if self.failure is not None:
            raise self.failure


def _sandbox(phase: int = OpenShellPhase.READY) -> _Sandbox:
    return _Sandbox(
        id="sandbox-id-1",
        name="agent-sandbox",
        workspace="research",
        phase=phase,
    )


def _binding() -> dict[str, OpenShellBinding]:
    return {
        "workflow-1": OpenShellBinding(
            name="agent-sandbox",
            workspace="research",
        )
    }


def test_runner_stops_compute_during_wait_and_starts_new_generation() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox())
        bindings = _binding()
        checkpoints = OpenShellCheckpointAdapter(
            checkpoints=InMemoryCheckpointAdapter(),
            client=client,
            bindings=bindings,
        )
        restorer = _Restorer()
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=bindings,
            application_restorer=restorer,
            transition_timeout_seconds=30,
        )
        broker: InMemoryDurableBroker[str, str] = InMemoryDurableBroker()

        @contextlib.contextmanager
        def marker(wait_type: str):
            assert wait_type == "model"
            assert client.sandbox.phase == OpenShellPhase.STOPPED
            yield
            assert client.sandbox.phase == OpenShellPhase.STOPPED

        runner = DurableWorkflowRunner(
            checkpoints=checkpoints,
            runtime=runtime,
            broker=broker,
            marker=marker,
        )
        task = asyncio.create_task(
            runner.run(
                "model",
                workflow_id="workflow-1",
                operation_id="operation-1",
                request="sanitized-request",
                timeout_seconds=1,
            )
        )
        while "operation-1" not in broker.submission_count:
            await asyncio.sleep(0)

        assert client.sandbox.phase == OpenShellPhase.STOPPED
        assert await broker.complete("operation-1", "result")
        assert await task == "result"
        assert client.sandbox.phase == OpenShellPhase.READY
        assert len(restorer.calls) == 1
        assert restorer.calls[0][0] == "workflow-1"
        assert restorer.calls[0][2].workflow_id == "workflow-1"
        assert restorer.calls[0][3] == ResumeReason.SUCCESS
        assert [call[0] for call in client.calls] == [
            "get",
            "get",
            "stop",
            "wait_stopped",
            "get",
            "get",
            "start",
            "wait_ready",
        ]

    asyncio.run(exercise())


def test_release_is_idempotent_for_confirmed_stopped_sandbox() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox(OpenShellPhase.STOPPED))
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=_Restorer(),
            transition_timeout_seconds=30,
        )
        checkpoint = OpenShellCheckpoint(
            workflow_id="workflow-1",
            sandbox_id="sandbox-id-1",
            sandbox_name="agent-sandbox",
            workspace="research",
            application="checkpoint-ref",
        )
        assert await runtime.release("workflow-1", checkpoint) is True
        assert [call[0] for call in client.calls] == ["get", "wait_stopped"]

    asyncio.run(exercise())


def test_release_recovers_after_stop_request_was_already_accepted() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox(OpenShellPhase.STOPPING))
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=_Restorer(),
            transition_timeout_seconds=30,
        )
        checkpoint = OpenShellCheckpoint(
            workflow_id="workflow-1",
            sandbox_id="sandbox-id-1",
            sandbox_name="agent-sandbox",
            workspace="research",
            application="checkpoint-ref",
        )
        assert await runtime.release("workflow-1", checkpoint) is True
        assert [call[0] for call in client.calls] == ["get", "wait_stopped"]

    asyncio.run(exercise())


def test_identity_change_fails_closed_before_stop() -> None:
    async def exercise() -> None:
        client = _Client(replace(_sandbox(), id="replacement-id"))
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=_Restorer(),
        )
        checkpoint = OpenShellCheckpoint(
            workflow_id="workflow-1",
            sandbox_id="sandbox-id-1",
            sandbox_name="agent-sandbox",
            workspace="research",
            application=None,
        )
        with pytest.raises(OpenShellRuntimeError, match="identity changed"):
            await runtime.release("workflow-1", checkpoint)
        assert [call[0] for call in client.calls] == ["get"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("phase", "allocated"),
    [
        (OpenShellPhase.READY, True),
        (OpenShellPhase.ERROR, True),
        (OpenShellPhase.STOPPING, True),
        (OpenShellPhase.STOPPED, False),
        (OpenShellPhase.STARTING, True),
    ],
)
def test_allocation_is_absent_only_after_stopped_confirmation(
    phase: OpenShellPhase, allocated: bool
) -> None:
    async def exercise() -> None:
        runtime = OpenShellSandboxRuntime(
            client=_Client(_sandbox(phase)),
            bindings=_binding(),
            application_restorer=_Restorer(),
        )
        assert await runtime.is_allocated("workflow-1") is allocated

    asyncio.run(exercise())


def test_client_error_is_redacted_at_boundary() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox())
        client.failure = RuntimeError("secret endpoint and credential")
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=_Restorer(),
        )
        with pytest.raises(OpenShellRuntimeError) as captured:
            await runtime.is_allocated("workflow-1")
        assert "secret" not in str(captured.value)
        assert captured.value.__cause__ is None

    asyncio.run(exercise())


def test_restore_rejects_non_restartable_phase() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox(OpenShellPhase.ERROR))
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=_Restorer(),
        )
        checkpoint = OpenShellCheckpoint(
            workflow_id="workflow-1",
            sandbox_id="sandbox-id-1",
            sandbox_name="agent-sandbox",
            workspace="research",
            application=None,
        )
        with pytest.raises(OpenShellRuntimeError, match="not restartable"):
            await runtime.restore(
                "workflow-1",
                checkpoint,
                reason=ResumeReason.ERROR,
            )

    asyncio.run(exercise())


def test_restore_accepts_temporal_json_checkpoint_and_restores_application() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox(OpenShellPhase.STOPPED))
        restorer = _Restorer()
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=restorer,
            transition_timeout_seconds=30,
        )
        await runtime.restore(
            "workflow-1",
            {
                "workflow_id": "workflow-1",
                "sandbox_id": "sandbox-id-1",
                "sandbox_name": "agent-sandbox",
                "workspace": "research",
                "application": {"opaque": "checkpoint-ref"},
            },
            reason=ResumeReason.SUCCESS,
        )
        assert restorer.calls == [
            (
                "workflow-1",
                _binding()["workflow-1"],
                {"opaque": "checkpoint-ref"},
                ResumeReason.SUCCESS,
            )
        ]
        assert client.sandbox.phase == OpenShellPhase.READY

    asyncio.run(exercise())


def test_restore_recovers_after_start_request_was_already_accepted() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox(OpenShellPhase.STARTING))
        restorer = _Restorer()
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=restorer,
            transition_timeout_seconds=30,
        )
        checkpoint = OpenShellCheckpoint(
            workflow_id="workflow-1",
            sandbox_id="sandbox-id-1",
            sandbox_name="agent-sandbox",
            workspace="research",
            application="checkpoint-ref",
        )
        await runtime.restore(
            "workflow-1",
            checkpoint,
            reason=ResumeReason.SUCCESS,
        )
        assert [call[0] for call in client.calls] == ["get", "wait_ready"]
        assert len(restorer.calls) == 1

    asyncio.run(exercise())


def test_application_restore_failure_rolls_compute_back_to_stopped() -> None:
    async def exercise() -> None:
        client = _Client(_sandbox(OpenShellPhase.STOPPED))
        restorer = _Restorer()
        restorer.failure = RuntimeError("sensitive checkpoint error")
        runtime = OpenShellSandboxRuntime(
            client=client,
            bindings=_binding(),
            application_restorer=restorer,
            transition_timeout_seconds=30,
        )
        checkpoint = OpenShellCheckpoint(
            workflow_id="workflow-1",
            sandbox_id="sandbox-id-1",
            sandbox_name="agent-sandbox",
            workspace="research",
            application="checkpoint-ref",
        )
        with pytest.raises(OpenShellRuntimeError) as captured:
            await runtime.restore(
                "workflow-1",
                checkpoint,
                reason=ResumeReason.ERROR,
            )
        assert "sensitive" not in str(captured.value)
        assert client.sandbox.phase == OpenShellPhase.STOPPED
        assert [call[0] for call in client.calls] == [
            "get",
            "start",
            "wait_ready",
            "stop",
            "wait_stopped",
        ]

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_transition_timeout(value: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        OpenShellSandboxRuntime(
            client=_Client(_sandbox()),
            bindings=_binding(),
            application_restorer=_Restorer(),
            transition_timeout_seconds=value,
        )
