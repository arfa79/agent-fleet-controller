from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass

from .model import AgentTrace, EventKind, WAIT_PAIRS
from .simulation import SimulationPolicy


@dataclass(frozen=True, slots=True)
class DryRunDecision:
    action_id: str
    agent_id: str
    action: str
    timestamp: float
    reason: str
    wait_type: str


def _action_id(agent_id: str, wait_number: int, action: str) -> str:
    value = f"afc-plan-v1\0{agent_id}\0{wait_number}\0{action}".encode()
    return hashlib.sha256(value).hexdigest()[:24]


def plan_agent(trace: AgentTrace, policy: SimulationPolicy) -> tuple[DryRunDecision, ...]:
    policy.validate()
    decisions: list[DryRunDecision] = []
    wait_started_at: float | None = None
    wait_kind: EventKind | None = None
    wait_number = 0

    for event in trace.events[1:]:
        if event.kind in WAIT_PAIRS:
            wait_started_at = event.timestamp
            wait_kind = event.kind
            wait_number += 1
            continue
        if wait_kind is None:
            continue
        expected_finish = WAIT_PAIRS[wait_kind]
        if event.kind is not expected_finish and event.kind is not EventKind.AGENT_FAILED:
            continue

        assert wait_started_at is not None
        eligible_at = wait_started_at + policy.idle_after_seconds
        wait_type = wait_kind.value.removesuffix("_wait_started")
        if event.timestamp > eligible_at:
            decisions.append(
                DryRunDecision(
                    action_id=_action_id(trace.agent_id, wait_number, "would_suspend"),
                    agent_id=trace.agent_id,
                    action="would_suspend",
                    timestamp=eligible_at,
                    reason="reclaimable_wait_threshold_reached",
                    wait_type=wait_type,
                )
            )
            action = "would_resume" if event.kind is expected_finish else "terminal_observed"
            reason = "reclaimable_wait_finished" if event.kind is expected_finish else "agent_failed"
            decisions.append(
                DryRunDecision(
                    action_id=_action_id(trace.agent_id, wait_number, action),
                    agent_id=trace.agent_id,
                    action=action,
                    timestamp=event.timestamp,
                    reason=reason,
                    wait_type=wait_type,
                )
            )
        wait_started_at = None
        wait_kind = None

    return tuple(decisions)


def plan(traces: tuple[AgentTrace, ...], policy: SimulationPolicy) -> dict[str, object]:
    decisions = sorted(
        (decision for trace in traces for decision in plan_agent(trace, policy)),
        key=lambda decision: (decision.timestamp, decision.agent_id, decision.action_id),
    )
    return {
        "schema_version": 1,
        "mode": "observe_only_dry_run",
        "mutates_runtime": False,
        "policy": asdict(policy),
        "agents": len(traces),
        "decisions": [asdict(decision) for decision in decisions],
        "summary": {
            "would_suspend": sum(d.action == "would_suspend" for d in decisions),
            "would_resume": sum(d.action == "would_resume" for d in decisions),
            "terminal_observed": sum(d.action == "terminal_observed" for d in decisions),
        },
    }
