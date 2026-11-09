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
