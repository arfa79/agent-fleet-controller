from __future__ import annotations

import asyncio
import contextlib

import pytest

import agent_fleet_controller.durable as durable
from agent_fleet_controller.durable import ReclaimabilityError


def _fake_wait(actions: list[str]):
    @contextlib.contextmanager
    def fake_wait(wait_type: str):
        actions.append(f"{wait_type}:started")
        try:
            yield
        finally:
            actions.append(f"{wait_type}:finished")

    return fake_wait


def test_reclaimable_wait_enforces_safe_order(monkeypatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr(durable, "wait", _fake_wait(actions))

    result = durable.run_reclaimable_wait(
        "model",
        checkpoint=lambda: actions.append("checkpoint") or "saved-state",
        release=lambda state: actions.append(f"release:{state}") or True,
        external_wait=lambda: actions.append("external-wait") or "response",
        restore=lambda state: actions.append(f"restore:{state}"),
    )

    assert result == "response"
    assert actions == [
        "checkpoint",
        "release:saved-state",
        "model:started",
        "external-wait",
        "model:finished",
        "restore:saved-state",
    ]


def test_reclaimable_wait_refuses_unconfirmed_release(monkeypatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr(durable, "wait", _fake_wait(actions))

    with pytest.raises(ReclaimabilityError, match="did not confirm"):
        durable.run_reclaimable_wait(
            "tool",
            checkpoint=lambda: actions.append("checkpoint") or "saved-state",
            release=lambda state: actions.append(f"release:{state}") or False,
            external_wait=lambda: actions.append("external-wait"),
            restore=lambda state: actions.append(f"restore:{state}"),
        )

    assert actions == ["checkpoint", "release:saved-state"]


def test_reclaimable_wait_restores_after_external_failure(monkeypatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr(durable, "wait", _fake_wait(actions))

    def fail() -> None:
        actions.append("external-wait")
        raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        durable.run_reclaimable_wait(
            "model",
            checkpoint=lambda: "saved-state",
            release=lambda state: True,
            external_wait=fail,
            restore=lambda state: actions.append("restore"),
        )

    assert actions == [
        "model:started",
        "external-wait",
        "model:finished",
        "restore",
    ]


def test_async_reclaimable_wait_enforces_safe_order(monkeypatch) -> None:
    actions: list[str] = []
    monkeypatch.setattr(durable, "wait", _fake_wait(actions))

    async def exercise() -> str:
        async def checkpoint() -> str:
            actions.append("checkpoint")
            return "saved-state"

        async def release(state: str) -> bool:
            actions.append(f"release:{state}")
            return True

        async def external_wait() -> str:
            actions.append("external-wait")
            return "response"

        async def restore(state: str) -> None:
            actions.append(f"restore:{state}")

        return await durable.run_reclaimable_wait_async(
            "model",
            checkpoint=checkpoint,
            release=release,
            external_wait=external_wait,
            restore=restore,
        )

    assert asyncio.run(exercise()) == "response"
    assert actions == [
        "checkpoint",
        "release:saved-state",
        "model:started",
        "external-wait",
        "model:finished",
        "restore:saved-state",
    ]


def test_reclaimable_wait_validates_type_before_side_effects() -> None:
    called = False

    def checkpoint() -> str:
        nonlocal called
        called = True
        return "saved-state"

    with pytest.raises(ValueError, match="model, tool, or human"):
        durable.run_reclaimable_wait(
            "network",
            checkpoint=checkpoint,
            release=lambda state: True,
            external_wait=lambda: None,
            restore=lambda state: None,
        )

    assert called is False
