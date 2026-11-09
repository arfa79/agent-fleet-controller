from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .model import (
    AgentTrace,
    EventKind,
    Resources,
    TERMINAL_EVENTS,
    TraceEvent,
    WAIT_FINISHES,
    WAIT_PAIRS,
)


class TraceValidationError(ValueError):
    """Raised when a trace cannot be trusted by the simulator."""


WORKLOAD_CLASS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _finite_number(value: object, field: str, line_number: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TraceValidationError(f"line {line_number}: {field} must be a number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise TraceValidationError(f"line {line_number}: {field} must be finite")
    return parsed


def parse_jsonl(lines: Iterable[str]) -> tuple[AgentTrace, ...]:
    grouped: dict[str, list[TraceEvent]] = defaultdict(list)

    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            raw = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise TraceValidationError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(raw, dict):
            raise TraceValidationError(f"line {line_number}: event must be a JSON object")

        allowed = {
            "v",
            "ts",
            "agent_id",
            "kind",
            "cpu_cores",
            "memory_gib",
            "workload_class",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise TraceValidationError(
                f"line {line_number}: unknown fields: {', '.join(unknown)}"
            )

        version = raw.get("v")
        if version != 1:
            raise TraceValidationError(f"line {line_number}: only trace version 1 is supported")

        agent_id = raw.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise TraceValidationError(f"line {line_number}: agent_id must be non-empty")
        if len(agent_id) > 128:
            raise TraceValidationError(f"line {line_number}: agent_id is too long")

        timestamp = _finite_number(raw.get("ts"), "ts", line_number)
        if timestamp < 0:
            raise TraceValidationError(f"line {line_number}: ts must be non-negative")

        try:
            kind = EventKind(raw.get("kind"))
        except (TypeError, ValueError) as exc:
            raise TraceValidationError(f"line {line_number}: unknown event kind") from exc

        resources = None
        workload_class = None
        resource_fields_present = "cpu_cores" in raw or "memory_gib" in raw
        if kind is EventKind.AGENT_STARTED:
            if "cpu_cores" not in raw or "memory_gib" not in raw:
                raise TraceValidationError(
                    f"line {line_number}: agent_started requires cpu_cores and memory_gib"
                )
            cpu = _finite_number(raw["cpu_cores"], "cpu_cores", line_number)
            memory = _finite_number(raw["memory_gib"], "memory_gib", line_number)
            if cpu <= 0 or memory <= 0:
                raise TraceValidationError(
                    f"line {line_number}: resource values must be greater than zero"
                )
            resources = Resources(cpu_cores=cpu, memory_gib=memory)
            workload_class = raw.get("workload_class")
            if workload_class is not None and (
                not isinstance(workload_class, str)
                or WORKLOAD_CLASS_PATTERN.fullmatch(workload_class) is None
            ):
                raise TraceValidationError(
                    f"line {line_number}: workload_class must be a lowercase safe label"
                )
        elif resource_fields_present:
            raise TraceValidationError(
                f"line {line_number}: resources are only valid on agent_started"
            )
        elif "workload_class" in raw:
            raise TraceValidationError(
                f"line {line_number}: workload_class is only valid on agent_started"
            )

        grouped[agent_id].append(
            TraceEvent(
                version=version,
                timestamp=timestamp,
                agent_id=agent_id,
                kind=kind,
                resources=resources,
                workload_class=workload_class,
            )
        )

    if not grouped:
        raise TraceValidationError("trace contains no events")

    traces = [_validate_agent(agent_id, events) for agent_id, events in grouped.items()]
    return tuple(sorted(traces, key=lambda trace: trace.agent_id))


def _validate_agent(agent_id: str, events: list[TraceEvent]) -> AgentTrace:
    if events[0].kind is not EventKind.AGENT_STARTED:
        raise TraceValidationError(f"agent {agent_id}: first event must be agent_started")
    if events[-1].kind not in TERMINAL_EVENTS:
        raise TraceValidationError(f"agent {agent_id}: last event must be terminal")

    starts = sum(event.kind is EventKind.AGENT_STARTED for event in events)
    terminals = sum(event.kind in TERMINAL_EVENTS for event in events)
    if starts != 1 or terminals != 1:
        raise TraceValidationError(
            f"agent {agent_id}: expected exactly one start and one terminal event"
        )

    active_wait: EventKind | None = None
    previous_timestamp = -1.0
    for event in events:
        if event.timestamp < previous_timestamp:
            raise TraceValidationError(f"agent {agent_id}: timestamps must be monotonic")
        previous_timestamp = event.timestamp

        if event.kind in WAIT_PAIRS:
            if active_wait is not None:
                raise TraceValidationError(f"agent {agent_id}: nested waits are not supported")
            active_wait = event.kind
        elif event.kind in WAIT_FINISHES:
            expected = WAIT_FINISHES[event.kind]
            if active_wait is not expected:
                raise TraceValidationError(
                    f"agent {agent_id}: {event.kind.value} has no matching start"
                )
            active_wait = None
        elif event.kind is EventKind.AGENT_COMPLETED and active_wait is not None:
            raise TraceValidationError(
                f"agent {agent_id}: completed with an unclosed wait"
            )
        elif event.kind in TERMINAL_EVENTS:
            # A process may fail while blocked on an external dependency. The
            # terminal event implicitly closes that wait without fabricating a
            # successful wait-finished event.
            active_wait = None

    if active_wait is not None:
        raise TraceValidationError(f"agent {agent_id}: unclosed wait")

    resources = events[0].resources
    assert resources is not None
    return AgentTrace(
        agent_id=agent_id,
        resources=resources,
        workload_class=events[0].workload_class,
        events=tuple(events),
    )


def load_trace(path: str | Path) -> tuple[AgentTrace, ...]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return parse_jsonl(stream)
