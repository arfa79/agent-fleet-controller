from __future__ import annotations

import pytest

from agent_fleet_controller.simulation import SimulationPolicy, simulate, simulate_agent
from agent_fleet_controller.trace import parse_jsonl


def _single_trace(wait_end: float = 100.0):
    return parse_jsonl(
        [
            '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":2,"memory_gib":4}',
            '{"v":1,"ts":10,"agent_id":"a","kind":"model_wait_started"}',
            f'{{"v":1,"ts":{wait_end},"agent_id":"a","kind":"model_wait_finished"}}',
            f'{{"v":1,"ts":{wait_end + 20},"agent_id":"a","kind":"agent_completed"}}',
        ]
    )[0]


def test_accounts_for_reclamation_and_overhead() -> None:
    result = simulate_agent(
        _single_trace(),
        SimulationPolicy(idle_after_seconds=30, checkpoint_seconds=1, resume_seconds=1),
    )
    assert result.reclaimed_idle_seconds == 60
    assert result.suspensions == 1
    assert result.baseline_core_seconds == 240
    assert result.controlled_core_seconds == 124
    assert result.controlled_completion_seconds == 121


def test_short_wait_does_not_suspend() -> None:
    result = simulate_agent(_single_trace(wait_end=20), SimulationPolicy(idle_after_seconds=30))
    assert result.suspensions == 0
    assert result.reclaimed_idle_seconds == 0


def test_gate_requires_savings_and_bounded_latency() -> None:
    report = simulate(
        (_single_trace(),),
        SimulationPolicy(
            idle_after_seconds=30,
            checkpoint_seconds=1,
            resume_seconds=1,
            minimum_resource_savings_percent=20,
            maximum_completion_overhead_percent=5,
        ),
    )
    assert report["gate"]["pass"] is True
    assert report["metrics"]["cpu_resource_time_savings_percent"] == pytest.approx(48.3333)
    assert report["metrics"]["trace_completion_rate_percent"] == 100


def test_policy_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="idle_after_seconds"):
        simulate((_single_trace(),), SimulationPolicy(idle_after_seconds=-1))


def test_failed_wait_is_reclaimable_until_terminal_event() -> None:
    trace = parse_jsonl(
        [
            '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started","cpu_cores":1,"memory_gib":1}',
            '{"v":1,"ts":10,"agent_id":"a","kind":"tool_wait_started"}',
            '{"v":1,"ts":100,"agent_id":"a","kind":"agent_failed"}',
        ]
    )[0]
    result = simulate_agent(trace, SimulationPolicy(idle_after_seconds=30))
    assert result.reclaimed_idle_seconds == 60
    assert result.suspensions == 1
