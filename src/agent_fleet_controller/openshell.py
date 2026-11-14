from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Generic, Mapping, Protocol, TypeVar

from .workflow import CheckpointAdapter, ResumeReason


StateT = TypeVar("StateT")


class OpenShellPhase(IntEnum):
    """Stable OpenShell sandbox phase values introduced before v0.1.7."""

    READY = 2
    ERROR = 3
    STOPPING = 6
    STOPPED = 7
    STARTING = 8


class OpenShellRuntimeError(RuntimeError):
    """Raised when OpenShell cannot prove the requested lifecycle state."""


class OpenShellSandboxRef(Protocol):
    id: str
    name: str
    workspace: str

    @property
    def phase(self) -> int: ...


class OpenShellClient(Protocol):
    """Subset of NVIDIA OpenShell's synchronous Python SandboxClient."""

    def get(self, name: str, *, workspace: str) -> OpenShellSandboxRef: ...

    def stop(self, name: str, *, workspace: str) -> OpenShellSandboxRef: ...

    def start(self, name: str, *, workspace: str) -> OpenShellSandboxRef: ...

    def wait_stopped(
        self,
        name: str,
        *,
        workspace: str,
        timeout_seconds: float,
    ) -> OpenShellSandboxRef: ...

    def wait_ready(
        self,
        name: str,
        *,
        workspace: str,
        timeout_seconds: float,
    ) -> OpenShellSandboxRef: ...


class OpenShellApplicationRestorer(Protocol[StateT]):
    """Restore durable agent state after OpenShell starts new compute."""

    async def restore(
        self,
        workflow_id: str,
        binding: "OpenShellBinding",
        checkpoint: StateT,
        *,
        reason: ResumeReason,
    ) -> None: ...


@dataclass(frozen=True)
class OpenShellBinding:
    """Declarative ownership mapping from an AFC workflow to one sandbox."""

    name: str
    workspace: str = "default"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("OpenShell sandbox name must not be empty")
        if not self.workspace:
            raise ValueError("OpenShell workspace must not be empty")


@dataclass(frozen=True)
class OpenShellCheckpoint(Generic[StateT]):
    """Application checkpoint plus the exact sandbox incarnation it owns."""

    workflow_id: str
    sandbox_id: str
    sandbox_name: str
    workspace: str
    application: StateT


class OpenShellCheckpointAdapter(Generic[StateT]):
    """Bind a durable application checkpoint to an exact OpenShell sandbox."""

    def __init__(
        self,
        *,
        checkpoints: CheckpointAdapter[StateT],
        client: OpenShellClient,
        bindings: Mapping[str, OpenShellBinding],
    ) -> None:
        self._checkpoints = checkpoints
        self._client = client
        self._bindings = dict(bindings)

    async def checkpoint(self, workflow_id: str) -> OpenShellCheckpoint[StateT]:
        binding = _binding_for(self._bindings, workflow_id)
        application = await self._checkpoints.checkpoint(workflow_id)
        sandbox = await _call_client(
            "inspect sandbox",
            self._client.get,
            binding.name,
            workspace=binding.workspace,
        )
        _require_identity(sandbox, binding)
        if int(sandbox.phase) != OpenShellPhase.READY:
            raise OpenShellRuntimeError(
                "OpenShell sandbox is not ready for a reclaimable wait"
            )
        return OpenShellCheckpoint(
            workflow_id=workflow_id,
            sandbox_id=sandbox.id,
            sandbox_name=binding.name,
            workspace=binding.workspace,
            application=application,
        )


