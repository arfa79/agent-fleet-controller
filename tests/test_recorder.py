from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_fleet_controller.recorder import record_command
from agent_fleet_controller.trace import load_trace


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_records_real_process_markers_and_private_metrics(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    metrics_path = tmp_path / "metrics.jsonl"
    source_root = Path(__file__).parents[1] / "src"
    program = (
        f"import sys,time; sys.path.insert(0,{str(source_root)!r}); "
        "from agent_fleet_controller.markers import wait; "
        "ctx=wait('model'); ctx.__enter__(); time.sleep(0.08); ctx.__exit__(None,None,None)"
    )
    result = record_command(
        [sys.executable, "-c", program],
        trace_path=trace_path,
        metrics_path=metrics_path,
        cpu_cores=1,
        memory_gib=1,
        workload_class="coding-agent",
        sample_interval=0.02,
    )

    assert result.returncode == 0
    assert result.samples >= 1
    assert result.rejected_markers == 0
    assert load_trace(trace_path)[0].workload_class == "coding-agent"
    assert [event["kind"] for event in _read_jsonl(trace_path)] == [
        "agent_started",
        "model_wait_started",
        "model_wait_finished",
        "agent_completed",
    ]
    metrics = _read_jsonl(metrics_path)
    assert metrics
    assert set(metrics[0]) == {
        "agent_id",
        "cpu_seconds",
        "processes",
        "rss_bytes",
        "ts",
        "v",
    }
    assert trace_path.stat().st_mode & 0o777 == 0o600
    assert metrics_path.stat().st_mode & 0o777 == 0o600


def test_records_nonzero_exit_as_failure(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    metrics_path = tmp_path / "metrics.jsonl"
    result = record_command(
        [sys.executable, "-c", "raise SystemExit(7)"],
        trace_path=trace_path,
        metrics_path=metrics_path,
        cpu_cores=1,
        memory_gib=1,
        sample_interval=0.02,
    )
    assert result.returncode == 7
    assert _read_jsonl(trace_path)[-1]["kind"] == "agent_failed"


def test_refuses_to_overwrite_existing_recording(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    metrics_path = tmp_path / "metrics.jsonl"
    trace_path.write_text("owned by user")
    try:
        record_command(
            [sys.executable, "-c", "pass"],
            trace_path=trace_path,
            metrics_path=metrics_path,
            cpu_cores=1,
            memory_gib=1,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing output was not protected")
    assert trace_path.read_text() == "owned by user"
    assert not metrics_path.exists()


def test_does_not_launch_command_when_output_setup_fails(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    blocking_parent = tmp_path / "not-a-directory"
    blocking_parent.write_text("block directory creation")
    metrics_path = blocking_parent / "metrics.jsonl"
    side_effect = tmp_path / "child-ran"

    try:
        record_command(
            [sys.executable, "-c", f"open({str(side_effect)!r}, 'w').close()"],
            trace_path=trace_path,
            metrics_path=metrics_path,
            cpu_cores=1,
            memory_gib=1,
        )
    except OSError:
        pass
    else:
        raise AssertionError("invalid output setup was accepted")

    assert not side_effect.exists()
    assert not trace_path.exists()


def test_runs_command_in_explicit_working_directory(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trace_path = tmp_path / "trace.jsonl"
    metrics_path = tmp_path / "metrics.jsonl"

    result = record_command(
        [sys.executable, "-c", "from pathlib import Path; Path('result').touch()"],
        trace_path=trace_path,
        metrics_path=metrics_path,
        cpu_cores=1,
        memory_gib=1,
        cwd=workspace,
    )

    assert result.returncode == 0
    assert (workspace / "result").exists()
