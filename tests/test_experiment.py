from __future__ import annotations

import json

import pytest

from agent_fleet_controller.experiment import ManifestValidationError, run_experiment


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": "test-v1",
        "evidence_kind": "synthetic",
        "runtime": {"name": "test-runtime", "version": "v1", "driver": "none"},
        "agent": {"name": "test-agent", "version": "v1"},
        "model": {"provider": "none", "model_id": "synthetic-model"},
        "tool_environment": {"name": "test-tools", "revision": "v1"},
        "workload": {
            "dataset_id": "test-data",
            "revision": "v1",
            "real_agent_workload": False,
        },
        "baseline": {
            "kind": "always_on",
            "native_policy_enabled": False,
            "price_table_revision": "test-v1",
        },
        "evaluator": {"name": "terminal-status", "version": "v1"},
        "collection": {
            "inputs": ["trace.jsonl"],
            "minimum_traces_per_class": 1,
            "minimum_workload_classes": 1,
        },
        "policy": {
            "idle_after_seconds": 30,
            "checkpoint_seconds": 0.5,
            "resume_seconds": 1.5,
            "cpu_hourly_cost": 0,
            "memory_gib_hourly_cost": 0,
            "minimum_resource_savings_percent": 20,
            "maximum_completion_overhead_percent": 5,
        },
    }


def test_runs_reproducible_manifest_relative_to_its_directory(tmp_path) -> None:
    (tmp_path / "trace.jsonl").write_text(
        '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started",'
        '"cpu_cores":1,"memory_gib":1,"workload_class":"test-agent"}\n'
        '{"v":1,"ts":40,"agent_id":"a","kind":"agent_completed"}\n'
    )
    manifest_path = tmp_path / "experiment.json"
    manifest_path.write_text(json.dumps(_manifest()))

    report = run_experiment(manifest_path)

    assert report["experiment"]["experiment_id"] == "test-v1"
    assert report["agents"] == 1
    assert report["collection_gate"]["pass"] is True


def test_rejects_manifest_schema_drift(tmp_path) -> None:
    manifest = _manifest()
    manifest["credentials"] = "do-not-accept-arbitrary-fields"
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(manifest))

    with pytest.raises(ManifestValidationError, match="unknown: credentials"):
        run_experiment(path)
