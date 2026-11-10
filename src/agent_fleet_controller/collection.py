from __future__ import annotations

import json
import math
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from .recorder import record_command
from .trace import WORKLOAD_CLASS_PATTERN


class CollectionSpecError(ValueError):
    """Raised when a workload collection specification is invalid."""


@dataclass(frozen=True, slots=True)
class WorkloadCommand:
    workload_class: str
    cpu_cores: float
    memory_gib: float
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CollectionResult:
    status: str
    output: Path
    workspace_root: Path
    requested_runs: int
    completed_runs: int
    failed_runs: int
    blocked_class: str | None = None
    blocked_returncode: int | None = None


def collection_result_payload(result: CollectionResult) -> dict[str, object]:
    """Return a content-free summary suitable for CLI output and status files."""

    payload = asdict(result)
    payload["output"] = str(result.output)
    payload["workspace_root"] = str(result.workspace_root)
    payload["mode"] = "external_command_collection"
    payload["commands_copied_to_output"] = False
    return payload


def _finite_positive(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionSpecError(f"{field} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise CollectionSpecError(f"{field} must be finite and greater than zero")
    return parsed


def load_collection_spec(path: str | Path) -> tuple[WorkloadCommand, ...]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CollectionSpecError(f"invalid collection spec JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "classes"}:
        raise CollectionSpecError(
            "collection spec must contain exactly schema_version and classes"
        )
    if isinstance(raw["schema_version"], bool) or raw["schema_version"] != 1:
        raise CollectionSpecError("only collection spec schema_version 1 is supported")
    classes = raw["classes"]
    if not isinstance(classes, list) or len(classes) < 2:
        raise CollectionSpecError("classes must contain at least two workload classes")

    parsed: list[WorkloadCommand] = []
    names: set[str] = set()
    expected = {"workload_class", "cpu_cores", "memory_gib", "command"}
    for index, item in enumerate(classes):
        if not isinstance(item, dict) or set(item) != expected:
            raise CollectionSpecError(
                f"classes[{index}] must contain exactly: {', '.join(sorted(expected))}"
            )
        workload_class = item["workload_class"]
        if not isinstance(workload_class, str) or (
            WORKLOAD_CLASS_PATTERN.fullmatch(workload_class) is None
        ):
            raise CollectionSpecError(
                f"classes[{index}].workload_class must be a lowercase safe label"
            )
        if workload_class in names:
            raise CollectionSpecError(f"duplicate workload class: {workload_class}")
        names.add(workload_class)
        command = item["command"]
        if not isinstance(command, list) or not command:
            raise CollectionSpecError(f"classes[{index}].command must be a non-empty array")
        if any(
            not isinstance(argument, str) or not argument or "\x00" in argument
            for argument in command
        ):
            raise CollectionSpecError(
                f"classes[{index}].command arguments must be non-empty strings"
            )
        if len(command) > 256 or sum(len(argument) for argument in command) > 32768:
            raise CollectionSpecError(f"classes[{index}].command is too large")
        parsed.append(
            WorkloadCommand(
                workload_class=workload_class,
                cpu_cores=_finite_positive(
                    item["cpu_cores"], f"classes[{index}].cpu_cores"
                ),
                memory_gib=_finite_positive(
                    item["memory_gib"], f"classes[{index}].memory_gib"
                ),
                command=tuple(command),
            )
        )
    return tuple(parsed)


def _write_private_json(path: Path, payload: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")


def collect_workloads(
    workloads: tuple[WorkloadCommand, ...],
    *,
    output: str | Path,
    workspace_root: str | Path,
    runs_per_class: int = 30,
    timeout_seconds: float = 900.0,
    sample_interval: float = 1.0,
) -> CollectionResult:
    if len(workloads) < 2:
        raise ValueError("at least two workload classes are required")
    class_names = [workload.workload_class for workload in workloads]
    if len(set(class_names)) != len(class_names) or any(
        WORKLOAD_CLASS_PATTERN.fullmatch(name) is None for name in class_names
    ):
        raise ValueError("workload classes must be unique lowercase safe labels")
    if any(not workload.command for workload in workloads):
        raise ValueError("every workload command must be non-empty")
    if any(
        not math.isfinite(workload.cpu_cores)
        or not math.isfinite(workload.memory_gib)
        or workload.cpu_cores <= 0
        or workload.memory_gib <= 0
        for workload in workloads
    ):
        raise ValueError("workload resources must be finite and greater than zero")
    if (
        isinstance(runs_per_class, bool)
        or not isinstance(runs_per_class, int)
        or runs_per_class <= 0
    ):
        raise ValueError("runs_per_class must be greater than zero")
    if (
        not math.isfinite(timeout_seconds)
        or not math.isfinite(sample_interval)
        or timeout_seconds <= 0
        or sample_interval <= 0
    ):
        raise ValueError(
            "timeout_seconds and sample_interval must be finite and greater than zero"
        )
    timeout_binary = shutil.which("timeout")
    if timeout_binary is None:
        raise OSError("the timeout command is required for bounded collection")

    output_path = Path(output)
    workspace_path = Path(workspace_root)
    if output_path.exists() or workspace_path.exists():
        raise FileExistsError("refusing to reuse an output or workspace directory")
    resolved_output = output_path.resolve()
    resolved_workspace = workspace_path.resolve()
    if (
        resolved_output == resolved_workspace
        or resolved_output.is_relative_to(resolved_workspace)
        or resolved_workspace.is_relative_to(resolved_output)
    ):
        raise ValueError("output and workspace_root must be separate directory trees")
    output_path.mkdir(parents=True, mode=0o700)
    workspace_path.mkdir(parents=True, mode=0o700)

    completed = 0
    failed = 0
    total_requested = len(workloads) * runs_per_class
    run_order = [
        (workload, run_number)
        for run_number in range(1, runs_per_class + 1)
        for workload in workloads
    ]
    for workload, run_number in run_order:
        class_output = output_path / workload.workload_class
        class_workspace = workspace_path / workload.workload_class
        class_output.mkdir(mode=0o700, exist_ok=True)
        class_workspace.mkdir(mode=0o700, exist_ok=True)
        run_workspace = class_workspace / f"run-{run_number:03d}"
        run_workspace.mkdir(mode=0o700)
        trace_path = class_output / f"run-{run_number:03d}.jsonl"
        bounded_command = [
            timeout_binary,
            "--signal=INT",
            "--kill-after=5s",
            f"{timeout_seconds}s",
            *workload.command,
        ]
        record = record_command(
            bounded_command,
            trace_path=trace_path,
            metrics_path=trace_path.with_suffix(".metrics.jsonl"),
            cpu_cores=workload.cpu_cores,
            memory_gib=workload.memory_gib,
            workload_class=workload.workload_class,
            sample_interval=sample_interval,
            cwd=run_workspace,
        )
        if record.returncode == 0:
            completed += 1
            continue

        failed += 1
        result = CollectionResult(
            status="blocked_on_preflight" if run_number == 1 else "failed",
            output=output_path,
            workspace_root=workspace_path,
            requested_runs=total_requested,
            completed_runs=completed,
            failed_runs=failed,
            blocked_class=workload.workload_class,
            blocked_returncode=record.returncode,
        )
        _write_private_json(
            output_path / "collection-status.json", collection_result_payload(result)
        )
        return result

    result = CollectionResult(
        status="complete",
        output=output_path,
        workspace_root=workspace_path,
        requested_runs=total_requested,
        completed_runs=completed,
        failed_runs=failed,
    )
    _write_private_json(
        output_path / "collection-status.json", collection_result_payload(result)
    )
    return result
