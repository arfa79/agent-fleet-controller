from __future__ import annotations

import json

import pytest

from agent_fleet_controller.analysis import analyze, analyze_paths, load_traces
from agent_fleet_controller.simulation import SimulationPolicy
from agent_fleet_controller.trace import TraceValidationError, parse_jsonl


def _trace(agent_id: str, workload_class: str, wait_end: float):
    return parse_jsonl(
        [
            json.dumps(
                {
                    "v": 1,
                    "ts": 0,
                    "agent_id": agent_id,
                    "kind": "agent_started",
                    "cpu_cores": 1,
                    "memory_gib": 2,
                    "workload_class": workload_class,
                }
            ),
            json.dumps(
                {
                    "v": 1,
                    "ts": 10,
                    "agent_id": agent_id,
                    "kind": "model_wait_started",
                }
            ),
            json.dumps(
                {
                    "v": 1,
                    "ts": wait_end,
                    "agent_id": agent_id,
                    "kind": "model_wait_finished",
                }
            ),
            json.dumps(
                {
                    "v": 1,
                    "ts": 100,
                    "agent_id": agent_id,
                    "kind": "agent_completed",
                }
            ),
        ]
    )[0]


def test_analyze_keeps_workload_class_gates_separate() -> None:
    report = analyze(
        (
            _trace("coding-1", "coding-agent", 25),
            _trace("research-1", "research-agent", 90),
        ),
        SimulationPolicy(idle_after_seconds=30, checkpoint_seconds=1, resume_seconds=1),
        minimum_traces_per_class=1,
    )

    classes = report["workload_classes"]
    assert classes["coding-agent"]["simulation"]["gate"]["pass"] is False
    assert classes["research-agent"]["simulation"]["gate"]["pass"] is True
    assert classes["coding-agent"]["waits"]["model"]["p95_seconds"] == 15
    assert classes["research-agent"]["waits"]["model"]["p95_seconds"] == 80
    assert report["collection_gate"]["pass"] is True
    assert report["hypothesis_gate"]["pass"] is False


def test_analyze_directory_excludes_metrics_files(tmp_path) -> None:
    trace = tmp_path / "run.jsonl"
    metrics = tmp_path / "run.metrics.jsonl"
    events = _trace("agent-1", "coding-agent", 25).events
    trace.write_text(
        "\n".join(
            json.dumps(
                {
                    "v": event.version,
                    "ts": event.timestamp,
                    "agent_id": event.agent_id,
                    "kind": event.kind.value,
                    **(
                        {
                            "cpu_cores": event.resources.cpu_cores,
                            "memory_gib": event.resources.memory_gib,
                            "workload_class": event.workload_class,
                        }
                        if event.resources is not None
                        else {}
                    ),
                }
            )
            for event in events
        )
        + "\n"
    )
    metrics.write_text(
        '{"v":1,"ts":0,"agent_id":"agent-1","cpu_seconds":0.1,'
        '"rss_bytes":1073741824,"processes":1}\n'
        '{"v":1,"ts":1,"agent_id":"agent-1","cpu_seconds":0.3,'
        '"rss_bytes":1073741824,"processes":1}\n'
    )

    report = analyze_paths(
        [tmp_path],
        SimulationPolicy(),
        minimum_traces_per_class=1,
        minimum_workload_classes=1,
    )

    assert report["agents"] == 1
    assert report["trace_files"] == 1
    assert report["metrics_files"] == 1
    observed = report["overall"]["resource_observations"]
    assert observed["agents_with_metrics"] == 1
    assert observed["observed_cpu_seconds"] == pytest.approx(0.3)
    assert observed["observed_memory_gib_seconds"] == pytest.approx(1.0)


def test_rejects_duplicate_agent_ids_across_files(tmp_path) -> None:
    payload = (
        '{"v":1,"ts":0,"agent_id":"duplicate","kind":"agent_started",'
        '"cpu_cores":1,"memory_gib":1}\n'
        '{"v":1,"ts":1,"agent_id":"duplicate","kind":"agent_completed"}\n'
    )
    (tmp_path / "one.jsonl").write_text(payload)
    (tmp_path / "two.jsonl").write_text(payload)

    with pytest.raises(TraceValidationError, match="duplicate agent_id"):
        load_traces([tmp_path])
