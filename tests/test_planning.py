from __future__ import annotations

from agent_fleet_controller.planning import plan, plan_agent
from agent_fleet_controller.simulation import SimulationPolicy
from agent_fleet_controller.trace import parse_jsonl


def _trace(terminal: str = "agent_completed"):
    finish = (
        '{"v":1,"ts":50,"agent_id":"a","kind":"model_wait_finished"}'
        if terminal == "agent_completed"
        else None
    )
    lines = [
        '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started",'
        '"cpu_cores":1,"memory_gib":1}',
        '{"v":1,"ts":10,"agent_id":"a","kind":"model_wait_started"}',
    ]
    if finish:
        lines.append(finish)
    lines.append(f'{{"v":1,"ts":60,"agent_id":"a","kind":"{terminal}"}}')
    return parse_jsonl(lines)[0]


def test_plan_emits_deterministic_observe_only_decisions() -> None:
    policy = SimulationPolicy(idle_after_seconds=20)
    first = plan((_trace(),), policy)
    second = plan((_trace(),), policy)

    assert first == second
    assert first["mode"] == "observe_only_dry_run"
    assert first["mutates_runtime"] is False
    assert first["summary"] == {
        "would_suspend": 1,
        "would_resume": 1,
        "terminal_observed": 0,
    }
    assert [decision["timestamp"] for decision in first["decisions"]] == [30, 50]


def test_failed_agent_is_not_planned_for_resume() -> None:
    decisions = plan_agent(_trace("agent_failed"), SimulationPolicy(idle_after_seconds=20))

    assert [decision.action for decision in decisions] == [
        "would_suspend",
        "terminal_observed",
    ]
