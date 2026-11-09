from __future__ import annotations

from dataclasses import asdict, dataclass

from .model import AgentTrace, EventKind, WAIT_PAIRS


@dataclass(frozen=True, slots=True)
class SimulationPolicy:
    idle_after_seconds: float = 30.0
    checkpoint_seconds: float = 0.5
    resume_seconds: float = 1.5
    cpu_hourly_cost: float = 0.0
    memory_gib_hourly_cost: float = 0.0
    minimum_resource_savings_percent: float = 20.0
    maximum_completion_overhead_percent: float = 5.0

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True, slots=True)
class AgentResult:
    agent_id: str
    outcome: str
    baseline_completion_seconds: float
    controlled_completion_seconds: float
    reclaimed_idle_seconds: float
    suspensions: int
    baseline_core_seconds: float
    controlled_core_seconds: float
    baseline_memory_gib_seconds: float
    controlled_memory_gib_seconds: float


def _saving_percent(baseline: float, controlled: float) -> float:
    if baseline == 0:
        return 0.0
    return 100.0 * (baseline - controlled) / baseline


def simulate_agent(trace: AgentTrace, policy: SimulationPolicy) -> AgentResult:
    start = trace.events[0].timestamp
    end = trace.events[-1].timestamp
    lifetime = end - start
    reclaimed = 0.0
    suspensions = 0

    wait_started_at: float | None = None
    wait_kind: EventKind | None = None
    for event in trace.events[1:]:
        if event.kind in WAIT_PAIRS:
            wait_started_at = event.timestamp
            wait_kind = event.kind
            continue
        if wait_kind is not None and event.kind is WAIT_PAIRS[wait_kind]:
            assert wait_started_at is not None
            wait_duration = event.timestamp - wait_started_at
            reclaimable = wait_duration - policy.idle_after_seconds
            if reclaimable > 0:
                reclaimed += reclaimable
                suspensions += 1
            wait_started_at = None
            wait_kind = None

    if wait_started_at is not None:
        reclaimable = end - wait_started_at - policy.idle_after_seconds
        if reclaimable > 0:
            reclaimed += reclaimable
            suspensions += 1

    overhead_active = suspensions * (policy.checkpoint_seconds + policy.resume_seconds)
    controlled_active = max(0.0, lifetime - reclaimed + overhead_active)
    controlled_completion = lifetime + suspensions * policy.resume_seconds
    cpu = trace.resources.cpu_cores
    memory = trace.resources.memory_gib

    return AgentResult(
        agent_id=trace.agent_id,
        outcome=trace.events[-1].kind.value,
        baseline_completion_seconds=lifetime,
        controlled_completion_seconds=controlled_completion,
        reclaimed_idle_seconds=reclaimed,
        suspensions=suspensions,
        baseline_core_seconds=lifetime * cpu,
        controlled_core_seconds=controlled_active * cpu,
        baseline_memory_gib_seconds=lifetime * memory,
        controlled_memory_gib_seconds=controlled_active * memory,
    )


def simulate(traces: tuple[AgentTrace, ...], policy: SimulationPolicy) -> dict[str, object]:
    policy.validate()
    agents = [simulate_agent(trace, policy) for trace in traces]

    totals = {
        "agents": len(agents),
        "completed": sum(result.outcome == EventKind.AGENT_COMPLETED.value for result in agents),
        "failed": sum(result.outcome == EventKind.AGENT_FAILED.value for result in agents),
        "suspensions": sum(result.suspensions for result in agents),
        "reclaimed_idle_seconds": sum(result.reclaimed_idle_seconds for result in agents),
        "baseline_completion_seconds": sum(result.baseline_completion_seconds for result in agents),
        "controlled_completion_seconds": sum(result.controlled_completion_seconds for result in agents),
        "baseline_core_seconds": sum(result.baseline_core_seconds for result in agents),
        "controlled_core_seconds": sum(result.controlled_core_seconds for result in agents),
        "baseline_memory_gib_seconds": sum(
            result.baseline_memory_gib_seconds for result in agents
        ),
        "controlled_memory_gib_seconds": sum(
            result.controlled_memory_gib_seconds for result in agents
        ),
    }

    cpu_savings = _saving_percent(
        totals["baseline_core_seconds"], totals["controlled_core_seconds"]
    )
    memory_savings = _saving_percent(
        totals["baseline_memory_gib_seconds"],
        totals["controlled_memory_gib_seconds"],
    )
    completion_overhead = -_saving_percent(
        totals["baseline_completion_seconds"],
        totals["controlled_completion_seconds"],
    )
    baseline_cost = (
        totals["baseline_core_seconds"] * policy.cpu_hourly_cost
        + totals["baseline_memory_gib_seconds"] * policy.memory_gib_hourly_cost
    ) / 3600.0
    controlled_cost = (
        totals["controlled_core_seconds"] * policy.cpu_hourly_cost
        + totals["controlled_memory_gib_seconds"] * policy.memory_gib_hourly_cost
    ) / 3600.0
    completed = totals["completed"]

    metrics = {
        **totals,
        "trace_completion_rate_percent": 100.0 * completed / totals["agents"],
        "cpu_resource_time_savings_percent": cpu_savings,
        "memory_resource_time_savings_percent": memory_savings,
        "completion_time_overhead_percent": completion_overhead,
        "baseline_estimated_cost": baseline_cost,
        "controlled_estimated_cost": controlled_cost,
        "baseline_estimated_cost_per_completed_trace": (
            baseline_cost / completed if completed else None
        ),
        "controlled_estimated_cost_per_completed_trace": (
            controlled_cost / completed if completed else None
        ),
        "estimated_cost_savings_percent": _saving_percent(baseline_cost, controlled_cost),
    }
    gate = {
        "cpu_savings_pass": cpu_savings >= policy.minimum_resource_savings_percent,
        "memory_savings_pass": memory_savings >= policy.minimum_resource_savings_percent,
        "completion_overhead_pass": (
            completion_overhead <= policy.maximum_completion_overhead_percent
        ),
    }
    gate["pass"] = all(gate.values())

    return {
        "schema_version": 1,
        "evidence_level": "trace_simulation_only",
        "policy": asdict(policy),
        "metrics": metrics,
        "gate": gate,
        "agents": [asdict(result) for result in agents],
    }
