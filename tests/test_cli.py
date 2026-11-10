from __future__ import annotations

import json
import sys

from agent_fleet_controller.cli import main


def test_record_accepts_separator_and_propagates_child_exit(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"

    returncode = main(
        [
            "record",
            "--output",
            str(trace_path),
            "--cpu-cores",
            "1",
            "--memory-gib",
            "1",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ]
    )

    assert returncode == 7
    summary = json.loads(capsys.readouterr().err)
    assert summary["returncode"] == 7
    assert trace_path.exists()
    assert (tmp_path / "trace.metrics.jsonl").exists()


def test_record_requires_a_command(tmp_path, capsys) -> None:
    returncode = main(
        [
            "record",
            "--output",
            str(tmp_path / "trace.jsonl"),
            "--cpu-cores",
            "1",
            "--memory-gib",
            "1",
        ]
    )

    assert returncode == 2
    assert "a command is required" in capsys.readouterr().err


def test_analyze_reports_collection_gate(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started",'
        '"cpu_cores":1,"memory_gib":1,"workload_class":"coding-agent"}\n'
        '{"v":1,"ts":1,"agent_id":"a","kind":"agent_completed"}\n'
    )

    returncode = main(
        [
            "analyze",
            str(tmp_path),
            "--minimum-traces-per-class",
            "1",
            "--minimum-workload-classes",
            "1",
        ]
    )

    assert returncode == 0
    report = json.loads(capsys.readouterr().out)
    assert report["collection_gate"]["pass"] is True
    assert report["trace_files"] == 1


def test_plan_cli_is_observe_only(tmp_path, capsys) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"v":1,"ts":0,"agent_id":"a","kind":"agent_started",'
        '"cpu_cores":1,"memory_gib":1}\n'
        '{"v":1,"ts":1,"agent_id":"a","kind":"model_wait_started"}\n'
        '{"v":1,"ts":40,"agent_id":"a","kind":"model_wait_finished"}\n'
        '{"v":1,"ts":41,"agent_id":"a","kind":"agent_completed"}\n'
    )

    returncode = main(["plan", str(trace_path), "--idle-after", "10"])

    assert returncode == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mutates_runtime"] is False
    assert report["summary"]["would_suspend"] == 1


def test_experiment_cli_uses_example_manifest(capsys) -> None:
    from pathlib import Path

    manifest = Path(__file__).parents[1] / "examples" / "experiment-manifest.json"
    returncode = main(["experiment", str(manifest)])

    assert returncode == 0
    report = json.loads(capsys.readouterr().out)
    assert report["experiment"]["experiment_id"] == "demo-v1"


def test_collect_cli_returns_blocked_status(tmp_path, capsys) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "classes": [
                    {
                        "workload_class": "coding-agent",
                        "cpu_cores": 1,
                        "memory_gib": 1,
                        "command": [sys.executable, "-c", "raise SystemExit(9)"],
                    },
                    {
                        "workload_class": "research-agent",
                        "cpu_cores": 1,
                        "memory_gib": 1,
                        "command": [sys.executable, "-c", "pass"],
                    },
                ],
            }
        )
    )

    returncode = main(
        [
            "collect",
            "--spec",
            str(spec),
            "--output",
            str(tmp_path / "recordings"),
            "--workspace-root",
            str(tmp_path / "workspaces"),
            "--sample-interval",
            "0.01",
        ]
    )

    assert returncode == 3
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked_on_preflight"
    assert report["commands_copied_to_output"] is False


def test_record_cli_accepts_explicit_working_directory(tmp_path, capsys) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace_path = tmp_path / "trace.jsonl"

    returncode = main(
        [
            "record",
            "--output",
            str(trace_path),
            "--cpu-cores",
            "1",
            "--memory-gib",
            "1",
            "--cwd",
            str(workspace),
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('result').touch()",
        ]
    )

    assert returncode == 0
    assert (workspace / "result").exists()
    assert json.loads(capsys.readouterr().err)["returncode"] == 0