class OpenShellSandboxRuntime(Generic[StateT]):
    """Release and recreate OpenShell compute with native stop/start.

    ``stop`` retains the OpenShell sandbox record and persistent workspace while
    its driver removes the container, pod, or VM allocation. ``start`` creates a
    new runtime generation. The adapter fails closed on identity or phase drift.
    """

    def __init__(
        self,
        *,
        client: OpenShellClient,
        bindings: Mapping[str, OpenShellBinding],
        application_restorer: OpenShellApplicationRestorer[StateT],
        transition_timeout_seconds: float = 300.0,
    ) -> None:
        if (
            not math.isfinite(transition_timeout_seconds)
            or transition_timeout_seconds <= 0
        ):
            raise ValueError("transition_timeout_seconds must be greater than zero")
        self._client = client
        self._bindings = dict(bindings)
        self._application_restorer = application_restorer
        self._transition_timeout_seconds = transition_timeout_seconds

    async def release(
        self,
        workflow_id: str,
        checkpoint: OpenShellCheckpoint[StateT] | Mapping[str, Any],
    ) -> bool:
        checkpoint = _coerce_checkpoint(checkpoint)
        binding = self._validated_binding(workflow_id, checkpoint)
        current = await self._get(binding)
        _require_checkpoint_identity(current, checkpoint)

        if int(current.phase) == OpenShellPhase.READY:
            await _call_client(
                "stop sandbox",
                self._client.stop,
                binding.name,
                workspace=binding.workspace,
            )
        elif int(current.phase) not in {
            OpenShellPhase.STOPPING,
            OpenShellPhase.STOPPED,
        }:
            return False

        stopped = await _call_client(
            "wait for stopped sandbox",
            self._client.wait_stopped,
            binding.name,
            workspace=binding.workspace,
            timeout_seconds=self._transition_timeout_seconds,
        )
        _require_checkpoint_identity(stopped, checkpoint)
        return int(stopped.phase) == OpenShellPhase.STOPPED

    async def is_allocated(self, workflow_id: str) -> bool:
        binding = _binding_for(self._bindings, workflow_id)
        current = await self._get(binding)
        return int(current.phase) != OpenShellPhase.STOPPED

    async def restore(
        self,
        workflow_id: str,
        checkpoint: OpenShellCheckpoint[StateT] | Mapping[str, Any],
        *,
        reason: ResumeReason,
    ) -> None:
        checkpoint = _coerce_checkpoint(checkpoint)
        binding = self._validated_binding(workflow_id, checkpoint)
        current = await self._get(binding)
        _require_checkpoint_identity(current, checkpoint)

        if int(current.phase) == OpenShellPhase.STOPPED:
            await _call_client(
                "start sandbox",
                self._client.start,
                binding.name,
                workspace=binding.workspace,
            )
        elif int(current.phase) not in {
            OpenShellPhase.READY,
            OpenShellPhase.STARTING,
        }:
            raise OpenShellRuntimeError(
                "OpenShell sandbox is not restartable from its current phase"
            )

        ready = await _call_client(
            "wait for ready sandbox",
            self._client.wait_ready,
            binding.name,
            workspace=binding.workspace,
            timeout_seconds=self._transition_timeout_seconds,
        )
        _require_checkpoint_identity(ready, checkpoint)
        if int(ready.phase) != OpenShellPhase.READY:
            raise OpenShellRuntimeError("OpenShell did not confirm sandbox restoration")
        try:
            await self._application_restorer.restore(
                workflow_id,
                binding,
                checkpoint.application,
                reason=reason,
            )
        except Exception:
            await self._rollback_failed_restore(binding, checkpoint)
            raise OpenShellRuntimeError(
                "application state restoration failed after OpenShell restart"
            ) from None

    async def _rollback_failed_restore(
        self,
        binding: OpenShellBinding,
        checkpoint: OpenShellCheckpoint[StateT],
    ) -> None:
        try:
            await _call_client(
                "stop sandbox after failed application restore",
                self._client.stop,
                binding.name,
                workspace=binding.workspace,
            )
            stopped = await _call_client(
                "wait for stopped sandbox after failed application restore",
                self._client.wait_stopped,
                binding.name,
                workspace=binding.workspace,
                timeout_seconds=self._transition_timeout_seconds,
            )
            _require_checkpoint_identity(stopped, checkpoint)
        except OpenShellRuntimeError:
            raise OpenShellRuntimeError(
                "application restore failed and OpenShell rollback was not confirmed"
            ) from None

    async def _get(self, binding: OpenShellBinding) -> OpenShellSandboxRef:
        current = await _call_client(
            "inspect sandbox",
            self._client.get,
            binding.name,
            workspace=binding.workspace,
        )
        _require_identity(current, binding)
        return current

    def _validated_binding(
        self,
        workflow_id: str,
        checkpoint: OpenShellCheckpoint[StateT],
    ) -> OpenShellBinding:
        binding = _binding_for(self._bindings, workflow_id)
        if checkpoint.workflow_id != workflow_id:
            raise OpenShellRuntimeError("checkpoint belongs to another workflow")
        if (
            checkpoint.sandbox_name != binding.name
            or checkpoint.workspace != binding.workspace
        ):
            raise OpenShellRuntimeError("checkpoint does not match sandbox binding")
        if not checkpoint.sandbox_id:
            raise OpenShellRuntimeError("checkpoint has no OpenShell sandbox identity")
        return binding


async def _call_client(operation: str, function, *args, **kwargs):
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except Exception:
        # OpenShell failures may contain endpoints or operational metadata. Keep
        # Temporal history and AFC logs content-free at this boundary.
        raise OpenShellRuntimeError(f"OpenShell failed to {operation}") from None


def _binding_for(
    bindings: Mapping[str, OpenShellBinding], workflow_id: str
) -> OpenShellBinding:
    if not workflow_id:
        raise ValueError("workflow_id must not be empty")
    try:
        return bindings[workflow_id]
    except KeyError:
        raise OpenShellRuntimeError("workflow has no OpenShell sandbox binding") from None


def _require_identity(
    sandbox: OpenShellSandboxRef, binding: OpenShellBinding
) -> None:
    if not sandbox.id:
        raise OpenShellRuntimeError("OpenShell returned an empty sandbox identity")
    if sandbox.name != binding.name:
        raise OpenShellRuntimeError("OpenShell returned a different sandbox name")
    if sandbox.workspace != binding.workspace:
        raise OpenShellRuntimeError("OpenShell returned a different workspace")


def _require_checkpoint_identity(
    sandbox: OpenShellSandboxRef, checkpoint: OpenShellCheckpoint[object]
) -> None:
    if sandbox.id != checkpoint.sandbox_id:
        raise OpenShellRuntimeError("OpenShell sandbox identity changed")


def _coerce_checkpoint(
    checkpoint: OpenShellCheckpoint[StateT] | Mapping[str, Any],
) -> OpenShellCheckpoint[StateT]:
    if isinstance(checkpoint, OpenShellCheckpoint):
        return checkpoint
    try:
        workflow_id = checkpoint["workflow_id"]
        sandbox_id = checkpoint["sandbox_id"]
        sandbox_name = checkpoint["sandbox_name"]
        workspace = checkpoint["workspace"]
        application = checkpoint["application"]
    except (KeyError, TypeError):
        raise OpenShellRuntimeError("invalid serialized OpenShell checkpoint") from None
    if not all(
        isinstance(value, str) and value
        for value in (workflow_id, sandbox_id, sandbox_name, workspace)
    ):
        raise OpenShellRuntimeError("invalid serialized OpenShell checkpoint")
    return OpenShellCheckpoint(
        workflow_id=workflow_id,
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        workspace=workspace,
        application=application,
    )
