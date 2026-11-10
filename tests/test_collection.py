from __future__ import annotations

import json
import sys

import pytest

from agent_fleet_controller.collection import (
    CollectionSpecError,
    collect_workloads,
    load_collection_spec,
)
from agent_fleet_controller.trace import load_trace


def _write_spec(path, commands) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "classes": [
                    {
                        "workload_class": name,
                        "cpu_cores": 1,
                        "memory_gib": 1,
                        "command": command,
                    }
                    for name, command in commands
                ],
            }
        )
    )


def test_collects_interleaved_classes_without_persisting_commands(tmp_path) -> None:
    spec = tmp_path / "spec.json"
    command = [sys.executable, "-c", "from pathlib import Path; Path('done').touch()"]
    _write_spec(spec, [("coding-agent", command), ("research-agent", command)])
    output = tmp_path / "recordings"
    workspaces = tmp_path / "workspaces"

    result = collect_workloads(
        load_collection_spec(spec),
        output=output,
        workspace_root=workspaces,
        runs_per_class=2,
        sample_interval=0.01,
    )

    assert result.status == "complete"
    assert result.completed_runs == 4
    assert (workspaces / "coding-agent" / "run-001" / "done").exists()
    assert (workspaces / "research-agent" / "run-002" / "done").exists()
    assert load_trace(output / "coding-agent" / "run-001.jsonl")[0].workload_class == (
        "coding-agent"
    )
    status = (output / "collection-status.json").read_text()
    assert "commands_copied_to_output" in status
    assert "Path('done')" not in status


def test_stops_after_failed_class_preflight(tmp_path) -> None:
    spec = tmp_path / "spec.json"
    _write_spec(
        spec,
        [
            ("coding-agent", [sys.executable, "-c", "raise SystemExit(7)"]),
            ("research-agent", [sys.executable, "-c", "pass"]),
        ],
    )
    output = tmp_path / "recordings"

    result = collect_workloads(
        load_collection_spec(spec),
        output=output,
        workspace_root=tmp_path / "workspaces",
        runs_per_class=30,
        sample_interval=0.01,
    )

    assert result.status == "blocked_on_preflight"
    assert result.failed_runs == 1
    assert result.blocked_returncode == 7
    assert not (output / "research-agent").exists()


def test_rejects_single_class_collection(tmp_path) -> None:
    spec = tmp_path / "spec.json"
    _write_spec(spec, [("coding-agent", [sys.executable, "-c", "pass"])])

    with pytest.raises(CollectionSpecError, match="at least two"):
        load_collection_spec(spec)


def test_rejects_nested_output_and_workspace_trees(tmp_path) -> None:
    spec = tmp_path / "spec.json"
    command = [sys.executable, "-c", "pass"]
    _write_spec(spec, [("coding-agent", command), ("research-agent", command)])

    with pytest.raises(ValueError, match="separate directory trees"):
        collect_workloads(
            load_collection_spec(spec),
            output=tmp_path / "data",
            workspace_root=tmp_path / "data" / "workspaces",
        )
