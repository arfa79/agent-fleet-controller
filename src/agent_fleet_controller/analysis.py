from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable, Sequence

from .model import AgentTrace, EventKind, WAIT_PAIRS
from .simulation import SimulationPolicy, simulate
from .trace import TraceValidationError, load_trace


def discover_trace_files(inputs: Sequence[str | Path]) -> tuple[Path, ...]:
    """Resolve lifecycle traces while excluding recorder metrics files."""

    discovered: set[Path] = set()
    for raw_input in inputs:
        path = Path(raw_input)
        if path.is_dir():
            candidates = path.rglob("*.jsonl")
        elif path.is_file():
            candidates = (path,)
        else:
            raise FileNotFoundError(f"trace input does not exist: {path}")

        for candidate in candidates:
            if candidate.name.endswith(".metrics.jsonl"):
                continue
            discovered.add(candidate.resolve())

    if not discovered:
        raise TraceValidationError("no lifecycle trace files were found")
    return tuple(sorted(discovered))


def load_traces(
    inputs: Sequence[str | Path],
) -> tuple[tuple[AgentTrace, ...], tuple[Path, ...]]:
    files = discover_trace_files(inputs)
    traces: list[AgentTrace] = []
    seen_ids: dict[str, Path] = {}

    for path in files:
        for trace in load_trace(path):
            previous = seen_ids.get(trace.agent_id)
            if previous is not None:
                raise TraceValidationError(
                    f"duplicate agent_id {trace.agent_id!r} in {previous} and {path}"
                )
            seen_ids[trace.agent_id] = path
            traces.append(trace)

    return tuple(sorted(traces, key=lambda trace: trace.agent_id)), files


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _distribution(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "total_seconds": 0.0,
            "mean_seconds": None,
            "p50_seconds": None,
            "p95_seconds": None,
            "p99_seconds": None,
            "max_seconds": None,
        }
    return {
        "count": len(values),
        "total_seconds": sum(values),
        "mean_seconds": sum(values) / len(values),
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "p99_seconds": _percentile(values, 0.99),
        "max_seconds": max(values),
    }


def _wait_distributions(traces: Iterable[AgentTrace]) -> dict[str, object]:
    durations: dict[str, list[float]] = defaultdict(list)
    for trace in traces:
        active_kind: EventKind | None = None
        active_at: float | None = None
        for event in trace.events[1:]:
            if event.kind in WAIT_PAIRS:
                active_kind = event.kind
                active_at = event.timestamp
            elif active_kind is not None and (
                event.kind is WAIT_PAIRS[active_kind]
                or event.kind is EventKind.AGENT_FAILED
            ):
                assert active_at is not None
                wait_type = active_kind.value.removesuffix("_wait_started")
                duration = event.timestamp - active_at
                durations[wait_type].append(duration)
                durations["all"].append(duration)
                active_kind = None
                active_at = None

    return {
        wait_type: _distribution(durations.get(wait_type, []))
        for wait_type in ("all", "model", "tool", "human")
    }


def _simulation_summary(
    traces: tuple[AgentTrace, ...], policy: SimulationPolicy
) -> dict[str, object]:
    result = simulate(traces, policy)
    return {
        "gate": result["gate"],
        "metrics": result["metrics"],
        "policy": result["policy"],
    }


def analyze(
    traces: tuple[AgentTrace, ...],
    policy: SimulationPolicy,
    *,
    minimum_traces_per_class: int = 30,
    minimum_workload_classes: int = 2,
) -> dict[str, object]:
    if not traces:
        raise ValueError("at least one trace is required")
    if minimum_traces_per_class <= 0 or minimum_workload_classes <= 0:
        raise ValueError("collection minimums must be greater than zero")

    grouped: dict[str, list[AgentTrace]] = defaultdict(list)
    for trace in traces:
        grouped[trace.workload_class or "unclassified"].append(trace)

    class_reports: dict[str, object] = {}
    class_gates: list[bool] = []
    class_counts: list[int] = []
    for workload_class, class_trace_list in sorted(grouped.items()):
        class_traces = tuple(class_trace_list)
        simulation = _simulation_summary(class_traces, policy)
        gate = simulation["gate"]
        assert isinstance(gate, dict)
        class_gates.append(bool(gate["pass"]))
        class_counts.append(len(class_traces))
        class_reports[workload_class] = {
            "agents": len(class_traces),
            "simulation": simulation,
            "waits": _wait_distributions(class_traces),
        }

    enough_classes = len(grouped) >= minimum_workload_classes
    enough_traces = all(count >= minimum_traces_per_class for count in class_counts)
    no_unclassified = "unclassified" not in grouped
    collection_pass = enough_classes and enough_traces and no_unclassified
    hypothesis_pass = collection_pass and all(class_gates)

    return {
        "schema_version": 1,
        "evidence_level": "trace_simulation_only",
        "agents": len(traces),
        "workload_classes": class_reports,
        "overall": {
            "simulation": _simulation_summary(traces, policy),
            "waits": _wait_distributions(traces),
        },
        "collection_gate": {
            "minimum_traces_per_class": minimum_traces_per_class,
            "minimum_workload_classes": minimum_workload_classes,
            "workload_classes": len(grouped),
            "enough_traces_per_class": enough_traces,
            "enough_workload_classes": enough_classes,
            "no_unclassified_traces": no_unclassified,
            "pass": collection_pass,
        },
        "hypothesis_gate": {
            "all_workload_classes_pass": all(class_gates),
            "pass": hypothesis_pass,
        },
    }


def analyze_paths(
    inputs: Sequence[str | Path],
    policy: SimulationPolicy,
    *,
    minimum_traces_per_class: int = 30,
    minimum_workload_classes: int = 2,
) -> dict[str, object]:
    traces, files = load_traces(inputs)
    report = analyze(
        traces,
        policy,
        minimum_traces_per_class=minimum_traces_per_class,
        minimum_workload_classes=minimum_workload_classes,
    )
    report["trace_files"] = len(files)
    return report
